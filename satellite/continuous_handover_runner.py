#!/usr/bin/env python3
# encoding: utf-8

"""Continuous runtime handover runner for correctness checks and live demos."""

from __future__ import annotations

import argparse
import ast
import json
import itertools
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from controller import SCENARIO_DEFINITIONS
from mpls_control import GATEWAY_DEFAULT_PING_TARGETS, gateway_nat_realization_for_gateway
from mpls_scenario_loop import observe_runtime_residue
from mpls_scenario_smoke import scenario_all_ut_plan_summaries
from mpls_ut_egress_smoke import controller_action, run_command, verify_forwarding_cleanup

DEFAULT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = DEFAULT_DIR / "configs"
DEFAULT_LINK_STATE = DEFAULT_CONFIG_DIR / "link_state.json"
DEFAULT_TOPOLOGY = DEFAULT_CONFIG_DIR / "topology.json"
DEFAULT_CONTROLLER = DEFAULT_DIR / "controller.py"
DEFAULT_SEQUENCE = "r0,r1,r2,r3"
DEFAULT_PING_TARGET = "auto"
DEFAULT_UPDATE_MODE = "all-attached"
DEMO_SHOWCASE_UT_IDS = ("ut3", "ut5")
TIMING_JSON_PREFIXES = (
    ("UT egress timing: ", "ut_egress"),
    ("Apply scenario timing: ", "apply_scenario"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a correctness-first continuous handover demo over the generalized "
            "constellation scenarios."
        )
    )
    parser.add_argument(
        "--controller",
        default=str(DEFAULT_CONTROLLER),
        help="Path to controller.py.",
    )
    parser.add_argument(
        "--link-state",
        default=str(DEFAULT_LINK_STATE),
        help="Path to link_state.json.",
    )
    parser.add_argument(
        "--topology-config",
        default=str(DEFAULT_TOPOLOGY),
        help="Path to topology.json.",
    )
    parser.add_argument(
        "--sequence",
        default=DEFAULT_SEQUENCE,
        help=f"Comma-separated scenario sequence to repeat. Default: {DEFAULT_SEQUENCE}.",
    )
    parser.add_argument(
        "--max-rounds",
        "--rounds",
        dest="max_rounds",
        type=int,
        default=0,
        help="Total scenario rounds to run. Use 0 for an indefinite loop.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=7.0,
        help="Seconds to wait between successful rounds. Default: 7 seconds.",
    )
    parser.add_argument(
        "--ping-target",
        default=DEFAULT_PING_TARGET,
        help=(
            "External host IP override for continuous UT ping observers. "
            f"Use '{DEFAULT_PING_TARGET}' to follow the selected gateway target."
        ),
    )
    parser.add_argument(
        "--update-mode",
        choices=("all-attached", "impacted-only"),
        default=DEFAULT_UPDATE_MODE,
        help=(
            "Per-round forwarding update scope. "
            "Use 'all-attached' for v1 or 'impacted-only' for conservative v2."
        ),
    )
    parser.add_argument(
        "--continuous-demo",
        "--demo-loop",
        dest="continuous_demo",
        action="store_true",
        help=(
            "Run a lighter indefinite demo loop for manual observation. "
            "This mode keeps impacted cleanup/install and transition summaries, "
            "but skips full per-round verification, residue sweep, ping watchers, "
            "and strong attach ping validation."
        ),
    )
    parser.add_argument(
        "--verbose-timing",
        action="store_true",
        help=(
            "Print deeper timing breakdowns, including apply_scenario command-family "
            "elapsed time and per-UT cleanup/install transport details."
        ),
    )
    return parser.parse_args()


def parse_sequence(sequence_text: str) -> List[str]:
    scenarios = [item.strip() for item in sequence_text.split(",") if item.strip()]
    if not scenarios:
        raise SystemExit("sequence must contain at least one scenario")
    for scenario_name in scenarios:
        if scenario_name not in SCENARIO_DEFINITIONS:
            supported = ", ".join(sorted(SCENARIO_DEFINITIONS))
            raise SystemExit(
                f"unsupported scenario: {scenario_name}. Supported scenarios: {supported}"
            )
    return scenarios


def scenario_observe_ut_ids(scenario_name: str) -> List[str]:
    return [
        str(ut_id)
        for ut_id in SCENARIO_DEFINITIONS[scenario_name].get("observe_ut_ids", [])
    ]


def union_observe_ut_ids(scenarios: Iterable[str]) -> List[str]:
    observed = {
        ut_id
        for scenario_name in scenarios
        for ut_id in scenario_observe_ut_ids(scenario_name)
    }
    return sorted(observed)


def summarize_plans(topology_path: Path, link_state_path: Path) -> List[Dict[str, object]]:
    return scenario_all_ut_plan_summaries(topology_path, link_state_path)


def _summary_signature(
    summary: Dict[str, object],
) -> Tuple[str, str, Tuple[str, ...], str, str, str]:
    return (
        str(summary["serving_satellite"]),
        str(summary["selected_gateway"]),
        tuple(str(node_id) for node_id in summary["forward_mpls_nodes"]),
        str(summary.get("ping_target", "")),
        str(summary.get("gateway_nat_interface", "")),
        str(summary.get("gateway_nat_source_ip", "")),
    )


def summary_by_ut(
    summaries: List[Dict[str, object]],
) -> Dict[str, Dict[str, object]]:
    return {str(entry["ut_id"]): entry for entry in summaries}


def changed_ut_ids(
    previous: List[Dict[str, object]],
    current: List[Dict[str, object]],
) -> Tuple[List[str], List[str]]:
    previous_by_ut = {str(entry["ut_id"]): entry for entry in previous}
    changed: List[str] = []
    unchanged: List[str] = []
    for current_entry in current:
        ut_id = str(current_entry["ut_id"])
        previous_entry = previous_by_ut.get(ut_id)
        if previous_entry is None or _summary_signature(previous_entry) != _summary_signature(current_entry):
            changed.append(ut_id)
        else:
            unchanged.append(ut_id)
    return changed, unchanged


def cleanup_verification_command_count(summary: Dict[str, object]) -> int:
    count = 8
    if len(summary.get("forward_mpls_nodes", [])) > 2:
        count += 1
    return count


def summary_hop_count(summary: Dict[str, object]) -> int:
    hop_count = summary.get("hop_count")
    if hop_count is not None:
        return int(hop_count)
    return max(0, len(summary.get("forward_mpls_nodes", [])) - 1)


def parse_summary_value(raw_value: str) -> Any:
    text = raw_value.strip()
    if not text:
        return text
    if text[0] in "{[":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return text
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def collect_execution_summaries(output: str) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for line in output.splitlines():
        if (
            line.startswith("UT egress execution summary:")
            or line.startswith("Apply scenario execution summary:")
        ):
            if current:
                summaries.append(current)
            current = {"header": line}
            continue
        if current is None:
            continue
        if not line.startswith("  ") or "=" not in line:
            continue
        key, value = line.strip().split("=", 1)
        current[key] = parse_summary_value(value)
    if current:
        summaries.append(current)
    return summaries


