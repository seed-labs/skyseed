#!/usr/bin/env python3
# encoding: utf-8

"""Run the fixed post-reboot/new-session Skyseed readiness check."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Sequence

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()
REPO_ROOT = SATELLITE_DIR.parent
SEED310_PYTHON = "/home/seed/miniconda3/envs/seed310/bin/python"
RUNBOOK_PATH = SATELLITE_DIR / "docs" / "known_issues_runbook.md"
LEGACY_HOST_RUNTIME_WARNINGS = {
    "host_mpls_not_ready",
    "host_mpls_preflight_skipped",
    "bridge_netfilter_passthrough_not_ready",
}

CANONICAL_FAST_LONG_RUN_ARGS = [
    "--run-mode",
    "real-runtime",
    "--run-id",
    "<run_id>",
    "--orbit-input-mode",
    "planned-shell",
    "--planned-shell-id",
    "S5",
    "--max-satellites",
    "172",
    "--max-uts",
    "12",
    "--max-gateways",
    "6",
    "--ut-ids",
    "",
    "--gateway-ids",
    "",
    "--ut-catalog",
    "satellite/configs/starlink_shell_ground/one_shell_ut_catalog_12_grouped_valid6_gateways.json",
    "--gateway-catalog",
    "satellite/configs/starlink_shell_ground/one_shell_gateway_catalog_valid6.json",
    "--zone-catalog",
    "satellite/configs/starlink_shell_ground/one_shell_zone_catalog_6_valid6_gateways.json",
    "--ground-placement",
    "catalog",
    "--start-time",
    "2026-06-11T09:57:20Z",
    "--sample-interval",
    "60",
    "--event-evaluation-interval",
    "10",
    "--horizon-seconds",
    "7200",
    "--continuous",
    "--parameter-max-interval",
    "120",
    "--delay-threshold-ms",
    "0.5",
    "--loss-threshold-pct",
    "0.02",
    "--bandwidth-threshold-pct",
    "5.0",
    "--routing-cost-threshold",
    "5",
    "--runtime-mode",
    "fast-demo",
    "--loop-count",
    "1",
    "--interval",
    "0",
    "--ping-watch",
    "ut101:as154-host-new",
    "--stream-progress",
    "--progress-heartbeat-seconds",
    "15",
    "--startup-stall-warning-seconds",
    "900",
    "--epoch-stall-warning-seconds",
    "300",
    "--preflight-policy",
    "require-clean",
    "--host-prepare-policy",
    "auto",
    "--compose-up-timeout-seconds",
    "0",
    "--compose-build-timeout-seconds",
    "0",
    "--container-readiness-timeout-seconds",
    "0",
    "--compose-build-policy",
    "if-needed",
    "--format",
    "json",
]

OPERATIONAL_ITERATION_RUN_ARGS = [
    "--run-mode",
    "real-runtime",
    "--run-id",
    "<run_id>",
    "--run-root",
    "satellite/output/validation_profiles",
    "--operational-profile",
    "s4_h3_240s_bpf_owned_clean",
    "--format",
    "json",
]

from inspect_goal_readiness import (  # noqa: E402
    DEFAULT_BASELINE_REAL_RUN,
    DEFAULT_FAR_RUN,
    DEFAULT_MIXED_RUN,
    DEFAULT_NEAR_RUN,
    DEFAULT_RETURN_AUDIT_RUN,
    render_goal_readiness_summary,
    summarize_goal_readiness,
)


def canonical_fast_long_run_profile() -> Dict[str, Any]:
    run_command = [
        SEED310_PYTHON,
        str(SATELLITE_DIR / "apps" / "run_starlink_shell_experiment.py"),
        *CANONICAL_FAST_LONG_RUN_ARGS,
    ]
    return {
        "profile_id": "1shell_172_12ut6gw_gz_2h_fast_representative",
        "profile_status": "canonical_fast_path",
        "purpose": (
            "Fast representative 1-shell 172-satellite 12UT/6GW G/Z real-runtime "
            "long run. Use this for performance evidence unless the objective is "
            "explicitly diagnostic."
        ),
        "must_read_before_start": str(RUNBOOK_PATH),
        "prestart_commands": [
            ["sed", "-n", "1,360p", str(RUNBOOK_PATH)],
            ["git", "status", "--short", "--branch"],
            [
                SEED310_PYTHON,
                str(SATELLITE_DIR / "apps" / "skyseed_session_check.py"),
                "--format",
                "text",
            ],
            ["free", "-h"],
            ["df", "-h", str(REPO_ROOT)],
        ],
        "run_command": run_command,
        "representative_validation_profile": {
            "runtime_mode": "fast-demo",
            "pre_transition_probe_interval": 0,
            "runtime_epoch_validation_interval": 0,
            "parameter_epoch_validation_interval": 0,
            "runtime_epoch_validation_scope": "reapplied_gateway_class_sample",
            "keep": [
                "host_prepare_auto",
                "require_clean_preflight",
                "ping_watch",
                "first_runtime_epoch_ping_validation",
                "first_parameter_epoch_targeted_validation",
                "final_runtime_connectivity_validation",
                "runtime_lock",
                "post_run_preflight",
                "resource_usage_jsonl",
            ],
            "diagnostic_flags_not_for_long_run": [
                "--runtime-epoch-validation-interval 1",
                "--parameter-epoch-validation-interval 1",
                "--pre-transition-probe-interval 1",
                "--runtime-mode correctness",
            ],
        },
        "resource_gate": {
            "record_before_start": [
                "MemAvailable",
                "SwapFree",
                "workspace filesystem Avail",
                "existing run directory size",
                "skyseed preflight warning_reasons",
            ],
            "danger_zone": {
                "swapfree_kib": 0,
                "memavailable_kib_below": 512000,
                "meaning": (
                    "Stop cleanly and preserve artifacts unless the run is explicitly "
                    "characterizing host resource limits."
                ),
            },
        },
        "timing_policy": {
            "actual_network_update_headline": [
                "semantic_data_plane_mutation_apply_seconds",
                "node_local_apply_wall_seconds",
            ],
            "scheduler_pressure_headline": ["transition_wall_seconds"],
            "cleanup_optimization_bucket": [
                "semantic_pre_apply_cleanup_or_blocking_verification_seconds",
                "node_local_incremental_cleanup_wall_seconds",
            ],
            "do_not_report_as_network_update": [
                "transition_wall_seconds",
                "runtime_rollback",
                "host_artifact_cleanup",
                "post_run_preflight",
            ],
        },
        "monitor_commands": [
            [
                SEED310_PYTHON,
                str(SATELLITE_DIR / "apps" / "inspect_temporal_run.py"),
                "satellite/output/starlink_shell_experiments/<run_id>",
                "--format",
                "text",
            ],
            [
                "tail",
                "-n",
                "5",
                "satellite/output/starlink_shell_experiments/<run_id>/resource_usage.jsonl",
            ],
        ],
    }


def operational_iteration_profile() -> Dict[str, Any]:
    run_command = [
        SEED310_PYTHON,
        str(SATELLITE_DIR / "apps" / "run_starlink_shell_experiment.py"),
        *OPERATIONAL_ITERATION_RUN_ARGS,
    ]
    return {
        "profile_id": "s4_h3_240s_bpf_owned_operational_iteration",
        "profile_status": "current_fast_iteration_path",
        "purpose": (
            "Current high-iteration BPF-owned operational-clean run. It keeps "
            "the experiment shape stable at 240s horizons, 10s slice/evaluation "
            "cadence, and 3 horizons, while allowing each round to generate and "
            "freeze fresh prepared inputs."
        ),
        "run_command": run_command,
        "operational_profile": "s4_h3_240s_bpf_owned_clean",
        "resolved_runtime_contract": {
            "horizon_seconds": 240,
            "sample_interval_seconds": 10,
            "event_evaluation_interval_seconds": 10,
            "loop_count": 3,
            "runtime_mode": "operational_clean",
            "forwarding_backend": "bpf_owned",
            "preflight_policy": "require-clean",
            "host_prepare_policy": "auto",
            "runtime_epoch_validation_interval": 0,
            "parameter_epoch_validation_interval": 0,
            "runtime_epoch_validation_scope": "reapplied_gateway_class_sample",
            "forbidden_runtime_work": [
                "full_reload",
                "full_scan",
                "live_dump_diff",
                "hidden_reconcile",
                "legacy_controller_fallback",
                "linux_core_forwarding_fallback",
            ],
        },
        "fresh_prepared_inputs_allowed": True,
        "load_frozen_prepared_bundle_required": False,
        "stale_static_goal_evidence_blocks_iteration": False,
        "must_keep_invariants": [
            "horizon_seconds=240",
            "sample_interval_seconds=10",
            "event_evaluation_interval_seconds=10",
            "loop_count=3",
            "runtime_mode=operational_clean",
            "forwarding_backend=bpf_owned",
            "preflight_policy=require_clean",
            "host_prepare_policy=auto",
            "no full reload, full scan, hidden repair, or fallback route",
        ],
        "expected_artifact_root": "satellite/output/validation_profiles",
    }


def _operational_iteration_gate(
    *,
    readiness: Dict[str, Any],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    preflight = dict(readiness.get("preflight") or {})
    warnings = set(str(item) for item in list(preflight.get("warning_reasons") or []))
    blocking_reasons = [
        str(item) for item in list(preflight.get("blocking_reasons") or [])
    ]
    blocking_warnings = sorted(
        warning for warning in warnings if warning not in LEGACY_HOST_RUNTIME_WARNINGS
    )
    command = list(profile.get("run_command") or [])
    command_text = " ".join(command)
    contract = dict(profile.get("resolved_runtime_contract") or {})
    contract_shape_ready = (
        int(contract.get("horizon_seconds") or 0) == 240
        and int(contract.get("sample_interval_seconds") or 0) == 10
        and int(contract.get("event_evaluation_interval_seconds") or 0) == 10
        and int(contract.get("loop_count") or 0) == 3
        and str(contract.get("runtime_mode") or "") == "operational_clean"
        and str(contract.get("forwarding_backend") or "") == "bpf_owned"
        and str(contract.get("host_prepare_policy") or "") == "auto"
    )
    explicit_command_shape_ready = all(
        required in command_text
        for required in (
            "--horizon-seconds 240",
            "--sample-interval 10",
            "--event-evaluation-interval 10",
            "--loop-count 3",
            "--runtime-mode operational_clean",
            "--forwarding-backend bpf_owned",
            "--host-prepare-policy auto",
        )
    )
    profile_command_shape_ready = (
        "--operational-profile s4_h3_240s_bpf_owned_clean" in command_text
        and contract_shape_ready
    )
    command_shape_ready = explicit_command_shape_ready or profile_command_shape_ready
    preflight_present = bool(preflight.get("present"))
    scoped_hygiene_ready = bool(preflight.get("scoped_hygiene_ready"))
    preferred_python = bool(preflight.get("preferred_seed310_selected"))
    ready = (
        preflight_present
        and scoped_hygiene_ready
        and preferred_python
        and not blocking_reasons
        and not blocking_warnings
        and command_shape_ready
    )
    blockers = []
    if not preflight_present:
        blockers.append("live_preflight_missing")
    if not scoped_hygiene_ready:
        blockers.append("scoped_environment_hygiene_not_ready")
    if not preferred_python:
        blockers.append("preferred_seed310_python_not_selected")
    blockers.extend(f"preflight_blocker:{reason}" for reason in blocking_reasons)
    blockers.extend(f"preflight_warning:{warning}" for warning in blocking_warnings)
    if not command_shape_ready:
        blockers.append("operational_iteration_command_shape_mismatch")
    return {
        "schema_version": "skyseed-operational-iteration-gate-v1",
        "ready": ready,
        "blockers": blockers,
        "non_blocking_warnings": [],
        "suppressed_legacy_host_runtime_warnings": sorted(
            warning for warning in warnings if warning in LEGACY_HOST_RUNTIME_WARNINGS
        ),
        "warning_scope": "operational_clean_bpf_owned",
        "stale_static_goal_evidence_blocks_iteration": False,
        "static_evidence_ready": bool(readiness.get("static_evidence_ready")),
        "command_shape_ready": command_shape_ready,
        "profile_id": str(profile.get("profile_id") or ""),
    }


def _run_git_status(repo_root: Path) -> Dict[str, Any]:
    result = subprocess.run(
        ["git", "status", "--short", "--branch"],
        cwd=str(repo_root),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    branch_line = lines[0] if lines else ""
    dirty_entries = lines[1:] if branch_line.startswith("## ") else lines
    return {
        "returncode": result.returncode,
        "branch_line": branch_line,
        "dirty_entry_count": len(dirty_entries),
        "dirty_entries": dirty_entries,
        "stderr": result.stderr.strip(),
    }


def summarize_session_check(
    *,
    live_preflight: bool = True,
    near_run: Path = DEFAULT_NEAR_RUN,
    varied_runs: Sequence[Path] = (DEFAULT_MIXED_RUN, DEFAULT_FAR_RUN),
    return_audit_run: Path = DEFAULT_RETURN_AUDIT_RUN,
    baseline_real_run: Path = DEFAULT_BASELINE_REAL_RUN,
) -> Dict[str, Any]:
    readiness = summarize_goal_readiness(
        near_run=near_run,
        varied_runs=list(varied_runs),
        return_audit_run=return_audit_run,
        baseline_real_run=baseline_real_run,
        protection_run=baseline_real_run,
        live_preflight=live_preflight,
    )
    iteration_profile = operational_iteration_profile()
    return {
        "check_kind": "skyseed_post_reboot_session_check",
        "schema_version": "skyseed-session-check-v1alpha1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "repo_root": str(REPO_ROOT),
        "git_status": _run_git_status(REPO_ROOT),
        "goal_readiness": readiness,
        "canonical_fast_long_run_profile": canonical_fast_long_run_profile(),
        "operational_iteration_profile": iteration_profile,
        "operational_iteration_gate": _operational_iteration_gate(
            readiness=readiness,
            profile=iteration_profile,
        ),
    }


def render_session_check_summary(report: Dict[str, Any]) -> str:
    git = dict(report.get("git_status", {}))
    readiness = dict(report.get("goal_readiness", {}))
    display_readiness = dict(readiness)
    display_preflight = dict(display_readiness.get("preflight") or {})
    display_preflight["raw_warning_reasons"] = list(
        display_preflight.get("warning_reasons") or []
    )
    display_preflight["warning_reasons"] = [
        str(warning)
        for warning in list(display_preflight.get("warning_reasons") or [])
        if str(warning) not in LEGACY_HOST_RUNTIME_WARNINGS
    ]
    if any(
        warning in LEGACY_HOST_RUNTIME_WARNINGS
        for warning in display_preflight.get("raw_warning_reasons", [])
    ):
        display_preflight["host_runtime_ready"] = (
            "legacy_not_applicable_for_bpf_owned_operational_iteration"
        )
    display_readiness["preflight"] = display_preflight
    iteration_gate = dict(report.get("operational_iteration_gate", {}))
    iteration_profile = dict(report.get("operational_iteration_profile", {}))
    profile = dict(report.get("canonical_fast_long_run_profile", {}))
    validation_profile = dict(profile.get("representative_validation_profile", {}))
    timing_policy = dict(profile.get("timing_policy", {}))
    lines = [
        "Skyseed session check",
        f"  generated_at={report.get('generated_at')}",
        f"  branch={git.get('branch_line')}",
        f"  dirty_entries={git.get('dirty_entry_count')}",
        f"  static_evidence_ready={readiness.get('static_evidence_ready')}",
        f"  long_real_run_ready={readiness.get('long_real_run_ready')}",
        f"  next_action={readiness.get('next_action')}",
        f"  operational_iteration_ready={iteration_gate.get('ready')}",
        f"  operational_iteration_profile={iteration_profile.get('profile_id')}",
        (
            "  operational_iteration_warnings="
            f"{','.join(iteration_gate.get('non_blocking_warnings', [])) or 'none'}"
        ),
        f"  canonical_fast_profile={profile.get('profile_id')}",
        (
            "  representative_validation: "
            f"runtime_epoch_interval={validation_profile.get('runtime_epoch_validation_interval')} "
            f"parameter_epoch_interval={validation_profile.get('parameter_epoch_validation_interval')} "
            f"scope={validation_profile.get('runtime_epoch_validation_scope')}"
        ),
        (
            "  timing_headlines: "
            f"actual_update={','.join(timing_policy.get('actual_network_update_headline', []))} "
            f"scheduler_pressure={','.join(timing_policy.get('scheduler_pressure_headline', []))} "
            f"cleanup={','.join(timing_policy.get('cleanup_optimization_bucket', []))}"
        ),
        render_goal_readiness_summary(display_readiness),
    ]
    return "\n".join(lines)


def format_status_note(report: Dict[str, Any]) -> str:
    git = dict(report.get("git_status", {}))
    readiness = dict(report.get("goal_readiness", {}))
    iteration_gate = dict(report.get("operational_iteration_gate", {}))
    return "\n".join(
        [
            "",
            "## Session Check Log",
            "",
            f"- `{report.get('generated_at')}`: branch `{git.get('branch_line')}`, "
            f"dirty entries `{git.get('dirty_entry_count')}`, "
            f"static_ready `{readiness.get('static_evidence_ready')}`, "
            f"long_real_run_ready `{readiness.get('long_real_run_ready')}`, "
            f"operational_iteration_ready `{iteration_gate.get('ready')}`, "
            f"next_action `{readiness.get('next_action')}`, "
            f"operational warnings "
            f"`{','.join(iteration_gate.get('non_blocking_warnings', [])) or 'none'}`.",
        ]
    )


def append_status_note(report: Dict[str, Any], status_path: Path) -> None:
    status_path = status_path.expanduser().resolve()
    status_path.write_text(
        status_path.read_text(encoding="utf-8").rstrip()
        + format_status_note(report)
        + "\n",
        encoding="utf-8",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the fixed Skyseed new-session check: git status, goal readiness, "
            "and optional live preflight."
        )
    )
    parser.add_argument(
        "--skip-live-preflight",
        action="store_true",
        help="Do not run live skyseed_preflight; useful for quick static checks.",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--write-report", type=Path, default=None)
    parser.add_argument(
        "--append-status-note",
        action="store_true",
        help="Append a compact session-check note to satellite/status.md.",
    )
    parser.add_argument(
        "--status-path",
        type=Path,
        default=SATELLITE_DIR / "status.md",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = summarize_session_check(live_preflight=not args.skip_live_preflight)
    rendered_json = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.write_report:
        args.write_report.expanduser().resolve().parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        args.write_report.expanduser().write_text(rendered_json, encoding="utf-8")
    if args.append_status_note:
        append_status_note(report, args.status_path)
    if args.format == "json":
        print(rendered_json, end="")
    else:
        print(render_session_check_summary(report))
    return 0 if report["operational_iteration_gate"].get("ready") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
