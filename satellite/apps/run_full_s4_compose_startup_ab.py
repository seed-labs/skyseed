#!/usr/bin/env python3
# encoding: utf-8

"""Run startup-only full-S4 compose concurrency A/B trials."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Sequence, Tuple


REPO_ROOT = Path(__file__).resolve().parents[2]
SATELLITE_DIR = REPO_ROOT / "satellite"
DEFAULT_RUN_ROOT = SATELLITE_DIR / "output" / "starlink_shell_experiments"
DEFAULT_REPORT_DIR = DEFAULT_RUN_ROOT / "full-s4-compose-startup-ab-20260622"


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: Dict[str, Any] | List[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_profiles(value: str) -> List[Tuple[int, int]]:
    profiles: List[Tuple[int, int]] = []
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            continue
        if ":" in item:
            batch_text, parallel_text = item.split(":", 1)
        elif "/" in item:
            batch_text, parallel_text = item.split("/", 1)
        else:
            batch_text = parallel_text = item
        profiles.append((int(batch_text), int(parallel_text)))
    return profiles


def _host_snapshot() -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {
        "timestamp_utc": _timestamp_utc(),
        "load1": None,
        "load5": None,
        "load15": None,
        "swap_used_kib": None,
        "swap_total_kib": None,
    }
    try:
        fields = Path("/proc/loadavg").read_text(encoding="utf-8").split()
        snapshot["load1"] = float(fields[0])
        snapshot["load5"] = float(fields[1])
        snapshot["load15"] = float(fields[2])
    except (OSError, ValueError, IndexError):
        pass
    meminfo: Dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            parts = value.strip().split()
            if parts:
                meminfo[key] = int(parts[0])
    except (OSError, ValueError):
        pass
    swap_total = int(meminfo.get("SwapTotal", 0))
    swap_free = int(meminfo.get("SwapFree", 0))
    snapshot["swap_total_kib"] = swap_total
    snapshot["swap_used_kib"] = max(0, swap_total - swap_free)
    return snapshot


def _compose_residue_count(post_run_preflight: Dict[str, Any]) -> int:
    residue = dict(post_run_preflight.get("compose_residue_summary") or {})
    return sum(int(value or 0) for value in residue.values())


def _docker_daemon_recovery_event_count(text: str) -> int:
    needles = (
        "Cannot connect to the Docker daemon",
        "request canceled while waiting for connection",
        "context deadline exceeded",
        "docker daemon",
        "Docker daemon",
    )
    return sum(text.count(needle) for needle in needles)


def _summarize_stage_results(stage_results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    satellite_wall = 0.0
    ground_wall = 0.0
    timeout_count = 0
    recovered_count = 0
    retry_count = 0
    max_load1 = 0.0
    max_load5 = 0.0
    max_swap_used_kib = 0
    per_batch: List[Dict[str, Any]] = []
    for stage in stage_results:
        stage_type = str(stage.get("stage_type") or "")
        wall = float(stage.get("wall_seconds") or 0.0)
        if stage_type == "satellite_batch":
            satellite_wall += wall
        if stage_type == "endpoint":
            ground_wall += wall
        if bool(stage.get("timed_out")):
            timeout_count += 1
        retry = dict(stage.get("retry_after_timeout") or {})
        if retry:
            retry_count += 1
            if bool(stage.get("recovered_after_timeout")):
                recovered_count += 1
        max_load1 = max(max_load1, float(stage.get("max_observed_load1") or 0.0))
        max_load5 = max(max_load5, float(stage.get("max_observed_load5") or 0.0))
        max_swap_used_kib = max(
            max_swap_used_kib,
            int(stage.get("max_observed_swap_used_kib") or 0),
        )
        if stage_type == "satellite_batch":
            per_batch.append(
                {
                    "stage_id": stage.get("stage_id"),
                    "service_count": stage.get("service_count"),
                    "wall_seconds": wall,
                    "timed_out": bool(stage.get("timed_out")),
                    "recovered_after_timeout": bool(stage.get("recovered_after_timeout")),
                    "max_observed_load1": stage.get("max_observed_load1"),
                    "max_observed_load5": stage.get("max_observed_load5"),
                    "max_observed_swap_used_kib": stage.get("max_observed_swap_used_kib"),
                }
            )
    return {
        "satellite_startup_wall_seconds": round(satellite_wall, 6),
        "ground_endpoint_startup_wall_seconds": round(ground_wall, 6),
        "timeout_count": timeout_count,
        "failed_or_recovered_batch_count": retry_count,
        "recovered_batch_count": recovered_count,
        "max_load1": round(max_load1, 6),
        "max_load5": round(max_load5, 6),
        "max_swap_used_kib": max_swap_used_kib,
        "satellite_batches": per_batch,
    }


def _build_runner_command(
    *,
    args: argparse.Namespace,
    run_id: str,
    batch_size: int,
    compose_parallel: int,
) -> List[str]:
    return [
        sys.executable,
        str(SATELLITE_DIR / "apps" / "run_starlink_shell_experiment.py"),
        "--run-mode",
        "real-runtime",
        "--run-id",
        run_id,
        "--run-root",
        str(args.run_root),
        "--orbit-input-mode",
        "planned-shell",
        "--planned-shell-id",
        "S4",
        "--max-satellites",
        "348",
        "--ground-placement",
        "shell-aware",
        "--step-seconds",
        "120",
        "--slice-count",
        "3",
        "--sample-interval",
        "10",
        "--event-evaluation-interval",
        "10",
        "--horizon-seconds",
        "120",
        "--parameter-max-interval",
        "240",
        "--delay-threshold-ms",
        "0.5",
        "--loss-threshold-pct",
        "0.02",
        "--bandwidth-threshold-pct",
        "5.0",
        "--routing-cost-threshold",
        "5",
        "--ut-ids",
        ",".join(f"ut{index}" for index in range(2001, 2033)),
        "--gateway-ids",
        ",".join(f"gw{index}" for index in range(201, 213)),
        "--ut-catalog",
        "satellite/configs/starlink_shell_ground/s4_gz_rich_validation_ut_catalog_32.json",
        "--gateway-catalog",
        "satellite/configs/starlink_shell_ground/realistic_preview_gateway_catalog_12.json",
        "--zone-catalog",
        "satellite/configs/starlink_shell_ground/s4_gz_rich_validation_zone_catalog_16.json",
        "--docker-dummy-network-pool",
        "10.224.0.0/16",
        "--docker-dummy-network-mask",
        "23",
        "--forwarding-backend",
        "bpf_owned",
        "--compose-startup-mode",
        "infra-then-sat-batches",
        "--compose-satellite-batch-size",
        str(batch_size),
        "--compose-parallel-limit",
        str(compose_parallel),
        "--compose-inter-stage-backoff-seconds",
        str(args.compose_inter_stage_backoff_seconds),
        "--compose-build-policy",
        "if-needed",
        "--compose-up-timeout-seconds",
        str(args.compose_up_timeout_seconds),
        "--container-readiness-timeout-seconds",
        str(args.container_readiness_timeout_seconds),
        "--stop-after-container-readiness",
        "--format",
        "json",
    ]


def _run_streamed_command(command: Sequence[str], *, cwd: Path, label: str) -> Dict[str, Any]:
    process = subprocess.Popen(
        list(command),
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_chunks: List[str] = []
    stderr_chunks: List[str] = []

    def forward(stream: Any, chunks: List[str], stream_name: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                chunks.append(line)
                print(f"[{label} {stream_name}] {line}", end="", flush=True)
        finally:
            stream.close()

    threads = [
        threading.Thread(
            target=forward,
            args=(process.stdout, stdout_chunks, "stdout"),
            daemon=True,
        ),
        threading.Thread(
            target=forward,
            args=(process.stderr, stderr_chunks, "stderr"),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    returncode = process.wait()
    for thread in threads:
        thread.join(timeout=5.0)
    return {
        "returncode": returncode,
        "stdout": "".join(stdout_chunks),
        "stderr": "".join(stderr_chunks),
    }


def _summarize_run(
    *,
    run_dir: Path,
    profile: Dict[str, Any],
    command: Sequence[str],
    returncode: int,
    stdout: str,
    stderr: str,
    wall_seconds: float,
    host_before: Dict[str, Any],
    host_after: Dict[str, Any],
) -> Dict[str, Any]:
    docker_compose_path = run_dir / "docker_compose.json"
    manifest_path = run_dir / "run_manifest.json"
    timing_path = run_dir / "full_s4_retry_timing_breakdown.json"
    docker_compose = _read_json(docker_compose_path) if docker_compose_path.exists() else {}
    manifest = _read_json(manifest_path) if manifest_path.exists() else {}
    timing = _read_json(timing_path) if timing_path.exists() else {}
    stage_results = list(docker_compose.get("compose_up_stage_results") or [])
    stage_summary = _summarize_stage_results(stage_results)
    container_readiness = dict(docker_compose.get("container_readiness") or {})
    post_run_preflight = dict(docker_compose.get("post_run_preflight") or manifest.get("post_run_preflight") or {})
    compose_down_after = dict(docker_compose.get("compose_down_after") or {})
    required_container_count = int(container_readiness.get("required_container_count") or 0)
    required_running_count = required_container_count if container_readiness.get("ready") is True else 0
    residue_count = _compose_residue_count(post_run_preflight)
    docker_recovery_events = _docker_daemon_recovery_event_count(stdout + "\n" + stderr)
    runtime_report = manifest.get("runtime_report")
    if not isinstance(runtime_report, dict):
        runtime_report = {}
    stable_no_timeout = (
        returncode == 0
        and container_readiness.get("ready") is True
        and int(stage_summary["timeout_count"]) == 0
        and int(stage_summary["failed_or_recovered_batch_count"]) == 0
        and residue_count == 0
        and post_run_preflight.get("clean_after_run") is True
    )
    return {
        "profile": dict(profile),
        "run_dir": str(run_dir),
        "command": list(command),
        "returncode": returncode,
        "wall_seconds": round(wall_seconds, 6),
        "stable_no_timeout": stable_no_timeout,
        "container_readiness_ready": container_readiness.get("ready"),
        "required_containers_running_count": required_running_count,
        "required_container_count": required_container_count,
        "compose_up_wall_seconds": manifest.get("phase_timing", {}).get("compose_up_wall_seconds"),
        "container_readiness_wall_seconds": manifest.get("phase_timing", {}).get("container_readiness_wall_seconds"),
        "cleanup_wall_seconds": compose_down_after.get("wall_seconds"),
        "post_run_preflight_clean": post_run_preflight.get("clean_after_run"),
        "residue_count": residue_count,
        "docker_daemon_recovery_event_count": docker_recovery_events,
        "host_before": host_before,
        "host_after": host_after,
        "stage_summary": stage_summary,
        "pre_start_bpf_offline_gate": manifest.get("pre_start_bpf_offline_gate"),
        "timing_attribution": dict(timing.get("attribution") or {}),
        "runtime_success_claimed": manifest.get("aggregate_runtime_succeeded"),
        "runtime_mode_observed": runtime_report.get("mode"),
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
    }


def _write_markdown_report(report_dir: Path, report: Dict[str, Any]) -> None:
    lines = [
        "# Full S4 Compose Startup A/B Report",
        "",
        f"Generated: `{report['generated_at_utc']}`",
        f"Selected profile: `{report.get('selected_profile')}`",
        "",
        "| profile | stable | compose up s | sat startup s | ground startup s | max load1 | timeouts | recoveries | residue |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for trial in report.get("trials", []):
        profile = trial.get("profile", {})
        stage = trial.get("stage_summary", {})
        lines.append(
            "| "
            + " | ".join(
                [
                    f"batch={profile.get('batch_size')},parallel={profile.get('compose_parallel_limit')}",
                    str(trial.get("stable_no_timeout")),
                    str(trial.get("compose_up_wall_seconds")),
                    str(stage.get("satellite_startup_wall_seconds")),
                    str(stage.get("ground_endpoint_startup_wall_seconds")),
                    str(stage.get("max_load1")),
                    str(stage.get("timeout_count")),
                    str(stage.get("failed_or_recovered_batch_count")),
                    str(trial.get("residue_count")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "This is a startup-only report. It does not claim EventTimeline runtime success.",
            "",
        ]
    )
    (report_dir / "full_s4_compose_startup_ab_report.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument(
        "--profiles",
        default="4:4,6:6,8:8,6:4",
        help="Comma-separated batch:compose_parallel profiles.",
    )
    parser.add_argument(
        "--run-prefix",
        default="full-s4-compose-startup-ab-20260622",
    )
    parser.add_argument("--compose-up-timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--container-readiness-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--compose-inter-stage-backoff-seconds", type=float, default=0.0)
    parser.add_argument(
        "--stop-after-fastest-stable",
        action="store_true",
        help="Stop once a stable profile is followed by an unstable higher-concurrency primary profile.",
    )
    parser.add_argument(
        "--summarize-existing-only",
        action="store_true",
        help="Summarize existing profile run directories without launching new trials.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_dir = args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    profiles = _parse_profiles(str(args.profiles))
    trials: List[Dict[str, Any]] = []
    selected: Dict[str, Any] | None = None
    for batch_size, compose_parallel in profiles:
        run_id = f"{args.run_prefix}-b{batch_size}-p{compose_parallel}"
        run_dir = args.run_root / run_id
        profile = {
            "batch_size": batch_size,
            "compose_parallel_limit": compose_parallel,
            "compose_parallel_limit_mechanism": "COMPOSE_PARALLEL_LIMIT",
            "vm_cpu_policy": "keep_12_vcpu_for_this_ab_matrix",
        }
        command = _build_runner_command(
            args=args,
            run_id=run_id,
            batch_size=batch_size,
            compose_parallel=compose_parallel,
        )
        if bool(args.summarize_existing_only):
            host_before = {}
            host_after = {}
            wall_seconds = 0.0
            completed = {
                "returncode": 0 if run_dir.exists() else 1,
                "stdout": "",
                "stderr": "summarize_existing_only_missing_run_dir"
                if not run_dir.exists()
                else "",
            }
        else:
            host_before = _host_snapshot()
            started_at = time.perf_counter()
            print(
                f"[startup-ab] starting profile batch={batch_size} "
                f"compose_parallel={compose_parallel}",
                flush=True,
            )
            completed = _run_streamed_command(command, cwd=REPO_ROOT, label=run_id)
            wall_seconds = time.perf_counter() - started_at
            host_after = _host_snapshot()
        trial = _summarize_run(
            run_dir=run_dir,
            profile=profile,
            command=command,
            returncode=int(completed["returncode"]),
            stdout=str(completed.get("stdout") or ""),
            stderr=str(completed.get("stderr") or ""),
            wall_seconds=wall_seconds,
            host_before=host_before,
            host_after=host_after,
        )
        trials.append(trial)
        if trial.get("stable_no_timeout") is True:
            if selected is None:
                selected = trial
            else:
                current = float(trial.get("compose_up_wall_seconds") or trial.get("wall_seconds") or 0.0)
                best = float(selected.get("compose_up_wall_seconds") or selected.get("wall_seconds") or 0.0)
                if current < best:
                    selected = trial
        report = {
            "schema_version": "skyseed-full-s4-compose-startup-ab-v1",
            "generated_at_utc": _timestamp_utc(),
            "scope": "startup_only_no_event_timeline_runtime",
            "vm_assumption": {
                "vcpus": 12,
                "ram_gib_approx": 22,
                "primary_hypothesis": "startup_concurrency_docker_compose_virtualbox_scheduling_overload",
                "not_primary_fix": "lower_vm_cpu_count",
            },
            "profiles_requested": [
                {"batch_size": batch, "compose_parallel_limit": parallel}
                for batch, parallel in profiles
            ],
            "selected_profile": None if selected is None else selected.get("profile"),
            "trials": trials,
        }
        _write_json(report_dir / "full_s4_compose_startup_ab_report.json", report)
        _write_markdown_report(report_dir, report)
        if (
            bool(args.stop_after_fastest_stable)
            and selected is not None
            and trial.get("stable_no_timeout") is not True
            and (batch_size, compose_parallel) != profiles[-1]
        ):
            break
    final_report = _read_json(report_dir / "full_s4_compose_startup_ab_report.json")
    print(json.dumps(final_report, indent=2, sort_keys=True))
    return 0 if final_report.get("selected_profile") else 1


if __name__ == "__main__":
    raise SystemExit(main())
