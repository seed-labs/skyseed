#!/usr/bin/env python3
# encoding: utf-8

"""Run the fixed post-reboot/new-session Skyseed readiness check."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from skyseed_v1.operations.paths import (
    PROJECT_ROOT,
    PROJECT_RUNTIME_ROOT,
    SEED_EMULATOR_DEPENDENCY_ROOT,
    SOURCE_ROOT,
    bootstrap_project_imports,
)

bootstrap_project_imports()
SEED310_PYTHON = "/home/seed/miniconda3/envs/seed310/bin/python"
RUNBOOK_PATH = PROJECT_ROOT / "docs" / "operations" / "expensive_runs_EN.md"
LEGACY_PYTHONPATH_ENTRIES = {"/home/seed/seed-emulator"}
OPERATIONAL_CLEAN_NON_BLOCKING_PREFLIGHT_WARNINGS = {
    "host_mpls_not_ready",
    "bridge_netfilter_passthrough_not_ready",
}

OPERATIONAL_ITERATION_RUN_ARGS = [
    "run",
    "--run-root",
    "examples/s4_operational_clean/output",
    "--run-id",
    "<run_id>",
    "--example",
    "s4_operational_clean",
    "--format",
    "json",
]


def _clean_session_subprocess_env(
    base_env: Mapping[str, str] | None = None,
) -> Dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    required = [str(SOURCE_ROOT), str(SEED_EMULATOR_DEPENDENCY_ROOT)]
    existing = [
        item
        for item in str(env.get("PYTHONPATH") or "").split(os.pathsep)
        if item and os.path.normpath(item) not in LEGACY_PYTHONPATH_ENTRIES
    ]
    merged: list[str] = []
    for item in [*required, *existing]:
        if item and item not in merged:
            merged.append(item)
    env["PYTHONPATH"] = os.pathsep.join(merged)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def operational_iteration_profile() -> Dict[str, Any]:
    clean_pythonpath = f"{SOURCE_ROOT}{os.pathsep}{SEED_EMULATOR_DEPENDENCY_ROOT}"
    run_command = [
        "env",
        "PYTHONNOUSERSITE=1",
        f"PYTHONPATH={clean_pythonpath}",
        "skyseed-v1",
        *OPERATIONAL_ITERATION_RUN_ARGS,
    ]
    return {
        "profile_id": "rich_s4_operational_clean_bpf_4h_validation",
        "profile_status": "current_fast_iteration_path",
        "purpose": (
            "Current high-iteration BPF-owned operational-clean run. It keeps "
            "the experiment shape stable at 240s horizons, 10s slice/evaluation "
            "cadence, and 4 horizons, while allowing each round to generate and "
            "freeze fresh prepared inputs."
        ),
        "run_command": run_command,
        "launch_environment": {
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": clean_pythonpath,
            "legacy_seed_emulator_pythonpath_removed": True,
        },
        "validation_profile": "rich_s4_operational_clean_bpf",
        "resolved_runtime_contract": {
            "horizon_seconds": 240,
            "sample_interval_seconds": 10,
            "event_evaluation_interval_seconds": 10,
            "continuous": True,
            "loop_count": 4,
            "runtime_mode": "operational_clean",
            "forwarding_backend": "bpf_owned",
            "preflight_policy": "require-clean",
            "host_prepare_policy": "auto",
            "runtime_epoch_validation_interval": 0,
            "parameter_epoch_validation_interval": 0,
            "runtime_epoch_validation_scope": "reapplied_gateway_class_sample",
            "runtime_ping_observer_seconds": 30,
            "forbidden_runtime_work": [
                "full_reload",
                "full_scan",
                "live_dump_diff",
                "hidden_reconcile",
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
            "continuous=true",
            "loop_count=4",
            "runtime_mode=operational_clean",
            "forwarding_backend=bpf_owned",
            "preflight_policy=require_clean",
            "host_prepare_policy=auto",
            "runtime_ping_observer_seconds=30 (nonblocking observation)",
            "no full reload, full scan, hidden repair, or fallback route",
        ],
        "expected_artifact_root": "examples/s4_operational_clean/output",
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
    non_blocking_warnings = sorted(
        warnings & OPERATIONAL_CLEAN_NON_BLOCKING_PREFLIGHT_WARNINGS
    )
    blocking_warnings = sorted(
        warnings - OPERATIONAL_CLEAN_NON_BLOCKING_PREFLIGHT_WARNINGS
    )
    command = list(profile.get("run_command") or [])
    command_text = " ".join(command)
    contract = dict(profile.get("resolved_runtime_contract") or {})
    contract_shape_ready = (
        int(contract.get("horizon_seconds") or 0) == 240
        and int(contract.get("sample_interval_seconds") or 0) == 10
        and int(contract.get("event_evaluation_interval_seconds") or 0) == 10
        and bool(contract.get("continuous")) is True
        and int(contract.get("loop_count") or 0) == 4
        and str(contract.get("runtime_mode") or "") == "operational_clean"
        and str(contract.get("forwarding_backend") or "") == "bpf_owned"
        and str(contract.get("host_prepare_policy") or "") == "auto"
        and int(contract.get("runtime_ping_observer_seconds") or 0) == 30
    )
    explicit_command_shape_ready = all(
        required in command_text
        for required in (
            "--horizon-seconds 240",
            "--sample-interval 10",
            "--event-evaluation-interval 10",
            "--continuous",
            "--loop-count 4",
            "--runtime-mode operational_clean",
            "--forwarding-backend bpf_owned",
            "--host-prepare-policy auto",
        )
    )
    profile_command_shape_ready = (
        "skyseed-v1 run" in command_text
        and "--example s4_operational_clean" in command_text
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
        "non_blocking_warnings": non_blocking_warnings,
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


def _preflight_not_run() -> Dict[str, Any]:
    return {
        "present": False,
        "source": None,
        "warning_reasons": [],
        "blocking_reasons": [],
        "scoped_hygiene_ready": None,
        "preferred_seed310_selected": None,
        "backend_host_runtime_ready": None,
        "long_real_run_environment_ready": None,
    }


def _run_live_preflight() -> Dict[str, Any]:
    result = subprocess.run(
        [
            SEED310_PYTHON,
            "-m",
            "skyseed_v1.cli.preflight",
            "--profile",
            "operational-clean-bpf-owned",
            "--format",
            "json",
        ],
        cwd=str(PROJECT_ROOT),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_clean_session_subprocess_env(),
    )
    if result.returncode != 0:
        return {
            **_preflight_not_run(),
            "present": False,
            "source": "skyseed_preflight",
            "blocking_reasons": [f"skyseed_preflight_returncode:{result.returncode}"],
            "stderr": result.stderr.strip(),
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            **_preflight_not_run(),
            "present": False,
            "source": "skyseed_preflight",
            "blocking_reasons": [f"skyseed_preflight_json_error:{exc}"],
            "stderr": result.stderr.strip(),
        }
    assessment = dict(payload.get("assessment") or {})
    checks = dict(payload.get("checks") or {})
    python_runtime = dict(checks.get("python_runtime") or {})
    counts = dict(assessment.get("cleanliness_counts") or {})
    environment_clean = bool(assessment.get("environment_clean"))
    return {
        "present": True,
        "source": "skyseed_preflight",
        "warning_reasons": list(assessment.get("warning_reasons") or []),
        "blocking_reasons": list(assessment.get("blocking_reasons") or []),
        "scoped_hygiene_ready": environment_clean,
        "preferred_seed310_selected": bool(
            python_runtime.get("preferred_seed310_selected")
        ),
        "backend_host_runtime_ready": "not_applicable_for_operational_clean_bpf_owned",
        "long_real_run_environment_ready": bool(assessment.get("ready")),
        "cleanliness_counts": counts,
        "workspace_root": payload.get("workspace_root"),
    }


def summarize_session_check(
    *,
    live_preflight: bool = True,
) -> Dict[str, Any]:
    preflight = _run_live_preflight() if live_preflight else _preflight_not_run()
    long_real_run_ready = bool(
        preflight.get("present")
        and preflight.get("scoped_hygiene_ready")
        and preflight.get("preferred_seed310_selected")
        and not preflight.get("blocking_reasons")
    )
    readiness = {
        "preflight": preflight,
        "static_evidence_ready": False,
        "long_real_run_ready": long_real_run_ready,
        "next_action": (
            "start_active_validation_profile"
            if long_real_run_ready
            else "run_host_prepare_then_recheck_preflight_before_real_run"
        ),
    }
    iteration_profile = operational_iteration_profile()
    return {
        "check_kind": "skyseed_post_reboot_session_check",
        "schema_version": "skyseed-session-check-v1alpha1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "repo_root": str(PROJECT_ROOT),
        "git_status": _run_git_status(PROJECT_ROOT),
        "goal_readiness": readiness,
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
    display_readiness["preflight"] = display_preflight
    iteration_gate = dict(report.get("operational_iteration_gate", {}))
    iteration_profile = dict(report.get("operational_iteration_profile", {}))
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
        (
            "  preflight: "
            f"present={display_preflight.get('present')} "
            f"scoped_hygiene_ready={display_preflight.get('scoped_hygiene_ready')} "
            f"preferred_seed310_selected={display_preflight.get('preferred_seed310_selected')} "
            f"warnings={','.join(display_preflight.get('warning_reasons', [])) or 'none'}"
        ),
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
        help="Append a compact session-check note to the v1 runtime state directory.",
    )
    parser.add_argument(
        "--status-path",
        type=Path,
        default=PROJECT_RUNTIME_ROOT / "session_status.md",
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