def collect_timing_json_records(output: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for line in output.splitlines():
        stripped = line.strip()
        for prefix, record_type in TIMING_JSON_PREFIXES:
            if not stripped.startswith(prefix):
                continue
            payload = stripped[len(prefix) :].strip()
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            records.append({"record_type": record_type, "data": data})
            break
    return records


def merge_counter_dicts(target: Dict[str, int], source: Dict[str, Any]) -> None:
    if not isinstance(source, dict):
        return
    for key, value in source.items():
        target[str(key)] = target.get(str(key), 0) + int(value)


def merge_float_dicts(target: Dict[str, float], source: Dict[str, Any]) -> None:
    if not isinstance(source, dict):
        return
    for key, value in source.items():
        target[str(key)] = round(target.get(str(key), 0.0) + float(value), 3)


def aggregate_execution_summaries(output: str) -> Dict[str, Any]:
    summaries = collect_execution_summaries(output)
    aggregate: Dict[str, Any] = {
        "summary_blocks": len(summaries),
        "total_commands": 0,
        "node_counts": {},
        "command_family_counts": {},
        "logical_category_counts": {},
        "logical_kind_counts": {},
    }
    for summary in summaries:
        aggregate["total_commands"] += int(summary.get("total_commands", 0))
        merge_counter_dicts(aggregate["node_counts"], summary.get("node_counts", {}))
        merge_counter_dicts(
            aggregate["command_family_counts"],
            summary.get("command_family_counts", {}),
        )
        merge_counter_dicts(
            aggregate["logical_category_counts"],
            summary.get("logical_category_counts", {}),
        )
        merge_counter_dicts(
            aggregate["logical_kind_counts"],
            summary.get("logical_kind_counts", {}),
        )
    return aggregate


def empty_execution_metric(*, args: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "elapsed_seconds": 0.0,
        "summary_blocks": 0,
        "total_commands": 0,
        "transport_command_count": 0,
        "node_counts": {},
        "command_family_counts": {},
        "logical_category_counts": {},
        "logical_kind_counts": {},
        "spec_command_family_counts": {},
        "command_family_elapsed_seconds": {},
        "logical_kind_elapsed_seconds": {},
        "node_elapsed_seconds": {},
        "category_elapsed_seconds": {},
        "phase_elapsed_seconds": {},
        "slot_mutation_counts": {},
        "slot_noop_counts": {},
        "in_memory_event_counts": {},
        "in_memory_event_elapsed_seconds": {},
        "timing_records": [],
        "per_ut_metrics": {},
        "args": list(args or []),
    }


def merge_execution_metric(
    aggregate: Dict[str, Any],
    source: Dict[str, Any],
) -> Dict[str, Any]:
    aggregate["elapsed_seconds"] += float(source.get("elapsed_seconds", 0.0))
    aggregate["summary_blocks"] += int(source.get("summary_blocks", 0))
    aggregate["total_commands"] += int(source.get("total_commands", 0))
    aggregate["transport_command_count"] += int(source.get("transport_command_count", 0))
    merge_counter_dicts(aggregate["node_counts"], source.get("node_counts", {}))
    merge_counter_dicts(
        aggregate["command_family_counts"],
        source.get("command_family_counts", {}),
    )
    merge_counter_dicts(
        aggregate["logical_category_counts"],
        source.get("logical_category_counts", {}),
    )
    merge_counter_dicts(
        aggregate["logical_kind_counts"],
        source.get("logical_kind_counts", {}),
    )
    merge_counter_dicts(
        aggregate["spec_command_family_counts"],
        source.get("spec_command_family_counts", {}),
    )
    merge_float_dicts(
        aggregate["command_family_elapsed_seconds"],
        source.get("command_family_elapsed_seconds", {}),
    )
    merge_float_dicts(
        aggregate["logical_kind_elapsed_seconds"],
        source.get("logical_kind_elapsed_seconds", {}),
    )
    merge_float_dicts(
        aggregate["node_elapsed_seconds"],
        source.get("node_elapsed_seconds", {}),
    )
    merge_float_dicts(
        aggregate["category_elapsed_seconds"],
        source.get("category_elapsed_seconds", {}),
    )
    merge_float_dicts(
        aggregate["phase_elapsed_seconds"],
        source.get("phase_elapsed_seconds", {}),
    )
    merge_counter_dicts(
        aggregate["slot_mutation_counts"],
        source.get("slot_mutation_counts", {}),
    )
    merge_counter_dicts(
        aggregate["slot_noop_counts"],
        source.get("slot_noop_counts", {}),
    )
    merge_counter_dicts(
        aggregate["in_memory_event_counts"],
        source.get("in_memory_event_counts", {}),
    )
    merge_float_dicts(
        aggregate["in_memory_event_elapsed_seconds"],
        source.get("in_memory_event_elapsed_seconds", {}),
    )
    aggregate.setdefault("timing_records", []).extend(source.get("timing_records", []))
    aggregate.setdefault("per_ut_metrics", {}).update(source.get("per_ut_metrics", {}))
    return aggregate


def measured_controller_action(
    controller_path: Path,
    link_state_path: Path,
    *args: str,
    include_timing: bool = False,
) -> Dict[str, Any]:
    started_at = time.monotonic()
    action_args = list(args)
    if include_timing and "--timing" not in action_args:
        action_args.append("--timing")
    result = controller_action(controller_path, link_state_path, *action_args)
    elapsed = time.monotonic() - started_at
    aggregate = empty_execution_metric(args=action_args)
    merge_execution_metric(aggregate, aggregate_execution_summaries(result.stdout))
    aggregate["elapsed_seconds"] = elapsed
    aggregate["args"] = list(action_args)
    timing_records = collect_timing_json_records(result.stdout)
    aggregate["timing_records"] = timing_records
    for record in timing_records:
        data = dict(record.get("data", {}))
        if record.get("record_type") == "ut_egress":
            aggregate["transport_command_count"] += int(
                data.get("transport_command_count", 0)
            )
            merge_counter_dicts(
                aggregate["spec_command_family_counts"],
                data.get("spec_command_family_counts", {}),
            )
            merge_float_dicts(
                aggregate["node_elapsed_seconds"],
                data.get("node_elapsed_seconds", {}),
            )
            merge_float_dicts(
                aggregate["category_elapsed_seconds"],
                data.get("category_elapsed_seconds", {}),
            )
            command_observation = dict(data.get("command_observation", {}))
            merge_float_dicts(
                aggregate["command_family_elapsed_seconds"],
                command_observation.get("command_family_elapsed_seconds", {}),
            )
            merge_float_dicts(
                aggregate["logical_kind_elapsed_seconds"],
                command_observation.get("logical_kind_elapsed_seconds", {}),
            )
        elif record.get("record_type") == "apply_scenario":
            merge_float_dicts(
                aggregate["phase_elapsed_seconds"],
                data.get("phase_elapsed_seconds", {}),
            )
            merge_counter_dicts(
                aggregate["slot_mutation_counts"],
                data.get("slot_mutation_counts", {}),
            )
            merge_counter_dicts(
                aggregate["slot_noop_counts"],
                data.get("slot_noop_counts", {}),
            )
            merge_float_dicts(
                aggregate["command_family_elapsed_seconds"],
                data.get("command_family_elapsed_seconds", {}),
            )
            merge_float_dicts(
                aggregate["logical_kind_elapsed_seconds"],
                data.get("logical_kind_elapsed_seconds", {}),
            )
            merge_float_dicts(
                aggregate["node_elapsed_seconds"],
                data.get("node_elapsed_seconds", {}),
            )
            merge_counter_dicts(
                aggregate["in_memory_event_counts"],
                data.get("in_memory_event_counts", {}),
            )
            merge_float_dicts(
                aggregate["in_memory_event_elapsed_seconds"],
                data.get("in_memory_event_elapsed_seconds", {}),
            )
    return aggregate


def measured_ut_egress_actions(
    *,
    controller_path: Path,
    link_state_path: Path,
    controller_flag: str,
    ut_ids: List[str],
    ping_target_override: Optional[str] = None,
    include_timing: bool = False,
) -> Dict[str, Any]:
    aggregate = empty_execution_metric(args=[controller_flag])
    aggregate["ut_ids"] = list(ut_ids)
    for ut_id in ut_ids:
        action_args = [controller_flag, ut_id]
        if controller_flag == "--verify-ut-egress-mpls" and ping_target_override:
            action_args.extend(["--ping-target", ping_target_override])
        metric = measured_controller_action(
            controller_path,
            link_state_path,
            *action_args,
            include_timing=include_timing,
        )
        ut_metric = {
            "ut_id": ut_id,
            "elapsed_seconds": round(float(metric.get("elapsed_seconds", 0.0)), 3),
            "transport_command_count": int(metric.get("transport_command_count", 0)),
            "spec_command_family_counts": dict(metric.get("spec_command_family_counts", {})),
            "command_family_elapsed_seconds": dict(metric.get("command_family_elapsed_seconds", {})),
            "node_elapsed_seconds": dict(metric.get("node_elapsed_seconds", {})),
            "category_elapsed_seconds": dict(metric.get("category_elapsed_seconds", {})),
        }
        aggregate.setdefault("per_ut_metrics", {})[ut_id] = ut_metric
        merge_execution_metric(
            aggregate,
            metric,
        )
    return aggregate


def resolve_summary_ping_target(
    summary: Dict[str, object],
    ping_target_override: Optional[str],
) -> str:
    if ping_target_override:
        return ping_target_override
    return GATEWAY_DEFAULT_PING_TARGETS.get(
        str(summary["selected_gateway"]),
        "10.160.0.71",
    )


def expected_scenario_summaries(
    scenario_name: str,
    ping_target_override: Optional[str],
) -> Optional[List[Dict[str, object]]]:
    expected_paths = dict(SCENARIO_DEFINITIONS[scenario_name].get("expected_ut_paths", {}))
    if not expected_paths:
        return None
    summaries: List[Dict[str, object]] = []
    for ut_id in sorted(expected_paths):
        expected = dict(expected_paths[ut_id])
        satellite_path = [str(node_id) for node_id in expected.get("satellite_path", [])]
        selected_gateway = str(expected["selected_gateway_id"])
        nat_realization = gateway_nat_realization_for_gateway(selected_gateway)
        ping_target = ping_target_override or GATEWAY_DEFAULT_PING_TARGETS.get(
            selected_gateway,
            "10.160.0.71",
        )
        summaries.append(
            {
                "ut_id": str(ut_id),
                "serving_satellite": str(expected["serving_satellite_id"]),
                "selected_gateway": selected_gateway,
                "satellite_path": satellite_path,
                "forward_mpls_nodes": [*satellite_path, selected_gateway],
                "hop_count": max(0, len(satellite_path)),
                "ping_target": ping_target,
                "gateway_nat_interface": str(nat_realization["nat_interface"]),
                "gateway_nat_source_ip": str(nat_realization["nat_source_ip"]),
            }
        )
    return summaries


def impacted_ut_ids_from_summaries(
    previous: List[Dict[str, object]],
    target: List[Dict[str, object]],
) -> Tuple[List[str], List[str]]:
    previous_by_ut = summary_by_ut(previous)
    impacted: List[str] = []
    unchanged: List[str] = []
    for target_entry in target:
        ut_id = str(target_entry["ut_id"])
        previous_entry = previous_by_ut.get(ut_id)
        if previous_entry is None or _summary_signature(previous_entry) != _summary_signature(target_entry):
            impacted.append(ut_id)
        else:
            unchanged.append(ut_id)
    return impacted, unchanged


def summary_runtime_resources(summary: Dict[str, object]) -> set[str]:
    resources: set[str] = set()
    resources.add("serving:" + str(summary.get("serving_satellite", "")))
    resources.add("gateway:" + str(summary.get("selected_gateway", "")))
    resources.add("ping_target:" + str(summary.get("ping_target", "")))
    resources.add("nat_if:" + str(summary.get("gateway_nat_interface", "")))
    resources.add("nat_src:" + str(summary.get("gateway_nat_source_ip", "")))
    for node_id in summary.get("forward_mpls_nodes", []):
        resources.add("forward_node:" + str(node_id))
    return resources


def expand_collateral_impacted_ut_ids(
    previous: List[Dict[str, object]],
    target: List[Dict[str, object]],
    directly_impacted_ut_ids: List[str],
    candidate_unchanged_ut_ids: List[str],
) -> Tuple[List[str], List[str], List[str]]:
    previous_by_ut = summary_by_ut(previous)
    target_by_ut = summary_by_ut(target)
    impacted = list(dict.fromkeys(str(ut_id) for ut_id in directly_impacted_ut_ids))
    unchanged = list(dict.fromkeys(str(ut_id) for ut_id in candidate_unchanged_ut_ids))
    collateral_impacted: List[str] = []

    changed = True
    while changed:
        changed = False
        impacted_resources: set[str] = set()
        for ut_id in impacted:
            previous_summary = previous_by_ut.get(ut_id)
            target_summary = target_by_ut.get(ut_id)
            if previous_summary is not None:
                impacted_resources.update(summary_runtime_resources(previous_summary))
            if target_summary is not None:
                impacted_resources.update(summary_runtime_resources(target_summary))
        next_unchanged: List[str] = []
        for ut_id in unchanged:
            previous_summary = previous_by_ut.get(ut_id)
            target_summary = target_by_ut.get(ut_id)
            candidate_resources: set[str] = set()
            if previous_summary is not None:
                candidate_resources.update(summary_runtime_resources(previous_summary))
            if target_summary is not None:
                candidate_resources.update(summary_runtime_resources(target_summary))
            if impacted_resources.intersection(candidate_resources):
                impacted.append(ut_id)
                collateral_impacted.append(ut_id)
                changed = True
                continue
            next_unchanged.append(ut_id)
        unchanged = next_unchanged
    return impacted, unchanged, list(dict.fromkeys(collateral_impacted))


def sum_ut_metric_elapsed(metric: Dict[str, Any], ut_ids: List[str]) -> float:
    total = 0.0
    per_ut_metrics = dict(metric.get("per_ut_metrics", {}))
    for ut_id in ut_ids:
        entry = per_ut_metrics.get(ut_id)
        if entry is None:
            continue
        total += float(entry.get("elapsed_seconds", 0.0))
    return round(total, 3)


def cleanup_previous_forwarding(
    *,
    controller_path: Path,
    topology_path: Path,
    link_state_path: Path,
    previous_summaries: List[Dict[str, object]],
    ut_ids: Optional[List[str]] = None,
    verify_cleanup: bool = True,
    include_timing: bool = False,
) -> Dict[str, Any]:
    selected_ut_ids = list(ut_ids or [str(summary["ut_id"]) for summary in previous_summaries])
    metric = empty_execution_metric(args=["cleanup-forwarding"])
    metric["cleanup_verify_ut_ids"] = list(selected_ut_ids) if verify_cleanup else []
    metric["cleanup_verify_commands"] = 0
    if previous_summaries:
        previous_by_ut = summary_by_ut(previous_summaries)
        if selected_ut_ids:
            merge_execution_metric(
                metric,
                measured_ut_egress_actions(
                    controller_path=controller_path,
                    link_state_path=link_state_path,
                    controller_flag="--cleanup-ut-egress-mpls",
                    ut_ids=selected_ut_ids,
                    include_timing=include_timing,
                ),
            )
        if verify_cleanup:
            for ut_id in selected_ut_ids:
                summary = previous_by_ut.get(ut_id)
                if summary is None:
                    continue
                verify_forwarding_cleanup(
                    topology_path,
                    link_state_path,
                    str(summary["ut_id"]),
                )
                metric["cleanup_verify_commands"] += cleanup_verification_command_count(summary)
        return metric
    run_command(
        [
            "python3",
            str(controller_path),
            "--link-state",
            str(link_state_path),
            "--cleanup-all-attached-ut-egress-mpls",
        ],
        failure_stage="best effort cleanup-all-attached on clean baseline",
        allow_failure=True,
    )
    return metric


def start_ping_watchers(ut_targets: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    watchers: Dict[str, Dict[str, str]] = {}
    for ut_id, ping_target in sorted(ut_targets.items()):
        pid_path = f"/tmp/skyseed-continuous-{ut_id}.pid"
        log_path = f"/tmp/skyseed-continuous-{ut_id}.log"
        result = run_command(
            [
                "docker",
                "exec",
                str(ut_id),
                "sh",
                "-lc",
                (
                    "mkdir -p /tmp; "
                    f"if [ -f {pid_path} ]; then "
                    f"kill $(cat {pid_path}) >/dev/null 2>&1 || true; "
                    f"rm -f {pid_path}; fi; "
                    f"nohup ping -O -D -i 0.5 {ping_target} > {log_path} 2>&1 < /dev/null & "
                    f"echo $! > {pid_path}; cat {pid_path}"
                ),
            ],
            failure_stage=f"start continuous ping watcher on {ut_id}",
        )
        watchers[str(ut_id)] = {
            "pid_path": pid_path,
            "pid": result.stdout.strip(),
            "log_path": log_path,
            "ping_target": ping_target,
        }
    return watchers


def stop_ping_watchers(watchers: Dict[str, Dict[str, str]]) -> None:
    for ut_id, watcher in watchers.items():
        run_command(
            [
                "docker",
                "exec",
                str(ut_id),
                "sh",
                "-lc",
                (
                    f"if [ -f {watcher['pid_path']} ]; then "
                    f"kill $(cat {watcher['pid_path']}) >/dev/null 2>&1 || true; "
                    f"rm -f {watcher['pid_path']}; fi"
                ),
            ],
            failure_stage=f"stop continuous ping watcher on {ut_id}",
            allow_failure=True,
        )


def reconcile_ping_watchers(
    watchers: Dict[str, Dict[str, str]],
    ut_targets: Dict[str, str],
) -> Dict[str, Dict[str, str]]:
    removed = {
        ut_id: watcher
        for ut_id, watcher in watchers.items()
        if ut_id not in ut_targets or watcher.get("ping_target") != ut_targets[ut_id]
    }
    if removed:
        stop_ping_watchers(removed)
    next_watchers = {
        ut_id: watcher
        for ut_id, watcher in watchers.items()
        if ut_id in ut_targets and watcher.get("ping_target") == ut_targets[ut_id]
    }
    missing_targets = {
        ut_id: ping_target
        for ut_id, ping_target in ut_targets.items()
        if ut_id not in next_watchers
    }
    if missing_targets:
        next_watchers.update(start_ping_watchers(missing_targets))
    return next_watchers


def path_transition_summary(
    previous: List[Dict[str, object]],
    current: List[Dict[str, object]],
) -> List[str]:
    previous_by_ut = {str(entry["ut_id"]): entry for entry in previous}
    current_by_ut = {str(entry["ut_id"]): entry for entry in current}
    changes: List[str] = []
    for ut_id in sorted(current_by_ut):
        current_summary = current_by_ut[ut_id]
        previous_summary = previous_by_ut.get(ut_id)
        if previous_summary is None:
            changes.append(
                f"{ut_id}: attach serving={current_summary['serving_satellite']} "
                f"gateway={current_summary['selected_gateway']} "
                f"hops={summary_hop_count(current_summary)} "
                f"path={' -> '.join(current_summary['forward_mpls_nodes'])}"
            )
            continue
        current_signature = _summary_signature(current_summary)
        previous_signature = _summary_signature(previous_summary)
        if current_signature == previous_signature:
            continue
        changes.append(
            f"{ut_id}: serving {previous_summary['serving_satellite']} -> {current_summary['serving_satellite']}; "
            f"gateway {previous_summary['selected_gateway']} -> {current_summary['selected_gateway']}; "
            f"hops {summary_hop_count(previous_summary)} -> {summary_hop_count(current_summary)}; "
            f"path {' -> '.join(previous_summary['forward_mpls_nodes'])} => "
            f"{' -> '.join(current_summary['forward_mpls_nodes'])}"
        )
    return changes


def render_round_summary(
    *,
    round_index: int,
    scenario_name: str,
    scenario_summaries: List[Dict[str, object]],
    previous_summaries: List[Dict[str, object]],
    residue_issues: List[str],
    phase_metrics: Dict[str, Dict[str, Any]],
    update_mode: str,
    planned_impacted_ut_ids: List[str],
    planned_unchanged_ut_ids: List[str],
    collateral_impacted_ut_ids: List[str],
    fallback_reason: Optional[str],
) -> None:
    definition = SCENARIO_DEFINITIONS[scenario_name]
    changed, unchanged = changed_ut_ids(previous_summaries, scenario_summaries)
    print(f"[round {round_index}] scenario: {scenario_name}")
    print(f"  transition_region: {definition.get('transition_region')}")
    print(f"  update_mode: {update_mode}")
    print("  observe_ut_ids: " + ",".join(scenario_observe_ut_ids(scenario_name)))
    print("  planned_impacted_ut_ids: " + (",".join(planned_impacted_ut_ids) if planned_impacted_ut_ids else "(none)"))
    print("  planned_unchanged_ut_ids: " + (",".join(planned_unchanged_ut_ids) if planned_unchanged_ut_ids else "(none)"))
    print(
        "  collateral_impacted_ut_ids: "
        + (",".join(collateral_impacted_ut_ids) if collateral_impacted_ut_ids else "(none)")
    )
    print("  changed_ut_ids: " + (",".join(changed) if changed else "(none)"))
    print("  unchanged_ut_ids: " + (",".join(unchanged) if unchanged else "(none)"))
    if fallback_reason:
        print("  fallback_reason: " + fallback_reason)
    for summary in scenario_summaries:
        print(f"  ut: {summary['ut_id']}")
        print(f"    serving_satellite: {summary['serving_satellite']}")
        print(f"    selected_gateway: {summary['selected_gateway']}")
        print("    forward_mpls_nodes: " + " -> ".join(summary["forward_mpls_nodes"]))
        print(f"    hop_count: {summary_hop_count(summary)}")
    changes = path_transition_summary(previous_summaries, scenario_summaries)
    print("  path_transition_summary:")
    if changes:
        for change in changes:
            print(f"    {change}")
    else:
        print("    no path change from previous round")
    print(
        "  residue_summary: "
        + ("PASS" if not residue_issues else "FAIL " + "; ".join(residue_issues))
    )
    print("  phase_timing_seconds:")
    for phase_name in (
        "cleanup_forwarding",
        "apply_scenario",
        "verify_scenario",
        "resolve_plan_summaries",
        "install_all_attached",
        "verify_all_attached",
        "verify_observe_ut",
        "reconcile_watchers",
        "residue_check",
    ):
        metric = phase_metrics.get(phase_name, {})
        print(f"    {phase_name}: {metric.get('elapsed_seconds', 0.0):.2f}")
    print("  phase_command_summary:")
    for phase_name in (
        "cleanup_forwarding",
        "apply_scenario",
        "install_all_attached",
        "verify_all_attached",
        "verify_observe_ut",
    ):
        metric = phase_metrics.get(phase_name, {})
        if not metric:
            continue
        print(
            f"    {phase_name}: total_commands={metric.get('total_commands', 0)} "
            f"summary_blocks={metric.get('summary_blocks', 0)}"
        )
        if metric.get("ut_ids"):
            print("      ut_ids=" + ",".join(metric.get("ut_ids", [])))
        if metric.get("cleanup_verify_ut_ids"):
            print(
                "      cleanup_verify_ut_ids="
                + ",".join(metric.get("cleanup_verify_ut_ids", []))
            )
        if metric.get("sampled_unchanged_verify_ut_ids"):
            print(
                "      sampled_unchanged_verify_ut_ids="
                + ",".join(metric.get("sampled_unchanged_verify_ut_ids", []))
            )
        if metric.get("command_family_counts"):
            print(
                "      command_family_counts="
                + str(metric.get("command_family_counts"))
            )


def render_demo_round_summary(
    *,
    round_index: int,
    scenario_name: str,
    scenario_summaries: List[Dict[str, object]],
    previous_summaries: List[Dict[str, object]],
    phase_metrics: Dict[str, Dict[str, Any]],
    update_mode: str,
    planned_impacted_ut_ids: List[str],
    planned_unchanged_ut_ids: List[str],
    collateral_impacted_ut_ids: List[str],
    elapsed_seconds: float,
    verbose_timing: bool,
) -> None:
    definition = SCENARIO_DEFINITIONS[scenario_name]
    current_by_ut = summary_by_ut(scenario_summaries)
    changed, unchanged = changed_ut_ids(previous_summaries, scenario_summaries)
    print(f"[demo round {round_index}] scenario: {scenario_name}")
    print(f"  transition_region: {definition.get('transition_region')}")
    print(f"  update_mode: {update_mode}")
    print("  showcase_ut_ids: " + ",".join(DEMO_SHOWCASE_UT_IDS))
    print("  planned_impacted_ut_ids: " + (",".join(planned_impacted_ut_ids) if planned_impacted_ut_ids else "(none)"))
    print("  planned_unchanged_ut_ids: " + (",".join(planned_unchanged_ut_ids) if planned_unchanged_ut_ids else "(none)"))
    print(
        "  collateral_impacted_ut_ids: "
        + (",".join(collateral_impacted_ut_ids) if collateral_impacted_ut_ids else "(none)")
    )
    print("  changed_ut_ids: " + (",".join(changed) if changed else "(none)"))
    print("  unchanged_ut_ids: " + (",".join(unchanged) if unchanged else "(none)"))
    print("  showcase_summary:")
    for ut_id in DEMO_SHOWCASE_UT_IDS:
        summary = current_by_ut.get(ut_id)
        if summary is None:
            continue
        print(
            f"    {ut_id}: serving={summary['serving_satellite']} "
            f"gateway={summary['selected_gateway']} "
            f"hops={summary_hop_count(summary)} "
            f"path={' -> '.join(summary['forward_mpls_nodes'])}"
        )
    changes = path_transition_summary(previous_summaries, scenario_summaries)
    print("  path_transition_summary:")
    if changes:
        for change in changes:
            print(f"    {change}")
    else:
        print("    no path change from previous round")
    summary_resolve_elapsed = float(
        phase_metrics.get("resolve_plan_summaries", {}).get("elapsed_seconds", 0.0)
    ) + float(phase_metrics.get("summary_printing", {}).get("elapsed_seconds", 0.0))
    print("  timing_summary:")
    print(
        f"    cleanup_impacted: "
        f"{phase_metrics.get('cleanup_forwarding', {}).get('elapsed_seconds', 0.0):.2f}"
    )
    print(
        f"    apply_scenario: "
        f"{phase_metrics.get('apply_scenario', {}).get('elapsed_seconds', 0.0):.2f}"
    )
    print(
        f"    install_impacted: "
        f"{phase_metrics.get('install_all_attached', {}).get('elapsed_seconds', 0.0):.2f}"
    )
    print(f"    summary_resolve: {summary_resolve_elapsed:.2f}")
    print(f"    total: {elapsed_seconds:.2f}")
    print("  phase_command_summary:")
    for phase_name in ("cleanup_forwarding", "apply_scenario", "install_all_attached"):
        metric = phase_metrics.get(phase_name, {})
        if not metric:
            continue
        print(
            f"    {phase_name}: total_commands={metric.get('total_commands', 0)} "
            f"summary_blocks={metric.get('summary_blocks', 0)}"
        )
        if metric.get("ut_ids"):
            print("      ut_ids=" + ",".join(metric.get("ut_ids", [])))
        if metric.get("cleanup_verify_ut_ids"):
            print(
                "      cleanup_verify_ut_ids="
                + ",".join(metric.get("cleanup_verify_ut_ids", []))
            )
        if metric.get("command_family_counts"):
            print(
                "      command_family_counts="
                + str(metric.get("command_family_counts"))
            )
    if verbose_timing:
        apply_metric = phase_metrics.get("apply_scenario", {})
        print("  verbose_timing:")
        print(
            "    apply_phase_elapsed_seconds="
            + str(apply_metric.get("phase_elapsed_seconds", {}))
        )
        print(
            "    apply_command_family_elapsed_seconds="
            + str(apply_metric.get("command_family_elapsed_seconds", {}))
        )
        print(
            "    apply_slot_mutation_counts="
            + str(apply_metric.get("slot_mutation_counts", {}))
        )
        print(
            "    apply_slot_noop_counts="
            + str(apply_metric.get("slot_noop_counts", {}))
        )
        print(
            "    apply_in_memory_event_elapsed_seconds="
            + str(apply_metric.get("in_memory_event_elapsed_seconds", {}))
        )
        cleanup_metric = phase_metrics.get("cleanup_forwarding", {})
        install_metric = phase_metrics.get("install_all_attached", {})
        print(
            "    cleanup_direct_elapsed_seconds="
            + str(cleanup_metric.get("direct_elapsed_seconds", 0.0))
        )
        print(
            "    cleanup_collateral_elapsed_seconds="
            + str(cleanup_metric.get("collateral_elapsed_seconds", 0.0))
        )
        print(
            "    cleanup_spec_command_family_counts="
            + str(cleanup_metric.get("spec_command_family_counts", {}))
        )
        print(
            "    cleanup_skipped_noop_ut_ids="
            + str(cleanup_metric.get("skipped_noop_ut_ids", []))
        )
        print(
            "    cleanup_per_ut_elapsed_seconds="
            + str(
                {
                    ut_id: entry.get("elapsed_seconds", 0.0)
                    for ut_id, entry in sorted(
                        dict(cleanup_metric.get("per_ut_metrics", {})).items()
                    )
                }
            )
        )
        print(
            "    install_direct_elapsed_seconds="
            + str(install_metric.get("direct_elapsed_seconds", 0.0))
        )
        print(
            "    install_collateral_elapsed_seconds="
            + str(install_metric.get("collateral_elapsed_seconds", 0.0))
        )
        print(
            "    install_spec_command_family_counts="
            + str(install_metric.get("spec_command_family_counts", {}))
        )
        print(
            "    install_skipped_noop_ut_ids="
            + str(install_metric.get("skipped_noop_ut_ids", []))
        )
        print(
            "    install_per_ut_elapsed_seconds="
            + str(
                {
                    ut_id: entry.get("elapsed_seconds", 0.0)
                    for ut_id, entry in sorted(
                        dict(install_metric.get("per_ut_metrics", {})).items()
                    )
                }
            )
        )


def pick_sample_unchanged_ut_ids(
    unchanged_ut_ids: List[str],
    observe_set: set[str],
    *,
    limit: int = 1,
) -> List[str]:
    preferred = [ut_id for ut_id in unchanged_ut_ids if ut_id in observe_set]
    selected = preferred[:limit]
    if len(selected) >= limit:
        return selected
    for ut_id in unchanged_ut_ids:
        if ut_id in selected:
            continue
        selected.append(ut_id)
        if len(selected) >= limit:
            break
    return selected


def main() -> int:
    args = parse_args()
    controller_path = Path(args.controller).resolve()
    link_state_path = Path(args.link_state).resolve()
    topology_path = Path(args.topology_config).resolve()
    scenarios = parse_sequence(str(args.sequence))
    rounds_limit: Optional[int] = None if args.max_rounds <= 0 else args.max_rounds
    is_demo_mode = bool(args.continuous_demo)
    observe_ut_ids = union_observe_ut_ids(scenarios)
    observe_set = set(observe_ut_ids)

    if is_demo_mode:
        print("[continuous-handover] starting continuous demo loop")
    else:
        print("[continuous-handover] starting correctness-first continuous runner")
    print("  sequence: " + ",".join(scenarios))
    if not is_demo_mode:
        print("  observe_ut_ids: " + ",".join(observe_ut_ids))
    else:
        print("  showcase_ut_ids: " + ",".join(DEMO_SHOWCASE_UT_IDS))
    print("  ping_target: " + str(args.ping_target))
    print(
        "  update_mode: "
        + ("impacted-only (demo default)" if is_demo_mode else str(args.update_mode))
    )
    print("  verbose_timing: " + ("yes" if args.verbose_timing else "no"))
    print("  interval_seconds: " + str(args.interval))
    print("  max_rounds: " + ("infinite" if rounds_limit is None else str(rounds_limit)))
    if is_demo_mode:
        print("  demo_behavior: impacted cleanup/install + transition summary + sleep")
        print(
            "  demo_validation: no verify_all_attached, no residue sweep, "
            "no runner ping, fast_runtime_apply=yes"
        )
        print("  observe_suggestions:")
        print("    docker exec ut3 ping -I ua-u3s0 10.150.0.71")
        print("    docker exec ut3 ping -I ua-u3s0 10.160.0.71")
        print("    docker exec ut5 ping -I ua-u5s0 10.160.0.71")
        print("    tcpdump / LFIB / route state / SEED map ICMP or MPLS path")

    controller_action(
        controller_path,
        link_state_path,
        "--prepare-all-ut-egress-policies",
    )

    watchers: Dict[str, Dict[str, str]] = {}
    previous_summaries: List[Dict[str, object]] = []
    round_index = 0
    ping_target_override = (
        None if str(args.ping_target).strip().lower() == DEFAULT_PING_TARGET else str(args.ping_target)
    )
    try:
        for scenario_name in itertools.cycle(scenarios):
            if rounds_limit is not None and round_index >= rounds_limit:
                break
            round_index += 1
            print(f"[continuous-handover] applying scenario {scenario_name} on round {round_index}")
            started_at = time.monotonic()
            phase_metrics: Dict[str, Dict[str, Any]] = {}
            effective_update_mode = "impacted-only" if is_demo_mode else str(args.update_mode)
            target_summaries = expected_scenario_summaries(
                scenario_name,
                ping_target_override,
            )
            planned_impacted_ut_ids: List[str] = []
            planned_unchanged_ut_ids: List[str] = []
            collateral_impacted_ut_ids: List[str] = []
            direct_impacted_ut_ids: List[str] = []
            fallback_reason: Optional[str] = None
            if effective_update_mode == "impacted-only":
                if target_summaries is None:
                    effective_update_mode = "all-attached"
                    print(
                        "[continuous-handover] impacted-only fallback: "
                        f"missing expected_ut_paths for {scenario_name}"
                    )
                else:
                    direct_impacted_ut_ids, direct_unchanged_ut_ids = impacted_ut_ids_from_summaries(
                        previous_summaries,
                        target_summaries,
                    )
                    (
                        planned_impacted_ut_ids,
                        planned_unchanged_ut_ids,
                        collateral_impacted_ut_ids,
                    ) = expand_collateral_impacted_ut_ids(
                        previous_summaries,
                        target_summaries,
                        direct_impacted_ut_ids,
                        direct_unchanged_ut_ids,
                    )
            if effective_update_mode == "all-attached":
                if previous_summaries:
                    planned_impacted_ut_ids = [
                        str(summary["ut_id"]) for summary in previous_summaries
                    ]
                elif target_summaries is not None:
                    planned_impacted_ut_ids = [
                        str(summary["ut_id"]) for summary in target_summaries
                    ]
                planned_unchanged_ut_ids = []

            cleanup_started_at = time.monotonic()
            phase_metrics["cleanup_forwarding"] = cleanup_previous_forwarding(
                controller_path=controller_path,
                topology_path=topology_path,
                link_state_path=link_state_path,
                previous_summaries=previous_summaries,
                ut_ids=(
                    None if effective_update_mode == "all-attached" else planned_impacted_ut_ids
                ),
                verify_cleanup=not is_demo_mode,
                include_timing=args.verbose_timing,
            )
            phase_metrics["cleanup_forwarding"]["direct_impacted_ut_ids"] = list(
                direct_impacted_ut_ids
            )
            phase_metrics["cleanup_forwarding"]["collateral_impacted_ut_ids"] = list(
                collateral_impacted_ut_ids
            )
            previous_ut_id_set = {
                str(summary["ut_id"]) for summary in previous_summaries
            }
            phase_metrics["cleanup_forwarding"]["skipped_noop_ut_ids"] = [
                ut_id for ut_id in planned_impacted_ut_ids if ut_id not in previous_ut_id_set
            ]
            phase_metrics["cleanup_forwarding"]["elapsed_seconds"] = (
                time.monotonic() - cleanup_started_at
            )
            phase_metrics["cleanup_forwarding"]["direct_elapsed_seconds"] = sum_ut_metric_elapsed(
                phase_metrics["cleanup_forwarding"],
                direct_impacted_ut_ids,
            )
            phase_metrics["cleanup_forwarding"]["collateral_elapsed_seconds"] = sum_ut_metric_elapsed(
                phase_metrics["cleanup_forwarding"],
                collateral_impacted_ut_ids,
            )
            phase_metrics["apply_scenario"] = measured_controller_action(
                controller_path,
                link_state_path,
                *(["--fast-runtime-apply"] if is_demo_mode else []),
                "--apply-scenario",
                scenario_name,
                include_timing=args.verbose_timing,
            )
            if is_demo_mode:
                phase_metrics["verify_scenario"] = {"elapsed_seconds": 0.0}
            else:
                phase_metrics["verify_scenario"] = measured_controller_action(
                    controller_path,
                    link_state_path,
                    "--verify-scenario",
                    scenario_name,
                    include_timing=args.verbose_timing,
                )
            summarize_started_at = time.monotonic()
            current_summaries = summarize_plans(topology_path, link_state_path)
            phase_metrics["resolve_plan_summaries"] = {
                "elapsed_seconds": time.monotonic() - summarize_started_at,
            }
            actual_changed_ut_ids, actual_unchanged_ut_ids = changed_ut_ids(
                previous_summaries,
                current_summaries,
            )
            if is_demo_mode:
                impacted_current_ut_ids: List[str] = []
                if effective_update_mode == "all-attached":
                    phase_metrics["install_all_attached"] = measured_controller_action(
                        controller_path,
                        link_state_path,
                        "--install-all-attached-ut-egress-mpls",
                        include_timing=args.verbose_timing,
                    )
                else:
                    impacted_current_ut_ids = [
                        ut_id for ut_id in planned_impacted_ut_ids
                        if ut_id in summary_by_ut(current_summaries)
                    ]
                    phase_metrics["install_all_attached"] = measured_ut_egress_actions(
                        controller_path=controller_path,
                        link_state_path=link_state_path,
                        controller_flag="--install-ut-egress-mpls",
                        ut_ids=impacted_current_ut_ids,
                        include_timing=args.verbose_timing,
                    )
                phase_metrics["install_all_attached"]["direct_impacted_ut_ids"] = list(
                    direct_impacted_ut_ids
                )
                phase_metrics["install_all_attached"]["collateral_impacted_ut_ids"] = list(
                    collateral_impacted_ut_ids
                )
                phase_metrics["install_all_attached"]["skipped_noop_ut_ids"] = [
                    ut_id for ut_id in planned_impacted_ut_ids if ut_id not in impacted_current_ut_ids
                ]
                phase_metrics["verify_all_attached"] = {
                    "elapsed_seconds": 0.0,
                    "verify_ut_ids": [],
                    "total_commands": 0,
                    "summary_blocks": 0,
                }
                phase_metrics["verify_observe_ut"] = {
                    "elapsed_seconds": 0.0,
                    "verify_ut_ids": [],
                    "total_commands": 0,
                    "summary_blocks": 0,
                }
                phase_metrics["reconcile_watchers"] = {"elapsed_seconds": 0.0}
                phase_metrics["residue_check"] = {"elapsed_seconds": 0.0}
                cleanup_metric = phase_metrics.get("cleanup_forwarding", {})
                install_metric = phase_metrics.get("install_all_attached", {})
                cleanup_metric["direct_elapsed_seconds"] = sum_ut_metric_elapsed(
                    cleanup_metric,
                    direct_impacted_ut_ids,
                )
                cleanup_metric["collateral_elapsed_seconds"] = sum_ut_metric_elapsed(
                    cleanup_metric,
                    collateral_impacted_ut_ids,
                )
                install_metric["direct_elapsed_seconds"] = sum_ut_metric_elapsed(
                    install_metric,
                    direct_impacted_ut_ids,
                )
                install_metric["collateral_elapsed_seconds"] = sum_ut_metric_elapsed(
                    install_metric,
                    collateral_impacted_ut_ids,
                )
                elapsed = time.monotonic() - started_at
                render_started_at = time.monotonic()
                render_demo_round_summary(
                    round_index=round_index,
                    scenario_name=scenario_name,
                    scenario_summaries=current_summaries,
                    previous_summaries=previous_summaries,
                    phase_metrics=phase_metrics,
                    update_mode=effective_update_mode,
                    planned_impacted_ut_ids=planned_impacted_ut_ids,
                    planned_unchanged_ut_ids=planned_unchanged_ut_ids,
                    collateral_impacted_ut_ids=collateral_impacted_ut_ids,
                    elapsed_seconds=elapsed,
                    verbose_timing=args.verbose_timing,
                )
                phase_metrics["summary_printing"] = {
                    "elapsed_seconds": time.monotonic() - render_started_at,
                }
                previous_summaries = current_summaries
                if args.interval > 0 and (
                    rounds_limit is None or round_index < rounds_limit
                ):
                    print(
                        f"[continuous-handover] sleeping {args.interval:.2f}s before next round"
                    )
                    time.sleep(args.interval)
                continue
            if effective_update_mode == "all-attached":
                phase_metrics["install_all_attached"] = measured_controller_action(
                    controller_path,
                    link_state_path,
                    "--install-all-attached-ut-egress-mpls",
                    include_timing=args.verbose_timing,
                )
                verify_all_args = ["--verify-all-attached-ut-egress-mpls"]
                if ping_target_override:
                    verify_all_args.extend(["--ping-target", ping_target_override])
                phase_metrics["verify_all_attached"] = measured_controller_action(
                    controller_path,
                    link_state_path,
                    *verify_all_args,
                    include_timing=args.verbose_timing,
                )
            else:
                impacted_current_ut_ids = [
                    ut_id for ut_id in planned_impacted_ut_ids
                    if ut_id in summary_by_ut(current_summaries)
                ]
                sampled_unchanged_ut_ids = pick_sample_unchanged_ut_ids(
                    actual_unchanged_ut_ids,
                    observe_set,
                )
                try:
                    phase_metrics["install_all_attached"] = measured_ut_egress_actions(
                        controller_path=controller_path,
                        link_state_path=link_state_path,
                        controller_flag="--install-ut-egress-mpls",
                        ut_ids=impacted_current_ut_ids,
                        include_timing=args.verbose_timing,
                    )
                    phase_metrics["install_all_attached"]["direct_impacted_ut_ids"] = list(
                        direct_impacted_ut_ids
                    )
                    phase_metrics["install_all_attached"]["collateral_impacted_ut_ids"] = list(
                        collateral_impacted_ut_ids
                    )
                    phase_metrics["install_all_attached"]["skipped_noop_ut_ids"] = [
                        ut_id for ut_id in planned_impacted_ut_ids if ut_id not in impacted_current_ut_ids
                    ]
                    phase_metrics["verify_all_attached"] = measured_ut_egress_actions(
                        controller_path=controller_path,
                        link_state_path=link_state_path,
                        controller_flag="--verify-ut-egress-mpls",
                        ut_ids=impacted_current_ut_ids,
                        ping_target_override=ping_target_override,
                        include_timing=args.verbose_timing,
                    )
                    phase_metrics["verify_observe_ut"] = measured_ut_egress_actions(
                        controller_path=controller_path,
                        link_state_path=link_state_path,
                        controller_flag="--verify-ut-egress-mpls",
                        ut_ids=sampled_unchanged_ut_ids,
                        ping_target_override=ping_target_override,
                        include_timing=args.verbose_timing,
                    )
                    phase_metrics["verify_observe_ut"][
                        "sampled_unchanged_verify_ut_ids"
                    ] = list(sampled_unchanged_ut_ids)
                except RuntimeError as exc:
                    fallback_reason = str(exc)
                    effective_update_mode = "all-attached-fallback"
                    print(
                        "[continuous-handover] impacted-only fallback on "
                        f"{scenario_name} round {round_index}: {fallback_reason}"
                    )
                    phase_metrics["install_all_attached"] = measured_controller_action(
                        controller_path,
                        link_state_path,
                        "--install-all-attached-ut-egress-mpls",
                        include_timing=args.verbose_timing,
                    )
                    verify_all_args = ["--verify-all-attached-ut-egress-mpls"]
                    if ping_target_override:
                        verify_all_args.extend(["--ping-target", ping_target_override])
                    phase_metrics["verify_all_attached"] = measured_controller_action(
                        controller_path,
                        link_state_path,
                        *verify_all_args,
                        include_timing=args.verbose_timing,
                    )
                    phase_metrics["verify_observe_ut"] = {
                        "elapsed_seconds": 0.0,
                        "verify_ut_ids": [],
                        "total_commands": 0,
                        "summary_blocks": 0,
                    }
            if effective_update_mode == "all-attached":
                phase_metrics["verify_observe_ut"] = {
                    "elapsed_seconds": 0.0,
                    "verify_ut_ids": [],
                    "total_commands": 0,
                    "summary_blocks": 0,
                }
            summary_by_ut_map = summary_by_ut(current_summaries)

            watcher_started_at = time.monotonic()
            watcher_targets = {
                ut_id: resolve_summary_ping_target(summary, ping_target_override)
                for ut_id, summary in summary_by_ut_map.items()
                if ut_id in observe_set
            }
            watchers = reconcile_ping_watchers(watchers, watcher_targets)
            phase_metrics["reconcile_watchers"] = {
                "elapsed_seconds": time.monotonic() - watcher_started_at,
            }
            if round_index == 1:
                print("[continuous-handover] started background ping watchers")
                for ut_id, watcher in watchers.items():
                    print(
                        f"  watcher {ut_id}: target={watcher['ping_target']} "
                        f"log={watcher['log_path']} pid={watcher['pid']}"
                    )

            residue_started_at = time.monotonic()
            residue_issues = observe_runtime_residue(
                link_state_path=link_state_path,
                scenario_name=scenario_name,
            )
            phase_metrics["residue_check"] = {
                "elapsed_seconds": time.monotonic() - residue_started_at,
            }
            render_round_summary(
                round_index=round_index,
                scenario_name=scenario_name,
                scenario_summaries=current_summaries,
                previous_summaries=previous_summaries,
                residue_issues=residue_issues,
                phase_metrics=phase_metrics,
                update_mode=effective_update_mode,
                planned_impacted_ut_ids=planned_impacted_ut_ids,
                planned_unchanged_ut_ids=planned_unchanged_ut_ids,
                collateral_impacted_ut_ids=collateral_impacted_ut_ids,
                fallback_reason=fallback_reason,
            )
            if residue_issues:
                raise RuntimeError(
                    "runtime residue observation failed: " + ", ".join(residue_issues)
                )
            previous_summaries = current_summaries
            elapsed = time.monotonic() - started_at
            print(f"  elapsed_seconds: {elapsed:.2f}")
            if args.interval > 0 and (
                rounds_limit is None or round_index < rounds_limit
            ):
                print(
                    f"[continuous-handover] sleeping {args.interval:.2f}s before next round"
                )
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("[continuous-handover] interrupted by user; stopping watchers and cleaning forwarding")
        stop_ping_watchers(watchers)
        run_command(
            [
                "python3",
                str(controller_path),
                "--link-state",
                str(link_state_path),
                "--cleanup-all-attached-ut-egress-mpls",
            ],
            failure_stage="best effort cleanup-all-attached on interrupt",
            allow_failure=True,
        )
        return 130
    except Exception:
        stop_ping_watchers(watchers)
        run_command(
            [
                "python3",
                str(controller_path),
                "--link-state",
                str(link_state_path),
                "--cleanup-all-attached-ut-egress-mpls",
            ],
            failure_stage="best effort cleanup-all-attached after failure",
            allow_failure=True,
        )
        raise

    stop_ping_watchers(watchers)
    print(f"[continuous-handover] completed after {round_index} successful rounds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
