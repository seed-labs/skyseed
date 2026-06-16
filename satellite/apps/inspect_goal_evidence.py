#!/usr/bin/env python3
# encoding: utf-8

"""Render the current evidence for the 1-shell G/Z runtime goal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()

from inspect_goal_readiness import (  # noqa: E402
    DEFAULT_BASELINE_REAL_RUN,
    DEFAULT_FAR_RUN,
    DEFAULT_MIXED_RUN,
    DEFAULT_NEAR_RUN,
    DEFAULT_RETURN_AUDIT_RUN,
    summarize_goal_readiness,
)
from inspect_starlink_distance_matrix import summarize_distance_matrix_run  # noqa: E402
from return_path_install_audit import summarize_return_path_migration_worklist  # noqa: E402
from temporal_run_inspector import inspect_temporal_run  # noqa: E402


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.expanduser().read_text(encoding="utf-8"))


def _return_path_audit_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.is_dir():
        return resolved / "return_path_audit.json"
    return resolved


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def _distance_profile_name(index: int) -> str:
    return ["near", "mixed", "far"][index] if index < 3 else f"varied-{index}"


def _distance_evidence(
    *,
    near_run: Path,
    varied_runs: Sequence[Path],
) -> Dict[str, Any]:
    runs = [
        {
            "profile": "near",
            **summarize_distance_matrix_run(near_run),
        }
    ]
    for index, run_path in enumerate(varied_runs, start=1):
        runs.append(
            {
                "profile": _distance_profile_name(index),
                **summarize_distance_matrix_run(run_path),
            }
        )
    near = runs[0]
    varied = runs[1:]
    near_one_hop = (
        int(near.get("isl_hop_count_max", 0) or 0) == 0
        and float(near.get("isl_hop_count_avg", 0.0) or 0.0) == 0.0
    )
    varied_multihop_count = sum(
        1
        for run in varied
        if int(run.get("isl_hop_count_max", 0) or 0) > 0
        and float(run.get("isl_hop_count_avg", 0.0) or 0.0) > 0.0
    )
    return {
        "near_one_hop": near_one_hop,
        "varied_multihop_run_count": varied_multihop_count,
        "one_hop_is_geography_specific": near_one_hop and varied_multihop_count > 0,
        "runs": runs,
    }


def _selected_scope(worklist: Dict[str, Any]) -> Dict[str, Any]:
    scopes = [
        dict(scope)
        for scope in list(worklist.get("scopes", []))
        if isinstance(scope, dict)
    ]
    for scope in scopes:
        if str(scope.get("scope_name")) == "selected_runtime":
            return scope
    return scopes[0] if scopes else {}


def _return_path_evidence(
    return_audit_run: Path,
    readiness_return_path: Dict[str, Any],
) -> Dict[str, Any]:
    audit = _load_json(_return_path_audit_path(return_audit_run))
    worklist = summarize_return_path_migration_worklist(audit)
    scope = _selected_scope(worklist)
    present_items = [
        dict(item)
        for item in list(scope.get("work_items", []))
        if isinstance(item, dict) and bool(item.get("present_in_return_path_audit"))
    ]
    backbone_items = [
        item
        for item in present_items
        if str(item.get("migration_category")) == "backbone_return"
    ]
    return {
        "current_linux_realization": worklist.get("current_linux_realization"),
        "target_backbone_realization": worklist.get("target_backbone_realization"),
        "source_file": worklist.get("source_file"),
        "selected_scope_name": scope.get("scope_name"),
        "selected_total_flow_count": scope.get("total_flow_count"),
        "selected_current_backbone_items": scope.get(
            "total_current_backbone_return_state_item_count"
        ),
        "selected_target_backbone_items": scope.get(
            "total_target_shared_backbone_return_state_item_count"
        ),
        "selected_current_to_target_ratio": scope.get(
            "current_to_target_backbone_state_ratio"
        ),
        "top_backbone_work_item": scope.get("top_backbone_work_item"),
        "selected_candidate_shared_zone_route_count": readiness_return_path.get(
            "selected_runtime_candidate_shared_zone_route_count"
        ),
        "selected_candidate_zone_route_coverage_ratio": readiness_return_path.get(
            "selected_runtime_candidate_zone_route_coverage_ratio"
        ),
        "selected_candidate_zone_route_complete": readiness_return_path.get(
            "selected_runtime_candidate_zone_route_complete"
        ),
        "present_work_items": present_items,
        "present_backbone_work_items": backbone_items,
        "backbone_return_still_per_ut_or_per_flow": any(backbone_items),
    }


def _directional_isolation_evidence(
    *,
    return_audit_run: Path,
    return_path: Dict[str, Any],
) -> Dict[str, Any]:
    audit = _load_json(_return_path_audit_path(return_audit_run))
    selected_ids = {
        str(scope_id)
        for scope_id in list(audit.get("selected_scope_ids", []))
        if str(scope_id)
    }
    snapshot_reports = [
        dict(snapshot)
        for snapshot in list(audit.get("snapshot_reports", []))
        if isinstance(snapshot, dict)
    ]
    selected_snapshots = [
        snapshot
        for snapshot in snapshot_reports
        if not selected_ids or str(snapshot.get("scope_id")) in selected_ids
    ]
    if not selected_snapshots:
        selected_snapshots = snapshot_reports

    flow_count = sum(
        int(snapshot.get("flow_count", 0) or 0) for snapshot in selected_snapshots
    )
    gateway_class_count = sum(
        int(
            dict(snapshot.get("shared_return_targets", {})).get(
                "gateway_class_count",
                0,
            )
            or 0
        )
        for snapshot in selected_snapshots
    )
    max_gateway_class_count = max(
        (
            int(
                dict(snapshot.get("shared_return_targets", {})).get(
                    "gateway_class_count",
                    0,
                )
                or 0
            )
            for snapshot in selected_snapshots
        ),
        default=0,
    )
    current_backbone_items = int(
        return_path.get("selected_current_backbone_items", 0) or 0
    )
    target_backbone_items = int(
        return_path.get("selected_target_backbone_items", 0) or 0
    )
    selected_projection = dict(
        dict(dict(audit.get("selected_runtime_scope", {})).get("scale_projection", {}))
        .get("projections", {})
        .get("10000", {})
    )
    return {
        "selected_snapshot_count": len(selected_snapshots),
        "selected_flow_count": flow_count,
        "forward_direction_model": "ut_edge_gateway_class_selection",
        "forward_gateway_class_target_count": gateway_class_count,
        "max_gateway_class_target_count_per_snapshot": max_gateway_class_count,
        "forward_flow_to_gateway_class_ratio": _ratio(flow_count, gateway_class_count),
        "forward_state_bounded_by_gateway_classes": (
            flow_count > 0 and 0 < gateway_class_count <= flow_count
        ),
        "return_direction_current_model": return_path.get("current_linux_realization"),
        "return_direction_target_model": return_path.get("target_backbone_realization"),
        "return_current_backbone_items": current_backbone_items,
        "return_target_backbone_items": target_backbone_items,
        "return_current_backbone_items_per_flow": _ratio(
            current_backbone_items,
            flow_count,
        ),
        "return_target_backbone_items_per_flow": _ratio(
            target_backbone_items,
            flow_count,
        ),
        "return_state_scales_with_flow_count": bool(
            return_path.get("backbone_return_still_per_ut_or_per_flow")
        ),
        "projection_10000_current_backbone_items": selected_projection.get(
            "current_per_flow_compatibility_backbone_items"
        ),
        "projection_10000_target_constant_backbone_items": selected_projection.get(
            "target_shared_backbone_items_if_target_count_constant"
        ),
        "projection_10000_ratio_constant": selected_projection.get(
            "current_to_constant_target_ratio"
        ),
    }


def _timing_evidence(baseline_real_run: Path) -> Dict[str, Any]:
    inspection = inspect_temporal_run(baseline_real_run)
    timing = dict(inspection.get("timing_analysis", {}))
    initial = dict(timing.get("initial_activation", {}))
    steady = dict(timing.get("steady_state_runtime_epochs", {}))
    parameter = dict(timing.get("parameter_epochs", {}))
    teardown = dict(inspection.get("teardown_cleanup_summary", {}))
    separated = (
        initial.get("classification") == "bootstrap_plus_initial_runtime_epoch"
        and bool(initial.get("steady_runtime_excludes_initial_activation"))
        and int(steady.get("excluded_initial_runtime_epoch_count", 0) or 0) >= 1
    )
    return {
        "initial_activation": initial,
        "steady_runtime_epochs": steady,
        "parameter_epochs": parameter,
        "teardown_cleanup": teardown,
        "initial_activation_separated_from_steady_runtime": separated,
        "classification_recommendation": (
            "report_bootstrap_initial_activation_steady_runtime_parameter_and_teardown_separately"
        ),
    }


def inspect_goal_evidence(
    *,
    near_run: Path = DEFAULT_NEAR_RUN,
    varied_runs: Sequence[Path] = (DEFAULT_MIXED_RUN, DEFAULT_FAR_RUN),
    return_audit_run: Path = DEFAULT_RETURN_AUDIT_RUN,
    baseline_real_run: Path = DEFAULT_BASELINE_REAL_RUN,
    live_preflight: bool = False,
) -> Dict[str, Any]:
    readiness = summarize_goal_readiness(
        near_run=near_run,
        varied_runs=list(varied_runs),
        return_audit_run=return_audit_run,
        baseline_real_run=baseline_real_run,
        protection_run=baseline_real_run,
        live_preflight=live_preflight,
    )
    return_path = _return_path_evidence(
        return_audit_run,
        dict(readiness.get("return_path_migration", {})),
    )
    return {
        "evidence_kind": "skyseed_1shell_172_gz_goal_evidence",
        "schema_version": "skyseed-goal-evidence-v1alpha1",
        "distance_diversity": _distance_evidence(
            near_run=near_run,
            varied_runs=varied_runs,
        ),
        "return_path_isolation": return_path,
        "directional_isolation": _directional_isolation_evidence(
            return_audit_run=return_audit_run,
            return_path=return_path,
        ),
        "runtime_update_protection": readiness.get("runtime_update_protection", {}),
        "timing_classification": _timing_evidence(baseline_real_run),
        "goal_readiness": readiness,
    }


def _gateway_counts(value: Any) -> str:
    return ",".join(
        f"{key}:{count}" for key, count in dict(value or {}).items()
    ) or "none"


def render_goal_evidence_summary(report: Dict[str, Any]) -> str:
    distance = dict(report.get("distance_diversity", {}))
    return_path = dict(report.get("return_path_isolation", {}))
    directional = dict(report.get("directional_isolation", {}))
    protection = dict(report.get("runtime_update_protection", {}))
    timing = dict(report.get("timing_classification", {}))
    initial = dict(timing.get("initial_activation", {}))
    steady = dict(timing.get("steady_runtime_epochs", {}))
    parameter = dict(timing.get("parameter_epochs", {}))
    teardown = dict(timing.get("teardown_cleanup", {}))
    readiness = dict(report.get("goal_readiness", {}))

    lines: List[str] = [
        "Skyseed 1-shell / 172-sat G/Z evidence",
        (
            "  distance_diversity: "
            f"near_one_hop={distance.get('near_one_hop')} "
            f"varied_multihop_runs={distance.get('varied_multihop_run_count')} "
            f"one_hop_is_geography_specific={distance.get('one_hop_is_geography_specific')}"
        ),
    ]
    for run in list(distance.get("runs", [])):
        run = dict(run)
        lines.append(
            "    profile="
            f"{run.get('profile')} run={run.get('run_id')} "
            f"covered_uts={run.get('covered_runtime_ut_count')}/{run.get('selected_ut_count')} "
            f"isl_hops_avg={run.get('isl_hop_count_avg')} "
            f"isl_hops_max={run.get('isl_hop_count_max')} "
            f"runtime_actions_avg={run.get('runtime_update_action_count_avg')} "
            f"runtime_actions_max={run.get('runtime_update_action_count_max')} "
            f"node_subplans_avg={run.get('node_subplan_count_avg')} "
            f"node_subplans_max={run.get('node_subplan_count_max')} "
            f"gateways={_gateway_counts(run.get('selected_gateway_counts'))}"
        )
    lines.extend(
        [
            (
                "  return_path_isolation: "
                f"current={return_path.get('current_linux_realization')} "
                f"target={return_path.get('target_backbone_realization')} "
                f"selected_flows={return_path.get('selected_total_flow_count')} "
                f"current_backbone_items={return_path.get('selected_current_backbone_items')} "
                f"target_backbone_items={return_path.get('selected_target_backbone_items')} "
                f"ratio={return_path.get('selected_current_to_target_ratio')} "
                f"top_backbone_work_item={return_path.get('top_backbone_work_item')} "
                f"candidate_zone_routes={return_path.get('selected_candidate_shared_zone_route_count')} "
                f"candidate_zone_route_coverage={return_path.get('selected_candidate_zone_route_coverage_ratio')} "
                f"candidate_zone_route_complete={return_path.get('selected_candidate_zone_route_complete')} "
                f"backbone_per_ut_or_per_flow={return_path.get('backbone_return_still_per_ut_or_per_flow')}"
            ),
            (
                "  directional_isolation: "
                f"forward_model={directional.get('forward_direction_model')} "
                f"forward_gateway_classes={directional.get('forward_gateway_class_target_count')} "
                f"forward_flow_to_gateway_class_ratio={directional.get('forward_flow_to_gateway_class_ratio')} "
                f"forward_bounded={directional.get('forward_state_bounded_by_gateway_classes')} "
                f"return_model={directional.get('return_direction_current_model')} "
                f"return_items_per_flow={directional.get('return_current_backbone_items_per_flow')} "
                f"return_target_items_per_flow={directional.get('return_target_backbone_items_per_flow')} "
                f"projection_10000_current={directional.get('projection_10000_current_backbone_items')} "
                f"projection_10000_target_constant={directional.get('projection_10000_target_constant_backbone_items')} "
                f"projection_10000_ratio_constant={directional.get('projection_10000_ratio_constant')}"
            ),
            (
                "  runtime_update_protection: "
                f"ready={protection.get('ready')} "
                f"default_scope={protection.get('default_validation_scope')} "
                f"test_coverage={protection.get('test_coverage_present')} "
                f"artifact_ping_validation={protection.get('artifact_ping_validation_present')} "
                f"artifact_incremental_reuse={protection.get('artifact_incremental_reuse_present')}"
            ),
            (
                "  timing_classification: "
                f"initial_separated={timing.get('initial_activation_separated_from_steady_runtime')} "
                f"initial_wall={initial.get('initial_activation_wall_seconds')} "
                f"first_runtime_execution={initial.get('first_runtime_epoch_execution_after_prepare_reconcile_wall_seconds')} "
                f"steady_count={steady.get('count')} "
                f"steady_wall_avg={steady.get('average_transition_wall_seconds')} "
                f"steady_node_local_avg={steady.get('average_node_local_apply_wall_seconds')} "
                f"parameter_count={parameter.get('count')} "
                f"parameter_wall_avg={parameter.get('average_transition_wall_seconds')} "
                f"teardown_present={teardown.get('present')}"
            ),
            (
                "  gate: "
                f"static_evidence_ready={readiness.get('static_evidence_ready')} "
                f"long_real_run_ready={readiness.get('long_real_run_ready')} "
                f"next_action={readiness.get('next_action')}"
            ),
        ]
    )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Combine preview distance diversity, return-path migration worklist, "
            "runtime-update protections, and timing classification evidence for "
            "the active 1-shell G/Z runtime goal."
        )
    )
    parser.add_argument("--near-run", type=Path, default=DEFAULT_NEAR_RUN)
    parser.add_argument(
        "--varied-run",
        action="append",
        type=Path,
        default=None,
        help="Preview run with non-near UT/GW placement; repeatable.",
    )
    parser.add_argument("--return-audit-run", type=Path, default=DEFAULT_RETURN_AUDIT_RUN)
    parser.add_argument("--baseline-real-run", type=Path, default=DEFAULT_BASELINE_REAL_RUN)
    parser.add_argument("--live-preflight", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--write-report", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    varied_runs = (
        list(args.varied_run)
        if args.varied_run
        else [DEFAULT_MIXED_RUN, DEFAULT_FAR_RUN]
    )
    report = inspect_goal_evidence(
        near_run=args.near_run,
        varied_runs=varied_runs,
        return_audit_run=args.return_audit_run,
        baseline_real_run=args.baseline_real_run,
        live_preflight=bool(args.live_preflight),
    )
    rendered_json = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.write_report:
        args.write_report.expanduser().resolve().parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        args.write_report.expanduser().write_text(rendered_json, encoding="utf-8")
    if args.format == "json":
        print(rendered_json, end="")
    else:
        print(render_goal_evidence_summary(report))
    return 0 if report["goal_readiness"].get("static_evidence_ready") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
