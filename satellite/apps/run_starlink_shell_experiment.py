#!/usr/bin/env python3
# encoding: utf-8

"""Run a preview or bounded real runtime Starlink shell experiment."""

from __future__ import annotations

import argparse
import math
import csv
import queue
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Sequence, Tuple

import yaml

from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()

from iteration2_satellite_experiment import (
    _await_initial_real_runtime_readiness,
    _connectivity_summary,
    _warmup_ut_ids_for_transition,
)
from linux_mpls_runtime import (
    DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE,
    MinimalRealLinuxMplsInstaller,
    NodeLocalBatchFailure,
    RuntimeCommandFailure,
)
from network_mutation import (
    build_network_mutation_plan,
    dynamic_slot_state_from_link_state,
    dynamic_slot_state_from_snapshot,
    load_link_state,
    make_runtime_parameter_mutation_policy,
)
from network_runtime import (
    MinimalRealNetworkMutationExecutor,
    dynamic_slot_binding_resource_key,
)
from run_starlink_shell_runtime_attempt import (
    _build_slice_contexts,
    _classify_compose_failure,
    _compose_down,
    _load_snapshots,
)
from mpls_host import HostMplsPreparationError, ensure_host_mpls_ready
from seed_integration import (
    EXTERNAL_TEST_TARGETS,
    MINI_INTERNET_IX_DISPLAY_NAMES,
    MINI_INTERNET_STUB_AS_EXCHANGES,
    MINI_INTERNET_TRANSIT_AS_LAYOUT,
)
from skyseed_preflight import (
    BRIDGE_NETFILTER_SYSCTLS,
    cleanup_direct_backend_host_artifacts,
    direct_backend_host_artifacts_report,
    run_skyseed_preflight,
)
from contracts import NetworkMutationPlan
from return_path_audit import audit_planner_preview
from temporal_planning import enrich_snapshot_runtime_parameters


DEFAULT_RUN_ROOT = SATELLITE_DIR / "output" / "starlink_shell_experiments"
DEFAULT_BUILD_CACHE_DIR = DEFAULT_RUN_ROOT / ".compose_build_cache"
DEADLINE_MISS_THRESHOLD_SECONDS = 0.05


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _compose_residue_summary(preflight_report: Dict[str, Any]) -> Dict[str, Any]:
    docker_state = dict(preflight_report.get("checks", {}).get("docker_state", {}))
    containers = dict(docker_state.get("containers", {}))
    networks = dict(docker_state.get("networks", {}))
    return {
        "compose_project_container_count": len(
            list(containers.get("compose_project_containers", []))
        ),
        "named_skyseed_container_count": len(
            list(containers.get("named_skyseed_containers", []))
        ),
        "compose_project_network_count": len(
            list(networks.get("compose_project_networks", []))
        ),
        "named_skyseed_network_count": len(
            list(networks.get("named_skyseed_networks", []))
        ),
        "compose_process_count": len(
            list(preflight_report.get("checks", {}).get("compose_processes", {}).get("processes", []))
        ),
        "direct_backend_host_interface_count": len(
            list(
                preflight_report.get("checks", {})
                .get("direct_backend_host_artifacts", {})
                .get("interfaces", [])
            )
        ),
    }


def _write_json(path: Path, payload: Dict[str, Any] | List[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_json_safe(payload), sort_keys=True) + "\n")


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in value)


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _current_preflight_ignore_pids() -> List[int]:
    """Ignore the current runner and its direct parent shell wrapper."""

    ignore_pids: List[int] = []
    for pid in (os.getpid(), os.getppid()):
        if pid > 0 and pid not in ignore_pids:
            ignore_pids.append(pid)
    return ignore_pids


def _sigterm_as_keyboard_interrupt(signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt(f"signal {signum}")


def _terminate_process_group(
    process: subprocess.Popen[str],
) -> Tuple[str | None, str | None]:
    if process.poll() is not None:
        return (None, None)
    try:
        os.killpg(process.pid, signal.SIGTERM)
        return process.communicate(timeout=5.0)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        return process.communicate()
    except ProcessLookupError:
        return process.communicate()


def _prepare_bridge_netfilter_passthrough() -> Dict[str, Any]:
    command_results: List[Dict[str, Any]] = []
    for key in BRIDGE_NETFILTER_SYSCTLS:
        completed = subprocess.run(
            ["sudo", "-n", "sysctl", "-w", f"{key}=0"],
            text=True,
            capture_output=True,
            check=False,
        )
        result = {
            "command": ["sudo", "-n", "sysctl", "-w", f"{key}=0"],
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "ok": completed.returncode == 0,
        }
        command_results.append(result)
        if completed.returncode != 0:
            raise RuntimeError(
                "bridge netfilter passthrough preparation failed: "
                + (completed.stderr.strip() or completed.stdout.strip() or key)
            )
    return {
        "prepared": True,
        "command_results": command_results,
    }


def _prepare_real_runtime_host_environment() -> Dict[str, Any]:
    host_mpls_summary = ensure_host_mpls_ready(auto_prepare=True)
    bridge_netfilter_summary = _prepare_bridge_netfilter_passthrough()
    return {
        "prepared": True,
        "host_mpls": _safe_dict(host_mpls_summary),
        "bridge_netfilter": _safe_dict(bridge_netfilter_summary),
    }


def _write_runtime_lock(path: Path, *, run_id: str, run_mode: str) -> None:
    _write_json(
        path,
        {
            "run_id": run_id,
            "run_mode": run_mode,
            "pid": os.getpid(),
            "started_at_utc": _timestamp_utc(),
        },
    )


def _operation_requires_host_reconcile(operation: Dict[str, Any]) -> bool:
    operation_name = str(operation.get("operation"))
    if operation_name in {
        "activate_slot_attachment",
        "deactivate_slot_attachment",
        "rebind_slot_attachment",
    }:
        return True
    if operation_name != "update_slot_parameters":
        return True
    return False


def _reconcile_slot_names_for_epoch(
    *,
    planned_operations: Sequence[Dict[str, Any]],
    effective_epoch_type: str,
) -> List[str] | None:
    if effective_epoch_type not in {"parameter_epoch", "runtime_epoch"}:
        return None
    if effective_epoch_type == "parameter_epoch":
        return sorted(
            {
                str(operation.get("slot_name"))
                for operation in planned_operations
                if str(operation.get("slot_name"))
            }
        )
    return sorted(
        {
            str(operation.get("slot_name"))
            for operation in planned_operations
            if str(operation.get("slot_name"))
            and _operation_requires_host_reconcile(operation)
        }
    )


def _should_run_sampled_validation(
    *,
    epoch_index: int,
    interval: int,
    always_first: bool,
) -> bool:
    if epoch_index <= 1:
        return bool(always_first)
    if int(interval) <= 0:
        return False
    return ((int(epoch_index) - 1) % int(interval)) == 0


def _skipped_connectivity_summary(
    *,
    slice_id: str,
    skip_reason: str,
) -> Dict[str, Any]:
    return {
        "slice_id": slice_id,
        "flow_results": [],
        "ping_success": True,
        "wall_seconds": 0.0,
        "attempts": [],
        "attempt_count": 0,
        "retry_recovered": False,
        "validation_skipped": True,
        "skip_reason": skip_reason,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a prepared Starlink shell experiment in preview-only or bounded real-runtime mode."
        )
    )
    parser.add_argument(
        "--run-mode",
        choices=("preview", "real-runtime"),
        default="preview",
        help="Preview-only build or bounded real-runtime execution.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional stable run id. Defaults to a timestamped generated id.",
    )
    parser.add_argument(
        "--run-root",
        default=str(DEFAULT_RUN_ROOT),
        help="Directory where run-specific artifacts and logs are written.",
    )
    parser.add_argument(
        "--orbit-input-mode",
        choices=("planned-shell", "real-catalog"),
        default="planned-shell",
        help="Orbit input mode forwarded to the Starlink preview builder.",
    )
    parser.add_argument(
        "--planned-shell-id",
        default="S5",
        help="Planned shell preset when --orbit-input-mode planned-shell is used.",
    )
    parser.add_argument(
        "--orbit-data",
        default=None,
        help="Local orbit data file for reproducible real-catalog runs.",
    )
    parser.add_argument(
        "--max-satellites",
        type=int,
        default=24,
        help="Requested shell size. Common values are 24, 48, 72, and 172.",
    )
    parser.add_argument(
        "--ut-catalog",
        default=None,
        help="Optional JSON file describing the UT catalog forwarded to the preview builder.",
    )
    parser.add_argument(
        "--gateway-catalog",
        default=None,
        help="Optional JSON file describing the gateway catalog forwarded to the preview builder.",
    )
    parser.add_argument(
        "--zone-catalog",
        default=None,
        help="Optional JSON file describing explicit geographic zone partitions forwarded to the preview builder.",
    )
    parser.add_argument(
        "--ut-ids",
        default="ut3,ut4",
        help="Comma-separated UT ids to emulate.",
    )
    parser.add_argument(
        "--gateway-ids",
        default="gw5,gw6,gw7",
        help="Comma-separated gateway ids to emulate.",
    )
    parser.add_argument(
        "--max-uts",
        type=int,
        default=2,
        help="Maximum UT count when explicit ids are not provided.",
    )
    parser.add_argument(
        "--max-gateways",
        type=int,
        default=3,
        help="Maximum gateway count when explicit ids are not provided.",
    )
    parser.add_argument(
        "--ground-placement",
        choices=("catalog", "shell-aware"),
        default="shell-aware",
        help="Ground placement forwarded to the preview builder.",
    )
    parser.add_argument(
        "--start-time",
        default=None,
        help="Propagation start time in ISO-8601 UTC.",
    )
    parser.add_argument(
        "--sample-interval",
        type=float,
        default=None,
        help="Observation interval in seconds for coarse reporting and queue comparison.",
    )
    parser.add_argument(
        "--event-evaluation-interval",
        type=float,
        default=None,
        help="Optional finer dense evaluation interval used to generate event-time runtime updates.",
    )
    parser.add_argument(
        "--horizon-seconds",
        type=float,
        default=None,
        help="Optional temporal planning horizon in seconds.",
    )
    parser.add_argument(
        "--step-seconds",
        type=float,
        default=120.0,
        help="Nominal step between slices when no separate sample interval is supplied.",
    )
    parser.add_argument(
        "--slice-count",
        type=int,
        default=3,
        help="Requested sample/slice count when no explicit horizon is supplied.",
    )
    parser.add_argument(
        "--max-runtime-epochs",
        type=int,
        default=None,
        help="Optional cap on how many RuntimeEpochs are executed in real-runtime mode.",
    )
    parser.add_argument(
        "--max-parameter-epochs",
        type=int,
        default=None,
        help="Optional cap on how many ParameterEpochs are executed in real-runtime mode.",
    )
    parser.add_argument(
        "--max-epochs",
        type=int,
        default=None,
        help="Optional cap on the combined prepared RuntimeEpoch/ParameterEpoch queue.",
    )
    parser.add_argument(
        "--parameter-max-interval",
        type=float,
        default=240.0,
        help="Maximum time between lightweight ParameterEpochs when drift is present.",
    )
    parser.add_argument(
        "--delay-threshold-ms",
        type=float,
        default=0.5,
        help="Absolute delay drift threshold for ParameterEpoch generation.",
    )
    parser.add_argument(
        "--loss-threshold-pct",
        type=float,
        default=0.02,
        help="Absolute loss drift threshold for ParameterEpoch generation.",
    )
    parser.add_argument(
        "--bandwidth-threshold-pct",
        type=float,
        default=5.0,
        help="Relative bandwidth drift threshold for ParameterEpoch generation.",
    )
    parser.add_argument(
        "--routing-cost-threshold",
        type=int,
        default=5,
        help="Absolute routing-cost drift threshold for ParameterEpoch generation.",
    )
    parser.add_argument(
        "--runtime-mode",
        choices=("correctness", "fast-demo"),
        default="fast-demo",
        help="Runtime mode forwarded to the real executor.",
    )
    parser.add_argument(
        "--loop-count",
        type=int,
        default=1,
        help="Continuous-real loop count.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="Pacing interval between executed RuntimeEpochs.",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Execute the temporal epoch queue with activation-time scheduling and live replenishment.",
    )
    parser.add_argument(
        "--replenish-threshold-seconds",
        type=float,
        default=120.0,
        help="When the remaining queued horizon drops below this threshold, precompute the next segment.",
    )
    parser.add_argument(
        "--strict-activation-deadlines",
        action="store_true",
        help="Treat activation deadline misses as runtime failures instead of only auditing them.",
    )
    parser.add_argument(
        "--max-node-workers",
        type=int,
        default=2,
        help="Bounded node-local worker count.",
    )
    parser.add_argument(
        "--max-host-workers",
        type=int,
        default=0,
        help=(
            "Bounded host-runtime worker count. "
            "Use 0 to auto-select a conflict-safe bounded default."
        ),
    )
    parser.add_argument(
        "--pre-transition-probe-interval",
        type=int,
        default=0,
        help=(
            "Run the pre-transition connectivity probe every N steady runtime epochs. "
            "Use 0 to disable sampled pre-transition probes and rely on post-apply/final checks."
        ),
    )
    parser.add_argument(
        "--runtime-epoch-validation-interval",
        type=int,
        default=0,
        help=(
            "Run full post-apply runtime-epoch connectivity validation every N runtime epochs. "
            "Use 0 to validate the first runtime epoch and the final comprehensive check only."
        ),
    )
    parser.add_argument(
        "--runtime-epoch-validation-scope",
        choices=(
            "all_active_flows",
            "reapplied_flows",
            DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE,
        ),
        default=DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE,
        help=(
            "Flow scope for sampled RuntimeEpoch post-apply ping validation."
        ),
    )
    parser.add_argument(
        "--parameter-epoch-validation-interval",
        type=int,
        default=0,
        help=(
            "Run targeted connectivity validation every N non-noop ParameterEpochs. "
            "Use 0 to validate the first live ParameterEpoch only."
        ),
    )
    parser.add_argument(
        "--observe-flow",
        action="append",
        default=[],
        help="Optional packet observation selector ut_id:target_host_node_id or ut_id:target_ip.",
    )
    parser.add_argument(
        "--ping-watch",
        action="append",
        default=[],
        help="Optional watcher selector ut_id:target_ip or ut_id:target_host_node_id.",
    )
    parser.add_argument(
        "--stream-progress",
        action="store_true",
        help="Forward per-transition progress markers from the real executor.",
    )
    parser.add_argument(
        "--progress-heartbeat-seconds",
        type=float,
        default=30.0,
        help=(
            "Low-frequency runtime heartbeat interval for progress files and stall diagnostics. "
            "This is observational only and does not interrupt the run."
        ),
    )
    parser.add_argument(
        "--startup-stall-warning-seconds",
        type=float,
        default=1800.0,
        help=(
            "Warn if a startup/runtime phase makes no high-level progress for this long. "
            "Default is intentionally above known-good 1-shell cold-start timings."
        ),
    )
    parser.add_argument(
        "--epoch-stall-warning-seconds",
        type=float,
        default=600.0,
        help=(
            "Warn if an active epoch phase makes no high-level progress for this long. "
            "This emits diagnostics but does not auto-abort the run."
        ),
    )
    parser.add_argument(
        "--keep-environment",
        action="store_true",
        help="Leave the docker-compose environment running after the experiment.",
    )
    parser.add_argument(
        "--reuse-running-compose-environment",
        action="store_true",
        help=(
            "If the compiled output digest matches and all required containers are "
            "already running, skip compose down/build/up and reuse the live base environment."
        ),
    )
    parser.add_argument(
        "--preflight-policy",
        choices=("off", "warn", "require-clean"),
        default="warn",
        help=(
            "Run the scoped Skyseed preflight before real-runtime work. "
            "'warn' records blockers but continues, 'require-clean' aborts on blockers, "
            "and 'off' skips the preflight."
        ),
    )
    parser.add_argument(
        "--host-prepare-policy",
        choices=("off", "auto"),
        default="auto",
        help=(
            "Prepare host MPLS and bridge-netfilter prerequisites before real-runtime work. "
            "'auto' is the durable default for real runs; 'off' skips host preparation."
        ),
    )
    parser.add_argument(
        "--compose-up-timeout-seconds",
        type=float,
        default=300.0,
        help=(
            "Overall startup deadline for docker-compose bring-up in bounded real-runtime mode. "
            "Set to 0 to disable only the overall cutoff; per-stage watchdog/recovery stays active."
        ),
    )
    parser.add_argument(
        "--compose-startup-mode",
        choices=("auto", "monolithic", "infra-then-sat-batches"),
        default="auto",
        help=(
            "How to start docker-compose services: one monolithic up, or fixed "
            "infrastructure first followed by satellite batches. 'auto' chooses "
            "a strategy from the compose footprint."
        ),
    )
    parser.add_argument(
        "--compose-satellite-batch-size",
        type=int,
        default=12,
        help="Satellite batch size when --compose-startup-mode=infra-then-sat-batches.",
    )
    parser.add_argument(
        "--compose-parallel-limit",
        type=int,
        default=0,
        help=(
            "Optional COMPOSE_PARALLEL_LIMIT for build/up commands. "
            "Set to 0 to leave docker-compose default behavior unchanged."
        ),
    )
    parser.add_argument(
        "--compose-build-policy",
        choices=("always", "if-needed", "never"),
        default="if-needed",
        help="Whether to build images every run, only when the compiled output changed, or never.",
    )
    parser.add_argument(
        "--compose-build-timeout-seconds",
        type=float,
        default=1200.0,
        help=(
            "Timeout for docker-compose build in bounded real-runtime mode. "
            "Set to 0 to disable hard build cutoffs and rely on monitoring."
        ),
    )
    parser.add_argument(
        "--container-readiness-timeout-seconds",
        type=float,
        default=120.0,
        help=(
            "Timeout for post-compose container readiness checks before runtime readiness probes. "
            "Set to 0 to wait indefinitely while monitoring progress."
        ),
    )
    parser.add_argument(
        "--stop-after-container-readiness",
        action="store_true",
        help=(
            "Bring the environment up and stop after container readiness without "
            "starting runtime readiness probes or live temporal execution."
        ),
    )
    parser.add_argument(
        "--mini-internet-node-prefix",
        default="starlinkexp-",
        help="Prefix for compiled Mini Internet nodes and containers.",
    )
    parser.add_argument(
        "--mini-internet-ix-offset",
        type=int,
        default=100,
        help="IX id offset for coexistence with other compiled environments.",
    )
    parser.add_argument(
        "--docker-dummy-network-pool",
        default="10.224.0.0/12",
        help="SeedEmu dummy Docker network pool for compiled output.",
    )
    parser.add_argument(
        "--docker-dummy-network-mask",
        type=int,
        default=24,
        help="SeedEmu dummy Docker network mask for compiled output.",
    )
    parser.add_argument(
        "--enable-seed-internet-map",
        action="store_true",
        help="Keep the SeedEmu internet map enabled for compiled output.",
    )
    parser.add_argument(
        "--preview-only-no-compile",
        action="store_true",
        help="In preview mode, skip Docker compiler output and only build JSON artifacts.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Render the runner summary as text or JSON.",
    )
    return parser.parse_args()


def _generated_run_id(args: argparse.Namespace) -> str:
    if args.run_id:
        return str(args.run_id)
    now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return (
        f"starlink_{_safe_name(str(args.run_mode))}_"
        f"{_safe_name(str(args.orbit_input_mode))}_"
        f"s{int(args.max_satellites)}_{now}"
    )


def _preview_command(
    *,
    args: argparse.Namespace,
    config_dir: Path,
    output_dir: Path,
    compile_seed_output: bool,
) -> List[str]:
    command = [
        sys.executable,
        str((SATELLITE_DIR / "apps" / "build_starlink_shell_preview.py").resolve()),
        "--orbit-input-mode",
        str(args.orbit_input_mode),
        "--planned-shell-id",
        str(args.planned_shell_id),
        "--max-satellites",
        str(args.max_satellites),
        "--ground-placement",
        str(args.ground_placement),
        "--step-seconds",
        str(args.step_seconds),
        "--slice-count",
        str(args.slice_count),
        "--parameter-max-interval",
        str(args.parameter_max_interval),
        "--delay-threshold-ms",
        str(args.delay_threshold_ms),
        "--loss-threshold-pct",
        str(args.loss_threshold_pct),
        "--bandwidth-threshold-pct",
        str(args.bandwidth_threshold_pct),
        "--routing-cost-threshold",
        str(args.routing_cost_threshold),
        "--config-dir",
        str(config_dir),
        "--output-dir",
        str(output_dir),
        "--mini-internet-node-prefix",
        str(args.mini_internet_node_prefix),
        "--mini-internet-ix-offset",
        str(args.mini_internet_ix_offset),
        "--docker-dummy-network-pool",
        str(args.docker_dummy_network_pool),
        "--docker-dummy-network-mask",
        str(args.docker_dummy_network_mask),
        "--format",
        "json",
    ]
    if str(args.ut_ids or "").strip():
        command.extend(["--ut-ids", str(args.ut_ids)])
    else:
        command.extend(["--max-uts", str(args.max_uts)])
    if str(args.gateway_ids or "").strip():
        command.extend(["--gateway-ids", str(args.gateway_ids)])
    else:
        command.extend(["--max-gateways", str(args.max_gateways)])
    if args.orbit_data:
        command.extend(["--orbit-data", str(args.orbit_data)])
    if args.ut_catalog:
        command.extend(["--ut-catalog", str(args.ut_catalog)])
    if args.gateway_catalog:
        command.extend(["--gateway-catalog", str(args.gateway_catalog)])
    if args.zone_catalog:
        command.extend(["--zone-catalog", str(args.zone_catalog)])
    if args.start_time:
        command.extend(["--start-time", str(args.start_time)])
    if args.sample_interval is not None:
        command.extend(["--sample-interval", str(args.sample_interval)])
    if args.event_evaluation_interval is not None:
        command.extend(
            ["--event-evaluation-interval", str(args.event_evaluation_interval)]
        )
    if args.horizon_seconds is not None:
        command.extend(["--horizon-seconds", str(args.horizon_seconds)])
    if not bool(args.enable_seed_internet_map):
        command.append("--disable-seed-internet-map")
    if compile_seed_output:
        command.append("--compile-seed-output")
    else:
        command.append("--skip-seed-render")
    return command


def _run_preview(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    config_dir: Path,
    output_dir: Path,
    events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    compile_seed_output = (
        args.run_mode == "real-runtime" or not bool(args.preview_only_no_compile)
    )
    preview_stdout = run_dir / "preview.stdout.json"
    preview_stderr = run_dir / "preview.stderr.log"
    command = _preview_command(
        args=args,
        config_dir=config_dir,
        output_dir=output_dir,
        compile_seed_output=compile_seed_output,
    )
    events.append(
        {
            "timestamp_utc": _timestamp_utc(),
            "event_type": "preview_started",
            "command": command,
            "compile_seed_output": compile_seed_output,
        }
    )
    started_at = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(SATELLITE_DIR.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    preview_stdout.write_text(completed.stdout, encoding="utf-8")
    preview_stderr.write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            "Starlink preview build failed; see "
            f"{preview_stderr} and {preview_stdout}"
        )
    summary = json.loads(completed.stdout)
    events.append(
        {
            "timestamp_utc": _timestamp_utc(),
            "event_type": "preview_completed",
            "wall_seconds": round(time.perf_counter() - started_at, 6),
            "selected_satellite_count": summary.get("selected_satellite_count"),
            "planner_valid": summary.get("planner_preview", {}).get("all_valid"),
            "orbit_sample_count": summary.get("temporal_plan", {}).get("orbit_sample_count"),
            "runtime_epoch_count": summary.get("temporal_plan", {}).get("runtime_epoch_count"),
            "parameter_epoch_count": summary.get("temporal_plan", {}).get("parameter_epoch_count"),
        }
    )
    return summary


def _hash_directory(path: Path) -> str:
    digest = hashlib.sha256()
    for file_path in sorted(entry for entry in path.rglob("*") if entry.is_file()):
        digest.update(str(file_path.relative_to(path)).encode("utf-8"))
        digest.update(b"\0")
        raw_bytes = file_path.read_bytes()
        normalized_bytes = raw_bytes
        if file_path.suffix == ".json":
            try:
                normalized_bytes = json.dumps(
                    json.loads(raw_bytes.decode("utf-8")),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            except Exception:
                normalized_bytes = raw_bytes
        elif file_path.suffix in {".yml", ".yaml"}:
            try:
                normalized_bytes = yaml.safe_dump(
                    yaml.safe_load(raw_bytes.decode("utf-8")),
                    sort_keys=True,
                    default_flow_style=False,
                ).encode("utf-8")
            except Exception:
                normalized_bytes = raw_bytes
        digest.update(normalized_bytes)
        digest.update(b"\0")
    return digest.hexdigest()


def _compose_project_name(output_dir: Path) -> str:
    return output_dir.name


def _build_cache_path(*, cache_dir: Path, output_dir: Path) -> Path:
    return cache_dir / f"{_compose_project_name(output_dir)}.json"


def _load_compose_build_cache(*, cache_dir: Path, output_dir: Path) -> Dict[str, Any] | None:
    cache_path = _build_cache_path(cache_dir=cache_dir, output_dir=output_dir)
    if not cache_path.exists():
        return None
    return _read_json(cache_path)


def _save_compose_build_cache(
    *,
    cache_dir: Path,
    output_dir: Path,
    payload: Dict[str, Any],
) -> None:
    cache_path = _build_cache_path(cache_dir=cache_dir, output_dir=output_dir)
    _write_json(cache_path, payload)


def _compose_service_metadata(output_dir: Path) -> Dict[str, Any]:
    compose_path = output_dir / "docker-compose.yml"
    payload = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = dict(payload.get("services", {}))
    networks = dict(payload.get("networks", {}))
    service_names = list(services)
    project_name = _compose_project_name(output_dir)
    service_image_tags: Dict[str, str] = {}
    container_names: List[str] = []
    service_name_by_container: Dict[str, str] = {}
    for service_name, service_payload in services.items():
        service_payload = dict(service_payload)
        image_tag = service_payload.get("image")
        if image_tag is None:
            image_tag = f"{project_name}_{service_name}"
        service_image_tags[str(service_name)] = str(image_tag)
        container_name = service_payload.get("container_name")
        if container_name:
            container_names.append(str(container_name))
            service_name_by_container[str(container_name)] = str(service_name)
        else:
            service_name_by_container[str(service_name)] = str(service_name)
    return {
        "compose_path": str(compose_path),
        "project_name": project_name,
        "service_count": len(service_names),
        "network_count": len(networks),
        "service_names": service_names,
        "service_image_tags": service_image_tags,
        "container_names": sorted(container_names),
        "service_name_by_container": service_name_by_container,
    }


def _compose_network_suffix(network_name: str) -> str:
    if network_name == "default":
        return "default"
    if network_name.startswith("net_ix_"):
        return network_name.removeprefix("net_ix_")
    if not network_name.startswith("net_"):
        return network_name
    remainder = network_name.removeprefix("net_")
    parts = remainder.split("_", 1)
    if len(parts) == 2 and parts[0].isdigit():
        return parts[1]
    return remainder


def _compose_service_category(
    *,
    service_name: str,
    service_payload: Dict[str, Any],
    satellite_ids: set[str],
    ut_ids: set[str],
    gateway_ids: set[str],
) -> str:
    container_name = str(service_payload.get("container_name") or service_name)
    if container_name in satellite_ids:
        return "satellite"
    if container_name in ut_ids:
        return "ut"
    if container_name in gateway_ids:
        return "gateway"
    if container_name.startswith("ix") and container_name[2:].isdigit():
        return "ix"
    if container_name.endswith("-placeholder-rtr"):
        return "helper"
    if container_name.startswith("starlinkexp-as"):
        return "mini_internet"
    if service_name.startswith(("brdnode_", "hnode_", "rs_ix_")):
        return "mini_internet"
    return "helper"


def _compose_network_category(
    *,
    network_name: str,
    ix_network_names: set[str],
    management_network_names: set[str],
    dynamic_network_names: set[str],
) -> str:
    suffix = _compose_network_suffix(network_name)
    if network_name == "default":
        return "default"
    if suffix in ix_network_names or network_name.startswith("net_ix_"):
        return "ix"
    if suffix in management_network_names:
        return "management_static_ground"
    if suffix in dynamic_network_names:
        return "dynamic_satellite_link"
    if network_name.startswith("net_"):
        return "mini_internet"
    return "other"


def _compose_audit(
    *,
    topology: Dict[str, Any],
    output_dir: Path,
) -> Dict[str, Any]:
    compose_path = output_dir / "docker-compose.yml"
    payload = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = dict(payload.get("services", {}))
    networks = dict(payload.get("networks", {}))
    satellites = {
        str(entry.get("satellite_id"))
        for entry in topology.get("satellites", [])
        if entry.get("satellite_id")
    }
    gateways = {
        str(entry.get("gateway_id"))
        for entry in topology.get("gateways", [])
        if entry.get("gateway_id")
    }
    uts = {
        str(entry.get("ut_id"))
        for entry in topology.get("user_terminals", [])
        if entry.get("ut_id")
    }
    ix_network_names = {
        str(entry.get("network_name"))
        for entry in topology.get("internet_exchanges", [])
        if entry.get("network_name")
    }
    management_network_names = {
        str(entry.get("bootstrap_network", {}).get("network_name"))
        for group in ("satellites", "gateways", "user_terminals")
        for entry in topology.get(group, [])
        if entry.get("bootstrap_network", {}).get("network_name")
    }
    slot_inventory = dict(topology.get("slot_inventory", {}))
    offloaded_dynamic_networks = {
        str(slot.get("network_name"))
        for slot_group in slot_inventory.values()
        for slot in slot_group
        if slot.get("network_name")
    }
    offloaded_by_intent = {
        "same_plane_isl": sorted(
            str(slot.get("network_name"))
            for slot in slot_inventory.get("same_plane_isl", [])
            if slot.get("network_name")
        ),
        "cross_plane_isl": sorted(
            str(slot.get("network_name"))
            for slot in slot_inventory.get("cross_plane_isl", [])
            if slot.get("network_name")
        ),
        "gateway_gsl": sorted(
            str(slot.get("network_name"))
            for slot in slot_inventory.get("gateway_access", [])
            if slot.get("network_name")
        ),
        "ut_access": sorted(
            str(slot.get("network_name"))
            for slot in slot_inventory.get("ut_access", [])
            if slot.get("network_name")
        ),
    }
    service_categories: Dict[str, List[str]] = {
        "satellite": [],
        "ut": [],
        "gateway": [],
        "mini_internet": [],
        "ix": [],
        "helper": [],
    }
    for service_name, service_payload in services.items():
        category = _compose_service_category(
            service_name=str(service_name),
            service_payload=dict(service_payload),
            satellite_ids=satellites,
            ut_ids=uts,
            gateway_ids=gateways,
        )
        service_categories[category].append(
            str(service_payload.get("container_name") or service_name)
        )
    service_attachment_count = 0
    for service_payload in services.values():
        service_networks = service_payload.get("networks")
        if service_networks is None:
            continue
        if isinstance(service_networks, dict):
            service_attachment_count += len(service_networks)
        elif isinstance(service_networks, list):
            service_attachment_count += len(service_networks)
        else:
            service_attachment_count += 1
    network_categories: Dict[str, List[str]] = {
        "default": [],
        "ix": [],
        "mini_internet": [],
        "management_static_ground": [],
        "dynamic_satellite_link": [],
        "other": [],
    }
    for network_name in sorted(networks):
        category = _compose_network_category(
            network_name=str(network_name),
            ix_network_names=ix_network_names,
            management_network_names=management_network_names,
            dynamic_network_names=offloaded_dynamic_networks,
        )
        network_categories[category].append(str(network_name))
    return {
        "compose_path": str(compose_path),
        "service_counts": {
            category: len(names)
            for category, names in service_categories.items()
        },
        "service_names_by_type": {
            category: sorted(names)
            for category, names in service_categories.items()
        },
        "network_counts": {
            category: len(names)
            for category, names in network_categories.items()
        },
        "network_names_by_type": {
            category: sorted(names)
            for category, names in network_categories.items()
        },
        "service_attachment_count": int(service_attachment_count),
        "required_seed_compose_network_types": [
            "default",
            "ix",
            "mini_internet",
            "management_static_ground",
        ],
        "offloaded_dynamic_link_intents": {
            intent: {
                "count": len(names),
                "network_names": names,
            }
            for intent, names in offloaded_by_intent.items()
        },
        "compiled_dynamic_satellite_network_leaks": sorted(
            network_categories["dynamic_satellite_link"]
        ),
    }


def _compose_audit_unavailable(*, output_dir: Path, reason: str) -> Dict[str, Any]:
    zero_service_categories = [
        "satellite",
        "ut",
        "gateway",
        "mini_internet",
        "ix",
        "helper",
    ]
    zero_network_categories = [
        "default",
        "ix",
        "mini_internet",
        "management_static_ground",
        "dynamic_satellite_link",
        "other",
    ]
    return {
        "compose_path": str(output_dir / "docker-compose.yml"),
        "available": False,
        "reason": str(reason),
        "service_counts": {category: 0 for category in zero_service_categories},
        "service_names_by_type": {category: [] for category in zero_service_categories},
        "network_counts": {category: 0 for category in zero_network_categories},
        "network_names_by_type": {category: [] for category in zero_network_categories},
        "service_attachment_count": 0,
        "required_seed_compose_network_types": [
            "default",
            "ix",
            "mini_internet",
            "management_static_ground",
        ],
        "offloaded_dynamic_link_intents": {
            "same_plane_isl": {"count": 0, "network_names": []},
            "cross_plane_isl": {"count": 0, "network_names": []},
            "gateway_gsl": {"count": 0, "network_names": []},
            "ut_access": {"count": 0, "network_names": []},
        },
        "compiled_dynamic_satellite_network_leaks": [],
    }


def _docker_image_exists(image_tag: str) -> bool:
    completed = subprocess.run(
        ["docker", "image", "inspect", image_tag],
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def _compose_startup_stages(
    *,
    compose_audit: Dict[str, Any],
    service_name_by_container: Dict[str, str],
    mode: str,
    satellite_batch_size: int,
) -> List[Dict[str, Any]]:
    if mode == "monolithic":
        return [
            {
                "stage_id": "all-services",
                "stage_type": "all",
                "service_names": [],
                "no_deps": False,
            }
        ]
    def to_service_names(container_names: Sequence[str]) -> List[str]:
        return [
            str(service_name_by_container.get(str(name), str(name)))
            for name in container_names
        ]

    helpers = to_service_names(compose_audit.get("service_names_by_type", {}).get("helper", []))
    ix_services = to_service_names(compose_audit.get("service_names_by_type", {}).get("ix", []))
    mini_internet_services = to_service_names(
        compose_audit.get("service_names_by_type", {}).get("mini_internet", [])
    )
    endpoint_services: List[str] = []
    for category in ("gateway", "ut"):
        endpoint_services.extend(
            to_service_names(
                compose_audit.get("service_names_by_type", {}).get(category, [])
            )
        )
    satellites = to_service_names(
        compose_audit.get("service_names_by_type", {}).get("satellite", [])
    )
    stages: List[Dict[str, Any]] = []
    if helpers:
        stages.append(
            {
                "stage_id": "helpers",
                "stage_type": "helper",
                "service_names": helpers,
                "no_deps": False,
            }
        )
    if ix_services:
        stages.append(
            {
                "stage_id": "ix-services",
                "stage_type": "ix",
                "service_names": ix_services,
                "no_deps": True,
            }
        )
    infra_batch_size = max(1, min(max(1, int(satellite_batch_size)), 6))
    for batch_index in range(0, len(mini_internet_services), infra_batch_size):
        batch = mini_internet_services[batch_index : batch_index + infra_batch_size]
        stages.append(
            {
                "stage_id": f"mini-internet-{(batch_index // infra_batch_size) + 1:02d}",
                "stage_type": "mini_internet_batch",
                "service_names": batch,
                "no_deps": True,
            }
        )
    if endpoint_services:
        stages.append(
            {
                "stage_id": "ground-endpoints",
                "stage_type": "endpoint",
                "service_names": endpoint_services,
                "no_deps": True,
            }
        )
    batch_size = max(1, int(satellite_batch_size))
    for batch_index in range(0, len(satellites), batch_size):
        batch = satellites[batch_index : batch_index + batch_size]
        stages.append(
            {
                "stage_id": f"satellites-{(batch_index // batch_size) + 1:02d}",
                "stage_type": "satellite_batch",
                "service_names": batch,
                "no_deps": True,
            }
        )
    return stages


def _resolve_compose_startup_strategy(
    *,
    requested_mode: str,
    compose_audit: Dict[str, Any],
    compose_environment_reuse_requested: bool,
) -> Dict[str, Any]:
    service_counts = dict(compose_audit.get("service_counts", {}))
    network_counts = dict(compose_audit.get("network_counts", {}))
    total_services = int(sum(int(value) for value in service_counts.values()))
    total_networks = int(sum(int(value) for value in network_counts.values()))
    attachment_count = int(compose_audit.get("service_attachment_count", 0))
    scale_rules = {
        "monolithic_service_count_max": 84,
        "monolithic_attachment_count_max": 108,
        "staged_service_count_min": 85,
        "staged_attachment_count_min": 109,
    }
    effective_mode = str(requested_mode)
    reason = "requested_explicitly"
    if requested_mode == "auto":
        if (
            total_services >= int(scale_rules["staged_service_count_min"])
            or attachment_count >= int(scale_rules["staged_attachment_count_min"])
        ):
            effective_mode = "infra-then-sat-batches"
            reason = "auto_selected_staged_for_large_compose_footprint"
        else:
            effective_mode = "monolithic"
            reason = "auto_selected_monolithic_for_small_compose_footprint"
    return {
        "requested_mode": str(requested_mode),
        "effective_mode": effective_mode,
        "reason": reason,
        "compose_environment_reuse_requested": bool(compose_environment_reuse_requested),
        "timeout_policy": {
            "stage_timeout_cap_seconds": {
                "helper": 45.0,
                "ix": 60.0,
                "mini_internet_batch": 60.0,
                "endpoint": 60.0,
                "satellite_batch": 90.0,
                "all": 300.0,
            },
            "recovery_timeout_cap_seconds": {
                "helper": 45.0,
                "ix": 60.0,
                "mini_internet_batch": 90.0,
                "endpoint": 90.0,
                "satellite_batch": 180.0,
                "all": 180.0,
            },
            "recovery_mode": "retry_missing_services_after_stage_timeout",
            "satellite_recovery_retry_unit": "single_service",
        },
        "footprint": {
            "service_count": total_services,
            "network_count": total_networks,
            "attachment_count": attachment_count,
            "service_counts": service_counts,
            "network_counts": network_counts,
        },
        "scale_rules": scale_rules,
    }


def _compose_service_state_snapshot(
    *,
    output_dir: Path,
    container_names: Sequence[str],
) -> Dict[str, Any]:
    compose_ps = subprocess.run(
        ["docker-compose", "ps", "-a"],
        cwd=str(output_dir),
        text=True,
        capture_output=True,
        check=False,
    )
    state_by_container: Dict[str, Dict[str, Any]] = {}
    state_counts: Dict[str, int] = {
        "running": 0,
        "created": 0,
        "exited": 0,
        "restarting": 0,
        "paused": 0,
        "missing": 0,
        "other": 0,
    }
    service_counts_by_state: Dict[str, Dict[str, int]] = {}
    for container_name in container_names:
        inspect = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .State}}",
                str(container_name),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if inspect.returncode != 0 or not inspect.stdout.strip():
            state = "missing"
            detail: Dict[str, Any] = {
                "state": state,
                "inspect_error": inspect.stderr.strip(),
            }
        else:
            raw_state = json.loads(inspect.stdout.strip())
            running = bool(raw_state.get("Running"))
            status = str(raw_state.get("Status") or "").strip().lower()
            if running or status == "running":
                state = "running"
            elif status == "created":
                state = "created"
            elif status == "restarting":
                state = "restarting"
            elif status == "paused":
                state = "paused"
            elif status == "exited":
                state = "exited"
            else:
                state = "other"
            detail = {
                "state": state,
                "status": status,
                "started_at": raw_state.get("StartedAt"),
                "finished_at": raw_state.get("FinishedAt"),
                "exit_code": raw_state.get("ExitCode"),
                "error": raw_state.get("Error"),
            }
        state_counts[state] = state_counts.get(state, 0) + 1
        service_type = "other"
        if container_name.startswith("stl-"):
            service_type = "satellite"
        elif container_name.startswith("ut"):
            service_type = "ut"
        elif container_name.startswith("gw"):
            service_type = "gateway"
        elif container_name.startswith("ix"):
            service_type = "ix"
        elif container_name.startswith("starlinkexp-as"):
            service_type = "mini_internet"
        elif container_name.startswith("seed_output_") or container_name.endswith("-placeholder-rtr"):
            service_type = "helper"
        service_counts_by_state.setdefault(state, {})
        service_counts_by_state[state][service_type] = (
            int(service_counts_by_state[state].get(service_type, 0)) + 1
        )
        state_by_container[str(container_name)] = detail
    return {
        "compose_ps_returncode": compose_ps.returncode,
        "compose_ps_stdout": compose_ps.stdout,
        "compose_ps_stderr": compose_ps.stderr,
        "state_counts": state_counts,
        "service_counts_by_state": service_counts_by_state,
        "containers": state_by_container,
    }


def _compose_stage_timeout_seconds(
    *,
    remaining_timeout: float,
    stage_type: str,
) -> float:
    timeout_cap_by_stage = {
        "helper": 45.0,
        "ix": 60.0,
        "mini_internet_batch": 60.0,
        "endpoint": 60.0,
        "satellite_batch": 90.0,
        "all": 300.0,
    }
    cap = float(timeout_cap_by_stage.get(stage_type, 90.0))
    if float(remaining_timeout) <= 0.0:
        return cap
    return max(1.0, min(float(remaining_timeout), cap))


def _compose_stage_recovery_timeout_seconds(*, stage_type: str) -> float:
    recovery_cap_by_stage = {
        "helper": 45.0,
        "ix": 60.0,
        "mini_internet_batch": 90.0,
        "endpoint": 90.0,
        "satellite_batch": 180.0,
        "all": 180.0,
    }
    return float(recovery_cap_by_stage.get(stage_type, 90.0))


def _compose_stage_retry_batches(
    *,
    service_names: Sequence[str],
    stage_type: str,
) -> List[List[str]]:
    if stage_type == "satellite_batch":
        return [[str(name)] for name in service_names]
    return [[str(name)] for name in service_names]


def _stage_running_service_names(
    *,
    compose_service_snapshot: Dict[str, Any],
    stage_services: Sequence[str],
    service_name_by_container: Dict[str, str],
) -> List[str]:
    running: List[str] = []
    stage_service_set = {str(name) for name in stage_services}
    for container_name, details in dict(
        compose_service_snapshot.get("containers", {})
    ).items():
        if str(details.get("state")) != "running":
            continue
        service_name = str(
            service_name_by_container.get(str(container_name), str(container_name))
        )
        if service_name in stage_service_set:
            running.append(service_name)
    return sorted(set(running))


def _should_run_compose_build(
    *,
    policy: str,
    output_digest: str,
    output_dir: Path,
    service_image_tags: Dict[str, str],
    cache_dir: Path,
) -> Tuple[bool, Dict[str, Any]]:
    cache_payload = _load_compose_build_cache(cache_dir=cache_dir, output_dir=output_dir)
    existing_images = {
        image_tag: _docker_image_exists(image_tag)
        for image_tag in sorted(set(service_image_tags.values()))
    }
    if policy == "always":
        return True, {
            "policy": policy,
            "cached_output_digest": None if cache_payload is None else cache_payload.get("output_digest"),
            "output_digest": output_digest,
            "image_presence": existing_images,
            "reason": "policy_always",
        }
    if policy == "never":
        return False, {
            "policy": policy,
            "cached_output_digest": None if cache_payload is None else cache_payload.get("output_digest"),
            "output_digest": output_digest,
            "image_presence": existing_images,
            "reason": "policy_never",
        }
    cached_digest = None if cache_payload is None else cache_payload.get("output_digest")
    all_images_present = all(existing_images.values())
    if cached_digest == output_digest and all_images_present:
        return False, {
            "policy": policy,
            "cached_output_digest": cached_digest,
            "output_digest": output_digest,
            "image_presence": existing_images,
            "reason": "cache_digest_match_and_images_present",
        }
    return True, {
        "policy": policy,
        "cached_output_digest": cached_digest,
        "output_digest": output_digest,
        "image_presence": existing_images,
        "reason": (
            "output_digest_changed"
            if cached_digest != output_digest
            else "missing_images"
        ),
    }


def _run_compose_command_with_timeout(
    *,
    output_dir: Path,
    command: Sequence[str],
    timeout_seconds: float,
    extra_env: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    started_at = time.perf_counter()
    env = os.environ.copy()
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            list(command),
            cwd=str(output_dir),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        if float(timeout_seconds) <= 0.0:
            stdout, stderr = process.communicate()
        else:
            stdout, stderr = process.communicate(timeout=float(timeout_seconds))
        return {
            "completed": subprocess.CompletedProcess(
                args=list(command),
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
            ),
            "timed_out": False,
            "wall_seconds": time.perf_counter() - started_at,
            "stdout": stdout,
            "stderr": stderr,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout
        stderr = exc.stderr
        if process is not None and process.poll() is None:
            terminated_stdout, terminated_stderr = _terminate_process_group(process)
            if terminated_stdout is not None:
                stdout = terminated_stdout
            if terminated_stderr is not None:
                stderr = terminated_stderr
        return {
            "completed": None,
            "timed_out": True,
            "wall_seconds": time.perf_counter() - started_at,
            "stdout": stdout,
            "stderr": stderr,
        }
    except BaseException:
        if process is not None and process.poll() is None:
            _terminate_process_group(process)
        raise


def _compose_up_with_timeout(
    *,
    output_dir: Path,
    timeout_seconds: float,
    services: Sequence[str] | None = None,
    no_deps: bool = False,
    compose_parallel_limit: int = 0,
) -> Dict[str, Any]:
    command = ["docker-compose", "up", "-d", "--no-build"]
    if no_deps:
        command.append("--no-deps")
    if services:
        command.extend(str(service) for service in services)
    return _run_compose_command_with_timeout(
        output_dir=output_dir,
        command=command,
        timeout_seconds=timeout_seconds,
        extra_env=(
            {"COMPOSE_PARALLEL_LIMIT": str(int(compose_parallel_limit))}
            if int(compose_parallel_limit) > 0
            else None
        ),
    )


def _compose_build_with_timeout(
    *,
    output_dir: Path,
    timeout_seconds: float,
    compose_parallel_limit: int = 0,
) -> Dict[str, Any]:
    return _run_compose_command_with_timeout(
        output_dir=output_dir,
        command=["docker-compose", "build"],
        timeout_seconds=timeout_seconds,
        extra_env=(
            {"COMPOSE_PARALLEL_LIMIT": str(int(compose_parallel_limit))}
            if int(compose_parallel_limit) > 0
            else None
        ),
    )


def _compose_force_remove_stale_networks(output_dir: Path) -> Dict[str, Any]:
    project_name = _compose_project_name(output_dir)
    list_result = subprocess.run(
        [
            "docker",
            "network",
            "ls",
            "--format",
            "{{.Name}}\t{{.Label \"com.docker.compose.project\"}}",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    candidate_networks: List[str] = []
    for line in list_result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        network_name, compose_project = parts[0].strip(), parts[1].strip()
        if network_name and compose_project == project_name:
            candidate_networks.append(network_name)
    disconnected_endpoints: List[Dict[str, str]] = []
    removed_networks: List[str] = []
    removal_failures: List[Dict[str, str]] = []
    for network_name in candidate_networks:
        inspect = subprocess.run(
            ["docker", "network", "inspect", network_name],
            text=True,
            capture_output=True,
            check=False,
        )
        if inspect.returncode == 0:
            try:
                payload = json.loads(inspect.stdout)
            except json.JSONDecodeError:
                payload = []
            containers = {}
            if payload:
                containers = dict(payload[0].get("Containers") or {})
            for endpoint in containers.values():
                endpoint_name = str(endpoint.get("Name") or "").strip()
                if not endpoint_name:
                    continue
                disconnect = subprocess.run(
                    ["docker", "network", "disconnect", "-f", network_name, endpoint_name],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                disconnected_endpoints.append(
                    {
                        "network_name": network_name,
                        "endpoint_name": endpoint_name,
                        "returncode": str(disconnect.returncode),
                    }
                )
        remove = subprocess.run(
            ["docker", "network", "rm", network_name],
            text=True,
            capture_output=True,
            check=False,
        )
        if remove.returncode == 0:
            removed_networks.append(network_name)
        else:
            removal_failures.append(
                {
                    "network_name": network_name,
                    "stderr": remove.stderr.strip(),
                }
            )
    return {
        "project_name": project_name,
        "candidate_network_count": len(candidate_networks),
        "candidate_networks": candidate_networks,
        "disconnected_endpoints": disconnected_endpoints,
        "removed_networks": removed_networks,
        "removal_failures": removal_failures,
    }


def _copy_artifact(src: Path, dst: Path) -> None:
    if src.resolve() == dst.resolve():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _load_temporal_artifacts(preview_summary: Dict[str, Any]) -> Dict[str, Any]:
    artifacts = dict(preview_summary.get("topology_artifacts", {}))
    planner_preview = _read_json(Path(artifacts["planner_preview_json"]).resolve())
    temporal_plan = _read_json(Path(artifacts["temporal_plan_json"]).resolve())
    snapshot_preview = _read_json(Path(artifacts["snapshot_preview_json"]).resolve())
    shell_manifest = _read_json(Path(artifacts["shell_manifest_json"]).resolve())
    return {
        "artifacts": artifacts,
        "planner_preview": planner_preview,
        "temporal_plan": temporal_plan,
        "snapshot_preview": snapshot_preview,
        "shell_manifest": shell_manifest,
    }


def _count_runtime_update_actions(runtime_update_plan: Dict[str, Any]) -> int:
    return sum(
        len(dict(subplan).get("actions", []))
        for subplan in dict(runtime_update_plan.get("node_subplans", {})).values()
    )


def _segment_context_keys_for_epoch_queue(
    *,
    segment_id: str,
    epoch_queue: Sequence[Dict[str, Any]],
) -> set[str]:
    keys: set[str] = set()
    for entry in epoch_queue:
        segment_key = str(entry.get("segment_epoch_key") or "").strip()
        if segment_key:
            keys.add(segment_key)
            continue
        slice_id = str(entry.get("slice_id") or "").strip()
        if slice_id:
            keys.add(f"{segment_id}:{slice_id}")
    return keys


def _temporal_execution_queue(
    temporal_plan: Dict[str, Any],
    *,
    max_epochs: int | None,
    max_runtime_epochs: int | None,
    max_parameter_epochs: int | None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str], List[str]]:
    if list(temporal_plan.get("event_queue", [])):
        orbit_samples = [dict(sample) for sample in list(temporal_plan.get("orbit_samples", []))]
        all_sample_frames: List[Dict[str, Any]] = []
        runtime_by_slice = {
            str(epoch.get("slice_id")): dict(epoch)
            for epoch in list(temporal_plan.get("runtime_epochs", []))
            if epoch.get("slice_id")
        }
        parameter_by_slice = {
            str(epoch.get("slice_id")): dict(epoch)
            for epoch in list(temporal_plan.get("parameter_epochs", []))
            if epoch.get("slice_id")
        }
        snapshot_refs_by_slice = {
            str(ref.get("slice_id")): dict(ref)
            for ref in list(temporal_plan.get("topology_snapshot_refs", []))
            if ref.get("slice_id")
        }
        for sample in orbit_samples:
            slice_id = str(sample.get("slice_id"))
            runtime_epoch = runtime_by_slice.get(slice_id)
            parameter_epoch = parameter_by_slice.get(slice_id)
            snapshot_ref = snapshot_refs_by_slice.get(slice_id, {})
            if runtime_epoch is not None:
                classification = "runtime_epoch"
                epoch_payload = runtime_epoch
            elif parameter_epoch is not None:
                classification = "parameter_epoch"
                epoch_payload = parameter_epoch
            else:
                classification = "no_op"
                epoch_payload = {}
            all_sample_frames.append(
                {
                    "sample_id": sample.get("sample_id"),
                    "sample_index": sample.get("sample_index"),
                    "slice_id": slice_id,
                    "snapshot_id": snapshot_ref.get("snapshot_id"),
                    "classification": classification,
                    "relative_time_seconds": sample.get("relative_time_seconds"),
                    "absolute_time_utc": sample.get("absolute_time_utc"),
                    "epoch_id": epoch_payload.get("epoch_id"),
                    "epoch_type": classification,
                    "activation_relative_time_seconds": epoch_payload.get(
                        "activation_relative_time_seconds",
                        sample.get("relative_time_seconds"),
                    ),
                    "activation_time_utc": epoch_payload.get(
                        "activation_time_utc",
                        sample.get("absolute_time_utc"),
                    ),
                    "activation_group_id": epoch_payload.get("activation_group_id"),
                    "execution_policy": epoch_payload.get("deadline_policy")
                    or epoch_payload.get("update_policy"),
                    "trigger_reasons": list(epoch_payload.get("trigger_reasons", [])),
                    "active_link_count": epoch_payload.get("active_link_count"),
                    "changed_link_count": len(list(epoch_payload.get("changed_link_ids", []))),
                }
            )
        executable_queue = [
            {
                "sample_id": event.get("source_sample_id"),
                "sample_index": None,
                "slice_id": event.get("slice_id"),
                "snapshot_id": event.get("snapshot_id"),
                "classification": event.get("event_type"),
                "relative_time_seconds": event.get("activation_relative_time_seconds"),
                "absolute_time_utc": event.get("activation_time_utc"),
                "epoch_id": event.get("linked_epoch_id") or event.get("event_id"),
                "epoch_type": event.get("event_type"),
                "activation_relative_time_seconds": event.get("activation_relative_time_seconds"),
                "activation_time_utc": event.get("activation_time_utc"),
                "activation_group_id": event.get("activation_group_id"),
                "execution_policy": event.get("execution_policy"),
                "trigger_reasons": list(event.get("trigger_reasons", [])),
                "active_link_count": dict(event.get("metadata", {})).get("active_link_count"),
                "changed_link_count": dict(event.get("metadata", {})).get("changed_link_count", 0),
            }
            for event in list(temporal_plan.get("event_queue", []))
            if str(event.get("event_type")) in {"runtime_epoch", "parameter_epoch"}
        ]
        selected_queue: List[Dict[str, Any]] = []
        runtime_count = 0
        parameter_count = 0
        for entry in executable_queue:
            if max_epochs is not None and len(selected_queue) >= max(0, int(max_epochs)):
                break
            classification = str(entry.get("classification"))
            if classification == "runtime_epoch":
                if (
                    max_runtime_epochs is not None
                    and runtime_count >= max(0, int(max_runtime_epochs))
                ):
                    continue
                runtime_count += 1
            elif classification == "parameter_epoch":
                if (
                    max_parameter_epochs is not None
                    and parameter_count >= max(0, int(max_parameter_epochs))
                ):
                    continue
                parameter_count += 1
            selected_queue.append(dict(entry))
        selected_runtime_slice_ids = [
            str(entry.get("slice_id"))
            for entry in selected_queue
            if str(entry.get("classification")) == "runtime_epoch"
        ]
        selected_parameter_slice_ids = [
            str(entry.get("slice_id"))
            for entry in selected_queue
            if str(entry.get("classification")) == "parameter_epoch"
        ]
        return all_sample_frames, selected_queue, selected_runtime_slice_ids, selected_parameter_slice_ids

    runtime_epochs_by_slice = {
        str(epoch.get("slice_id")): dict(epoch)
        for epoch in list(temporal_plan.get("runtime_epochs", []))
        if epoch.get("slice_id")
    }
    parameter_epochs_by_slice = {
        str(epoch.get("slice_id")): dict(epoch)
        for epoch in list(temporal_plan.get("parameter_epochs", []))
        if epoch.get("slice_id")
    }
    snapshot_refs_by_slice = {
        str(ref.get("slice_id")): dict(ref)
        for ref in list(temporal_plan.get("topology_snapshot_refs", []))
        if ref.get("slice_id")
    }
    all_sample_frames: List[Dict[str, Any]] = []
    executable_queue: List[Dict[str, Any]] = []
    for sample in list(temporal_plan.get("orbit_samples", [])):
        sample = dict(sample)
        slice_id = str(sample.get("slice_id"))
        runtime_epoch = runtime_epochs_by_slice.get(slice_id)
        parameter_epoch = parameter_epochs_by_slice.get(slice_id)
        snapshot_ref = snapshot_refs_by_slice.get(slice_id, {})
        if runtime_epoch is not None:
            epoch_type = "runtime_epoch"
            epoch_payload = runtime_epoch
        elif parameter_epoch is not None:
            epoch_type = "parameter_epoch"
            epoch_payload = parameter_epoch
        else:
            epoch_type = "no_op"
            epoch_payload = {}
        frame = {
            "sample_id": sample.get("sample_id"),
            "sample_index": sample.get("sample_index"),
            "slice_id": slice_id,
            "snapshot_id": snapshot_ref.get("snapshot_id"),
            "classification": epoch_type,
            "relative_time_seconds": sample.get("relative_time_seconds"),
            "absolute_time_utc": sample.get("absolute_time_utc"),
            "epoch_id": epoch_payload.get("epoch_id"),
            "epoch_type": epoch_type,
            "activation_relative_time_seconds": epoch_payload.get(
                "activation_relative_time_seconds",
                sample.get("relative_time_seconds"),
            ),
            "activation_time_utc": epoch_payload.get(
                "activation_time_utc",
                sample.get("absolute_time_utc"),
            ),
            "activation_group_id": epoch_payload.get("activation_group_id"),
            "execution_policy": epoch_payload.get("deadline_policy")
            or epoch_payload.get("update_policy"),
            "trigger_reasons": list(epoch_payload.get("trigger_reasons", [])),
            "active_link_count": epoch_payload.get("active_link_count"),
            "changed_link_count": len(list(epoch_payload.get("changed_link_ids", []))),
        }
        all_sample_frames.append(frame)
        if epoch_type != "no_op":
            executable_queue.append(dict(frame))
    selected_queue: List[Dict[str, Any]] = []
    runtime_count = 0
    parameter_count = 0
    for entry in executable_queue:
        if max_epochs is not None and len(selected_queue) >= max(0, int(max_epochs)):
            break
        classification = str(entry.get("classification"))
        if classification == "runtime_epoch":
            if (
                max_runtime_epochs is not None
                and runtime_count >= max(0, int(max_runtime_epochs))
            ):
                continue
            runtime_count += 1
        elif classification == "parameter_epoch":
            if (
                max_parameter_epochs is not None
                and parameter_count >= max(0, int(max_parameter_epochs))
            ):
                continue
            parameter_count += 1
        selected_queue.append(dict(entry))
    selected_runtime_slice_ids = [
        str(entry.get("slice_id"))
        for entry in selected_queue
        if str(entry.get("classification")) == "runtime_epoch"
    ]
    selected_parameter_slice_ids = [
        str(entry.get("slice_id"))
        for entry in selected_queue
        if str(entry.get("classification")) == "parameter_epoch"
    ]
    return all_sample_frames, selected_queue, selected_runtime_slice_ids, selected_parameter_slice_ids


def _observation_frame_summary(temporal_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "frame_id": frame.get("frame_id"),
            "sample_id": frame.get("sample_id"),
            "slice_id": frame.get("slice_id"),
            "snapshot_id": frame.get("snapshot_id"),
            "classification": frame.get("classification"),
            "active_link_count": frame.get("active_link_count"),
            "active_serving_satellite_ids": dict(frame.get("active_serving_satellite_ids", {})),
            "gateway_exit_summary": dict(frame.get("gateway_exit_summary", {})),
            "flow_path_count": len(list(frame.get("flow_path_summaries", []))),
        }
        for frame in list(temporal_plan.get("observation_frames", []))
    ]


def _filter_snapshots_by_slice_ids(
    snapshots: Sequence[Any],
    selected_slice_ids: Sequence[str],
) -> List[Any]:
    selected = set(str(slice_id) for slice_id in selected_slice_ids)
    return [
        snapshot
        for snapshot in snapshots
        if str(snapshot.time_slice.get("slice_id")) in selected
    ]


def _build_plan_summaries(
    *,
    planner_preview: Dict[str, Any],
    temporal_plan: Dict[str, Any],
    selected_runtime_slice_ids: Sequence[str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    selected_set = set(str(slice_id) for slice_id in selected_runtime_slice_ids)
    runtime_epochs_by_slice = {
        str(epoch.get("slice_id")): dict(epoch)
        for epoch in list(temporal_plan.get("runtime_epochs", []))
    }
    runtime_update_plan_summary: List[Dict[str, Any]] = []
    network_mutation_plan_summary: List[Dict[str, Any]] = []
    runtime_epoch_summary: List[Dict[str, Any]] = []
    for snapshot_record in list(planner_preview.get("snapshots", [])):
        slice_id = str(snapshot_record.get("slice_id") or snapshot_record.get("time_slice", {}).get("slice_id"))
        if slice_id not in selected_set:
            continue
        runtime_update_plan = dict(snapshot_record.get("runtime_update_plan", {}))
        mutation_summary = dict(snapshot_record.get("network_mutation_plan_summary", {}))
        runtime_epoch = runtime_epochs_by_slice.get(slice_id, {})
        runtime_update_plan_summary.append(
            {
                "slice_id": slice_id,
                "snapshot_id": snapshot_record.get("snapshot_id"),
                "runtime_update_action_count": _count_runtime_update_actions(runtime_update_plan),
                "node_subplan_count": len(dict(runtime_update_plan.get("node_subplans", {}))),
                "node_action_counts": {
                    str(node_id): len(dict(subplan).get("actions", []))
                    for node_id, subplan in sorted(
                        dict(runtime_update_plan.get("node_subplans", {})).items()
                    )
                },
            }
        )
        network_mutation_plan_summary.append(
            {
                "slice_id": slice_id,
                "snapshot_id": snapshot_record.get("snapshot_id"),
                "mutation_id": mutation_summary.get("mutation_id"),
                "operation_count": mutation_summary.get("operation_count"),
                "slot_names": list(mutation_summary.get("slot_names", [])),
                "trigger_reasons": list(runtime_epoch.get("trigger_reasons", [])),
                "operation_breakdown": dict(runtime_epoch.get("network_mutation_operation_counts", {})),
            }
        )
        runtime_epoch_summary.append(
            {
                "slice_id": slice_id,
                "snapshot_id": snapshot_record.get("snapshot_id"),
                "activation_relative_time_seconds": runtime_epoch.get(
                    "activation_relative_time_seconds"
                ),
                "activation_time_utc": runtime_epoch.get("activation_time_utc"),
                "activation_group_id": runtime_epoch.get("activation_group_id"),
                "deadline_policy": runtime_epoch.get("deadline_policy"),
                "trigger_reasons": list(runtime_epoch.get("trigger_reasons", [])),
                "samples_since_previous_runtime_epoch": runtime_epoch.get(
                    "samples_since_previous_runtime_epoch"
                ),
                "active_link_count": runtime_epoch.get("active_link_count"),
                "changed_link_count": len(list(runtime_epoch.get("changed_link_ids", []))),
                "network_mutation_operation_count": runtime_epoch.get(
                    "network_mutation_operation_count"
                ),
                "runtime_update_action_count": _count_runtime_update_actions(runtime_update_plan),
            }
        )
    return runtime_update_plan_summary, network_mutation_plan_summary, runtime_epoch_summary


def _parameter_epoch_summary(temporal_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "slice_id": epoch.get("slice_id"),
            "snapshot_id": epoch.get("snapshot_id"),
            "activation_relative_time_seconds": epoch.get(
                "activation_relative_time_seconds"
            ),
            "activation_time_utc": epoch.get("activation_time_utc"),
            "activation_group_id": epoch.get("activation_group_id"),
            "update_policy": epoch.get("update_policy"),
            "trigger_reasons": list(epoch.get("trigger_reasons", [])),
            "samples_since_previous_parameter_epoch": epoch.get(
                "samples_since_previous_parameter_epoch"
            ),
            "active_link_count": epoch.get("active_link_count"),
            "changed_link_count": len(list(epoch.get("changed_link_ids", []))),
            "changed_parameter_counts": dict(epoch.get("changed_parameter_counts", {})),
        }
        for epoch in list(temporal_plan.get("parameter_epochs", []))
    ]


def _resource_budget_summary(temporal_plan: Dict[str, Any]) -> Dict[str, Any]:
    return dict(temporal_plan.get("resource_budget", {}))


def _mini_internet_summary(
    *,
    topology: Dict[str, Any],
    compose_audit: Dict[str, Any],
) -> Dict[str, Any]:
    mini_internet = dict(topology.get("mini_internet", {}))
    selected_transits = [dict(entry) for entry in mini_internet.get("transit_ases", [])]
    selected_stubs = [dict(entry) for entry in mini_internet.get("stub_ases", [])]
    selected_transit_asns = {
        int(entry["asn"])
        for entry in selected_transits
        if entry.get("asn") is not None
    }
    selected_stub_asns = {
        int(entry["asn"])
        for entry in selected_stubs
        if entry.get("asn") is not None
    }
    selected_ix_ids = sorted(
        {
            int(ix_id)
            for entry in selected_transits
            for ix_id in entry.get("exchange_ids", [])
        }
        | {
            int(entry["exchange_id"])
            for entry in selected_stubs
            if entry.get("exchange_id") is not None
        }
    )
    selected_external_targets = [
        dict(entry)
        for entry in mini_internet.get("external_test_targets", [])
    ]
    selected_target_ids = {
        str(entry.get("node_id"))
        for entry in selected_external_targets
        if entry.get("node_id")
    }
    removed_external_targets = [
        dict(entry)
        for entry in EXTERNAL_TEST_TARGETS
        if str(entry.get("node_id")) not in selected_target_ids
    ]
    return {
        "generation_model": "seed_generated_mini_internet_subset",
        "is_seed_generated": True,
        "is_manual_toy_subset": False,
        "included_ixs": [
            {
                "ix_id": int(ix_id),
                "display_name": str(MINI_INTERNET_IX_DISPLAY_NAMES.get(int(ix_id), f"IX-{ix_id}")),
            }
            for ix_id in selected_ix_ids
        ],
        "included_transit_ases": selected_transits,
        "included_stub_ases": selected_stubs,
        "removed_transit_asns": sorted(
            int(asn)
            for asn in MINI_INTERNET_TRANSIT_AS_LAYOUT
            if int(asn) not in selected_transit_asns
        ),
        "removed_stub_asns": sorted(
            int(asn)
            for asn in MINI_INTERNET_STUB_AS_EXCHANGES
            if int(asn) not in selected_stub_asns
        ),
        "gateway_ix_peerings": [
            dict(entry)
            for entry in mini_internet.get("gateway_ix_peerings", [])
        ],
        "included_external_test_targets": selected_external_targets,
        "removed_external_test_targets": removed_external_targets,
        "compose_footprint": {
            "ix_service_count": int(compose_audit.get("service_counts", {}).get("ix", 0)),
            "mini_internet_service_count": int(
                compose_audit.get("service_counts", {}).get("mini_internet", 0)
            ),
            "ix_network_count": int(compose_audit.get("network_counts", {}).get("ix", 0)),
            "mini_internet_network_count": int(
                compose_audit.get("network_counts", {}).get("mini_internet", 0)
            ),
        },
    }


def _data_provenance_summary(
    *,
    preview_summary: Dict[str, Any],
    shell_manifest: Dict[str, Any],
    mini_internet_summary: Dict[str, Any],
) -> Dict[str, Any]:
    orbit_source = dict(preview_summary.get("orbit_data_source", {}))
    shell_provenance = dict(shell_manifest.get("data_provenance", {}))
    ground_provenance = dict(shell_provenance.get("ground", {}))
    return {
        "orbit": {
            "orbit_mode": preview_summary.get("orbit_input_mode"),
            "shell_preset": (
                str(preview_summary.get("orbit_source", "")).removeprefix("starlink_phase1_")
                if preview_summary.get("orbit_input_mode") == "planned-shell"
                else None
            ),
            "position_generation_model": preview_summary.get("orbit_input_kind"),
            "source_name": orbit_source.get("source_name"),
            "source_url": orbit_source.get("source_url"),
            "source_path": orbit_source.get("cache_path"),
            "source_checksum": orbit_source.get("sha256"),
            "epoch_range_utc": {
                "min": preview_summary.get("record_summary", {}).get("epoch_utc_min"),
                "max": preview_summary.get("record_summary", {}).get("epoch_utc_max"),
            },
        },
        "ground": {
            "ut_coordinates": list(ground_provenance.get("ut_coordinates", [])),
            "gateway_coordinates": list(ground_provenance.get("gateway_coordinates", [])),
        },
        "mini_internet": mini_internet_summary,
    }


def _continuous_operation_summary(
    *,
    temporal_plan: Dict[str, Any],
    all_sample_frames: Sequence[Dict[str, Any]],
    selected_epoch_queue: Sequence[Dict[str, Any]],
    selected_runtime_slice_ids: Sequence[str],
    selected_parameter_slice_ids: Sequence[str],
) -> Dict[str, Any]:
    preparation_plan = dict(temporal_plan.get("preparation_plan", {}))
    return {
        "mode": "initial_horizon_precomputed_queue",
        "rolling_horizon_replenishment": dict(
            preparation_plan.get("continuous_operation_prepared", {})
        ).get("rolling_horizon_replenishment", "prepared_not_yet_live"),
        "all_sample_frame_count": len(all_sample_frames),
        "prepared_epoch_queue_count": len(list(selected_epoch_queue)),
        "prepared_runtime_epoch_count": len(list(selected_runtime_slice_ids)),
        "prepared_parameter_epoch_count": len(list(selected_parameter_slice_ids)),
        "real_runtime_execution_model": (
            "runtime_epochs_execute_now; parameter_epochs_prepared_and_reported_only"
        ),
        "batching_policy": dict(preparation_plan.get("continuous_operation_prepared", {})).get(
            "runtime_epoch_batching_policy",
            "same_activation_group_only",
        ),
    }


def _namespace_with_overrides(
    args: argparse.Namespace,
    **overrides: Any,
) -> argparse.Namespace:
    payload = vars(args).copy()
    payload.update(overrides)
    return argparse.Namespace(**payload)


def _parse_utc_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _format_utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sample_interval_seconds(
    *,
    args: argparse.Namespace,
    temporal_plan: Dict[str, Any] | None = None,
) -> float:
    if args.sample_interval is not None:
        return float(args.sample_interval)
    if temporal_plan is not None:
        policy = dict(temporal_plan.get("policy", {}))
        if policy.get("sample_interval_seconds") is not None:
            return max(0.001, float(policy.get("sample_interval_seconds")))
        samples = list(temporal_plan.get("orbit_samples", []))
        if len(samples) >= 2:
            return max(
                0.001,
                float(samples[1].get("relative_time_seconds", 0.0))
                - float(samples[0].get("relative_time_seconds", 0.0)),
            )
    return max(0.001, float(args.step_seconds))


def _resolved_max_host_workers(
    *,
    args: argparse.Namespace,
) -> int:
    requested_workers = int(getattr(args, "max_host_workers", 0))
    if requested_workers > 0:
        return requested_workers
    cpu_total = os.cpu_count() or 1
    # Keep the default deliberately bounded. The host-runtime manager still
    # enforces conflict-safe grouping per operation family and resource owner.
    if cpu_total <= 1:
        return 1
    # On the 2-vCPU research VM, a second host worker can push the kernel and
    # Docker into heavy contention once the 1-shell runtime is fully up.
    if cpu_total <= 2:
        return 1
    if cpu_total <= 4:
        return 2
    return max(2, min(4, cpu_total // 2))


def _requested_horizon_seconds(
    *,
    args: argparse.Namespace,
    temporal_plan: Dict[str, Any] | None = None,
) -> float:
    if args.horizon_seconds is not None:
        return max(0.0, float(args.horizon_seconds))
    if temporal_plan is not None:
        samples = list(temporal_plan.get("orbit_samples", []))
        if len(samples) >= 2:
            return max(
                0.0,
                float(samples[-1].get("relative_time_seconds", 0.0))
                - float(samples[0].get("relative_time_seconds", 0.0)),
            )
    sample_interval = _sample_interval_seconds(args=args, temporal_plan=temporal_plan)
    return max(0.0, sample_interval * max(0, int(args.slice_count) - 1))


def _initial_continuous_horizon_seconds(
    *,
    args: argparse.Namespace,
) -> float | None:
    total_horizon = _requested_horizon_seconds(args=args)
    if total_horizon <= 0.0:
        return None
    sample_interval = _sample_interval_seconds(args=args)
    replenish_threshold = max(sample_interval, float(args.replenish_threshold_seconds))
    segment_horizon = max(sample_interval * 4.0, replenish_threshold * 2.0)
    return min(total_horizon, segment_horizon)


def _preview_summary_to_segment(
    *,
    segment_id: str,
    preview_summary: Dict[str, Any],
    temporal_artifacts: Dict[str, Any],
    topology_path: Path,
    link_state_path: Path,
    run_start_time_utc: str | None,
    max_epochs: int | None,
    max_runtime_epochs: int | None,
    max_parameter_epochs: int | None,
) -> Dict[str, Any]:
    temporal_plan = dict(temporal_artifacts["temporal_plan"])
    all_sample_frames, selected_epoch_queue, selected_runtime_slice_ids, selected_parameter_slice_ids = (
        _temporal_execution_queue(
            temporal_plan,
            max_epochs=max_epochs,
            max_runtime_epochs=max_runtime_epochs,
            max_parameter_epochs=max_parameter_epochs,
        )
    )
    selected_slice_ids = [
        str(entry.get("slice_id"))
        for entry in selected_epoch_queue
    ]
    snapshot_preview_json = Path(temporal_artifacts["artifacts"]["snapshot_preview_json"]).resolve()
    all_snapshots = _load_snapshots(snapshot_preview_json)
    execution_snapshots = _filter_snapshots_by_slice_ids(all_snapshots, selected_slice_ids)
    slice_contexts = _build_slice_contexts(
        topology_path=topology_path,
        link_state_path=link_state_path,
        snapshots=execution_snapshots,
    )
    context_by_slice = {
        str(context["snapshot"].time_slice.get("slice_id")): context
        for context in slice_contexts
    }
    queue_with_segment = []
    for entry in selected_epoch_queue:
        payload = dict(entry)
        if run_start_time_utc and payload.get("activation_time_utc"):
            payload["activation_relative_time_seconds"] = round(
                (
                    _parse_utc_timestamp(str(payload.get("activation_time_utc")))
                    - _parse_utc_timestamp(run_start_time_utc)
                ).total_seconds(),
                6,
            )
            payload["relative_time_seconds"] = payload["activation_relative_time_seconds"]
        payload["segment_id"] = segment_id
        payload["segment_epoch_key"] = f"{segment_id}:{payload.get('slice_id')}"
        queue_with_segment.append(payload)
    orbit_samples = list(temporal_plan.get("orbit_samples", []))
    segment_start_time_utc = (
        str(orbit_samples[0].get("absolute_time_utc"))
        if orbit_samples
        else None
    )
    segment_end_time_utc = (
        str(orbit_samples[-1].get("absolute_time_utc"))
        if orbit_samples
        else None
    )
    return {
        "segment_id": segment_id,
        "preview_summary": preview_summary,
        "temporal_plan": temporal_plan,
        "all_sample_frames": all_sample_frames,
        "selected_epoch_queue": queue_with_segment,
        "selected_runtime_slice_ids": selected_runtime_slice_ids,
        "selected_parameter_slice_ids": selected_parameter_slice_ids,
        "context_by_slice": context_by_slice,
        "selected_slice_ids": selected_slice_ids,
        "start_time_utc": segment_start_time_utc,
        "end_time_utc": segment_end_time_utc,
        "horizon_seconds": float(
            preview_summary.get("temporal_plan", {}).get("horizon_seconds", 0.0)
        ),
        "summary": {
            "orbit_sample_count": len(orbit_samples),
            "runtime_epoch_count": len(selected_runtime_slice_ids),
            "parameter_epoch_count": len(selected_parameter_slice_ids),
            "no_op_sample_count": int(temporal_plan.get("no_op_sample_count", 0)),
        },
    }


def _run_replenishment_preview(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    segment_index: int,
    start_time_utc: str,
    horizon_seconds: float,
    topology_path: Path,
    link_state_path: Path,
    run_start_time_utc: str,
) -> Dict[str, Any]:
    segment_dir = run_dir / "replenished_segments" / f"segment_{segment_index:03d}"
    config_dir = segment_dir / "config"
    output_dir = segment_dir / "preview_only"
    segment_args = _namespace_with_overrides(
        args,
        run_mode="preview",
        start_time=start_time_utc,
        horizon_seconds=float(horizon_seconds),
        slice_count=max(
            1,
            int(math.floor(float(horizon_seconds) / _sample_interval_seconds(args=args))) + 1,
        ),
    )
    command = _preview_command(
        args=segment_args,
        config_dir=config_dir,
        output_dir=output_dir,
        compile_seed_output=False,
    )
    completed = subprocess.run(
        command,
        cwd=str(SATELLITE_DIR.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    (segment_dir / "preview.stdout.json").write_text(completed.stdout, encoding="utf-8")
    (segment_dir / "preview.stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            f"replenishment preview failed for segment {segment_index}; "
            f"see {segment_dir / 'preview.stderr.log'}"
        )
    preview_summary = json.loads(completed.stdout)
    temporal_artifacts = _load_temporal_artifacts(preview_summary)
    segment = _preview_summary_to_segment(
        segment_id=f"replenished-{segment_index:03d}",
        preview_summary=preview_summary,
        temporal_artifacts=temporal_artifacts,
        topology_path=topology_path,
        link_state_path=link_state_path,
        run_start_time_utc=run_start_time_utc,
        max_epochs=None,
        max_runtime_epochs=None,
        max_parameter_epochs=None,
    )
    _write_json(segment_dir / "segment_summary.json", segment["summary"])
    return segment


TIMING_BREAKDOWN_DEFINITIONS = {
    "bootstrap_pre_runtime_wall_seconds": (
        "host-side runtime preparation plus reconcile before the epoch-specific apply"
    ),
    "scheduled_activation_lag_seconds": (
        "wall-clock delay from the scheduled activation boundary to apply start"
    ),
    "pre_apply_cleanup_or_blocking_verification_seconds": (
        "cleanup or probes that run before new data-plane state is applied"
    ),
    "data_plane_mutation_apply_seconds": (
        "work that changes the live packet/data-plane state for this epoch"
    ),
    "post_apply_validation_probe_seconds": (
        "connectivity probes, access warmup, or packet observation after apply"
    ),
    "post_apply_cleanup_verification_seconds": (
        "node-local verification and post-apply manager checks"
    ),
    "reporting_artifact_write_seconds": "artifact/report write time observed by the runner",
    "total_epoch_wall_seconds": "total wall time for this epoch transition",
    "data_plane_ready_lag_seconds": (
        "best-effort lag from scheduled activation until data-plane apply completes"
    ),
    "forwarding_changing_work_seconds": (
        "subset of epoch work expected to alter forwarding or packet treatment"
    ),
    "verification_cleanup_runner_overhead_seconds": (
        "remaining wall time spent outside direct forwarding/data-plane mutation"
    ),
    "scheduler_blocking_after_data_plane_ready_seconds": (
        "synchronous runner work after data_plane_ready that delays the next epoch"
    ),
    "required_safety_gate_seconds": (
        "post-data-plane verification or recovery work treated as a required safety gate"
    ),
    "artifact_write_blocking_seconds": (
        "artifact/reporting work still executed on the epoch scheduling path"
    ),
    "non_blocking_post_apply_seconds": (
        "post-apply work explicitly deferred outside the epoch scheduling path"
    ),
}


TIMING_BREAKDOWN_KEYS = tuple(TIMING_BREAKDOWN_DEFINITIONS.keys())

EPOCH_TIMING_MARK_SCHEMA_VERSION = "skyseed-epoch-timing-marks-v1"

EPOCH_TIMING_MARK_FIELDS = (
    "scheduled_activation_time_utc",
    "actual_epoch_start_time_utc",
    "pre_apply_cleanup_start",
    "pre_apply_cleanup_end",
    "blocking_verification_start",
    "blocking_verification_end",
    "data_plane_apply_start_utc",
    "host_apply_start",
    "host_apply_end",
    "node_local_apply_start",
    "node_local_apply_end",
    "data_plane_ready_time_utc",
    "post_apply_validation_start",
    "post_apply_validation_end",
    "post_apply_cleanup_verification_start",
    "post_apply_cleanup_verification_end",
    "artifact_reporting_start",
    "artifact_reporting_end",
    "epoch_end_time_utc",
)

EPOCH_TIMING_DERIVED_FIELDS = (
    "actual_network_mutation_time_seconds",
    "blocking_cleanup_verification_time_seconds",
    "post_apply_non_blocking_validation_reporting_time_seconds",
    "total_epoch_wall_time_seconds",
    "data_plane_ready_lag_seconds",
    "scheduler_blocking_after_data_plane_ready_seconds",
    "required_safety_gate_seconds",
    "artifact_write_blocking_seconds",
    "non_blocking_post_apply_seconds",
)

EPOCH_TIMING_MARK_DEFINITIONS = {
    "scheduled_activation_time_utc": "scheduled event-time activation boundary",
    "actual_epoch_start_time_utc": "wall-clock time when the runner starts applying the epoch",
    "pre_apply_cleanup_start/end": "optional cleanup before new data-plane state is applied",
    "blocking_verification_start/end": "optional pre-apply blocking probe or verification",
    "data_plane_apply_start_utc": "first host/node action expected to mutate packet treatment",
    "host_apply_start/end": "host network mutation manager apply boundary",
    "node_local_apply_start/end": "node-local MPLS/route/NAT apply boundary when applicable",
    "data_plane_ready_time_utc": "best-known time when packet-changing apply work is complete",
    "post_apply_validation_start/end": "post-apply probe, packet observation, or targeted validation",
    "post_apply_cleanup_verification_start/end": "post-apply cleanup or verification boundary when separately observable",
    "artifact_reporting_start/end": "runner artifact record assembly boundary before final emission",
    "epoch_end_time_utc": "wall-clock time when the epoch record is complete",
}


POST_DATA_PLANE_PHASE_CLASSIFICATION = {
    "post_apply_validation": {
        "blocks_next_epoch": True,
        "required_before_next_epoch": "conditional",
        "observation_or_reporting_only": "conditional",
        "async_deferred": False,
        "timing_field": "post_apply_validation_probe_seconds",
        "notes": (
            "Runtime validation, sampled parameter validation, and recovery decisions "
            "are safety gates when scheduled; packet path observation is reporting-only "
            "but still synchronous in the current runner."
        ),
    },
    "path_observation": {
        "blocks_next_epoch": True,
        "required_before_next_epoch": False,
        "observation_or_reporting_only": True,
        "async_deferred": False,
        "timing_field": "packet_observation_wall_seconds",
        "notes": "Captures packet/path evidence for the just-applied state.",
    },
    "ping_probe_validation": {
        "blocks_next_epoch": True,
        "required_before_next_epoch": "conditional",
        "observation_or_reporting_only": False,
        "async_deferred": False,
        "timing_field": "post_transition_connectivity_probe_wall_seconds",
        "notes": (
            "Required when scheduled as a runtime/parameter safety gate because its "
            "failure can change epoch outcome or trigger recovery."
        ),
    },
    "gz_or_semantic_audit": {
        "blocks_next_epoch": False,
        "required_before_next_epoch": False,
        "observation_or_reporting_only": True,
        "async_deferred": False,
        "timing_field": None,
        "notes": "G/Z audit is run as a separate analysis artifact, not inside the epoch loop.",
    },
    "timing_row_generation": {
        "blocks_next_epoch": False,
        "required_before_next_epoch": False,
        "observation_or_reporting_only": True,
        "async_deferred": True,
        "timing_field": "non_blocking_post_apply_seconds",
        "notes": "Per-epoch live timing checkpoint generation is queued to a deferred writer.",
    },
    "artifact_report_writing": {
        "blocks_next_epoch": "partial",
        "required_before_next_epoch": False,
        "observation_or_reporting_only": True,
        "async_deferred": "partial",
        "timing_field": "artifact_write_blocking_seconds",
        "notes": (
            "The final epoch detail JSONL append remains synchronous for durable final "
            "records; live timing/report/audit checkpoint refresh is deferred."
        ),
    },
    "epoch_execution_details_jsonl_writing": {
        "blocks_next_epoch": True,
        "required_before_next_epoch": False,
        "observation_or_reporting_only": True,
        "async_deferred": False,
        "timing_field": "artifact_write_blocking_seconds",
        "notes": "Kept synchronous so detail records remain final and durable in order.",
    },
    "timing_json_csv_writing": {
        "blocks_next_epoch": False,
        "required_before_next_epoch": False,
        "observation_or_reporting_only": True,
        "async_deferred": True,
        "timing_field": "non_blocking_post_apply_seconds",
        "notes": "Live timing.json/csv checkpoint writes are deferred and flushed at shutdown.",
    },
    "temporal_audit_update": {
        "blocks_next_epoch": False,
        "required_before_next_epoch": False,
        "observation_or_reporting_only": True,
        "async_deferred": True,
        "timing_field": "non_blocking_post_apply_seconds",
        "notes": "Live temporal_audit checkpoint refresh is deferred; final audit is synchronous.",
    },
    "post_apply_cleanup_verification": {
        "blocks_next_epoch": True,
        "required_before_next_epoch": True,
        "observation_or_reporting_only": False,
        "async_deferred": False,
        "timing_field": "post_apply_cleanup_verification_seconds",
        "notes": "Node-local verification and cleanup checks protect the next epoch boundary.",
    },
}


def _seconds(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _timing_seconds(record: Dict[str, Any], key: str) -> float:
    timing = record.get("timing", {})
    if not isinstance(timing, dict):
        return 0.0
    return _seconds(timing.get(key))


def _round_seconds(value: float) -> float:
    return round(max(0.0, float(value)), 6)


def _round_signed_seconds(value: float) -> float:
    return round(float(value), 6)


def _format_utc_from_epoch_seconds(value: float | None) -> str | None:
    if value is None:
        return None
    return _format_utc_timestamp(datetime.fromtimestamp(float(value), tz=timezone.utc))


def _epoch_seconds_from_perf(
    value: float | None,
    *,
    actual_epoch_start_wall: float,
    actual_epoch_start_epoch: float,
) -> float | None:
    if value is None:
        return None
    return float(actual_epoch_start_epoch) + (
        float(value) - float(actual_epoch_start_wall)
    )


def _duration_between_elapsed(
    elapsed: Dict[str, Any],
    start_key: str,
    end_key: str,
) -> float:
    start = elapsed.get(start_key)
    end = elapsed.get(end_key)
    if start is None or end is None:
        return 0.0
    return max(0.0, float(end) - float(start))


def _build_epoch_timing_marks(
    *,
    scheduled_activation_time_utc: str | None,
    actual_epoch_start_wall: float,
    actual_epoch_start_epoch: float,
    activation_lag_seconds: float,
    mark_wall_times: Dict[str, float | None],
    null_phase_explanations: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    scheduled_elapsed = -float(activation_lag_seconds)
    timestamps: Dict[str, Any] = {
        "scheduled_activation_time_utc": scheduled_activation_time_utc,
        "actual_epoch_start_time_utc": _format_utc_from_epoch_seconds(
            actual_epoch_start_epoch
        ),
    }
    elapsed: Dict[str, Any] = {
        "scheduled_activation_time_utc": _round_signed_seconds(scheduled_elapsed),
        "actual_epoch_start_time_utc": 0.0,
    }
    for field in EPOCH_TIMING_MARK_FIELDS:
        if field in timestamps:
            continue
        wall_value = mark_wall_times.get(field)
        if wall_value is None:
            timestamps[field] = None
            elapsed[field] = None
            continue
        timestamps[field] = _format_utc_from_epoch_seconds(
            _epoch_seconds_from_perf(
                wall_value,
                actual_epoch_start_wall=actual_epoch_start_wall,
                actual_epoch_start_epoch=actual_epoch_start_epoch,
            )
        )
        elapsed[field] = _round_signed_seconds(float(wall_value) - actual_epoch_start_wall)

    post_apply_validation = _duration_between_elapsed(
        elapsed,
        "post_apply_validation_start",
        "post_apply_validation_end",
    )
    post_apply_cleanup_verification = _duration_between_elapsed(
        elapsed,
        "post_apply_cleanup_verification_start",
        "post_apply_cleanup_verification_end",
    )
    artifact_reporting = _duration_between_elapsed(
        elapsed,
        "artifact_reporting_start",
        "artifact_reporting_end",
    )
    derived = {
        "actual_network_mutation_time_seconds": _duration_between_elapsed(
            elapsed,
            "data_plane_apply_start_utc",
            "data_plane_ready_time_utc",
        ),
        "blocking_cleanup_verification_time_seconds": (
            _duration_between_elapsed(
                elapsed,
                "pre_apply_cleanup_start",
                "pre_apply_cleanup_end",
            )
            + _duration_between_elapsed(
                elapsed,
                "blocking_verification_start",
                "blocking_verification_end",
            )
        ),
        "post_apply_non_blocking_validation_reporting_time_seconds": (
            post_apply_validation
            + artifact_reporting
        ),
        "total_epoch_wall_time_seconds": _duration_between_elapsed(
            elapsed,
            "actual_epoch_start_time_utc",
            "epoch_end_time_utc",
        ),
        "data_plane_ready_lag_seconds": _duration_between_elapsed(
            elapsed,
            "scheduled_activation_time_utc",
            "data_plane_ready_time_utc",
        ),
        "scheduler_blocking_after_data_plane_ready_seconds": _duration_between_elapsed(
            elapsed,
            "data_plane_ready_time_utc",
            "epoch_end_time_utc",
        ),
        "required_safety_gate_seconds": post_apply_cleanup_verification,
        "artifact_write_blocking_seconds": artifact_reporting,
        "non_blocking_post_apply_seconds": 0.0,
    }
    explanations = dict(null_phase_explanations or {})
    for start_key, end_key, explanation in (
        (
            "pre_apply_cleanup_start",
            "pre_apply_cleanup_end",
            "no pre-apply cleanup was required for this epoch",
        ),
        (
            "blocking_verification_start",
            "blocking_verification_end",
            "no pre-apply blocking verification was scheduled for this epoch",
        ),
        (
            "node_local_apply_start",
            "node_local_apply_end",
            "node-local apply was not part of this epoch",
        ),
        (
            "post_apply_validation_start",
            "post_apply_validation_end",
            "post-apply validation or observation was skipped for this epoch",
        ),
        (
            "post_apply_cleanup_verification_start",
            "post_apply_cleanup_verification_end",
            "post-apply cleanup verification was not separately timestamped",
        ),
    ):
        if timestamps.get(start_key) is None and timestamps.get(end_key) is None:
            explanations.setdefault(start_key.rsplit("_", 1)[0], explanation)

    return {
        "schema_version": EPOCH_TIMING_MARK_SCHEMA_VERSION,
        "mark_definitions": dict(EPOCH_TIMING_MARK_DEFINITIONS),
        **timestamps,
        "monotonic_elapsed_seconds": elapsed,
        "derived_seconds": {
            key: _round_seconds(value)
            for key, value in derived.items()
        },
        "null_phase_explanations": explanations,
    }


def _record_node_local_timing_marks(
    epoch_timing_wall_marks: Dict[str, float | None],
    *,
    node_local_apply_started_wall: float,
    node_local_apply_wall_seconds: float,
    node_local_incremental_cleanup_wall_seconds: float,
    node_local_verify_wall_seconds: float,
) -> None:
    cleanup_seconds = max(0.0, float(node_local_incremental_cleanup_wall_seconds))
    node_apply_start = float(node_local_apply_started_wall)
    if cleanup_seconds > 0.0:
        epoch_timing_wall_marks["pre_apply_cleanup_start"] = node_apply_start
        epoch_timing_wall_marks["pre_apply_cleanup_end"] = (
            node_apply_start + cleanup_seconds
        )
        node_apply_start += cleanup_seconds
    node_apply_end = node_apply_start + max(0.0, float(node_local_apply_wall_seconds))
    epoch_timing_wall_marks["node_local_apply_start"] = node_apply_start
    epoch_timing_wall_marks["node_local_apply_end"] = node_apply_end
    epoch_timing_wall_marks["data_plane_ready_time_utc"] = node_apply_end
    verify_seconds = max(0.0, float(node_local_verify_wall_seconds))
    if verify_seconds > 0.0:
        epoch_timing_wall_marks["post_apply_cleanup_verification_start"] = (
            node_apply_end
        )
        epoch_timing_wall_marks["post_apply_cleanup_verification_end"] = (
            node_apply_end + verify_seconds
        )


def _epoch_timing_marks(record: Dict[str, Any]) -> Dict[str, Any]:
    marks = record.get("epoch_timing_marks")
    if isinstance(marks, dict):
        normalized = {
            "schema_version": marks.get(
                "schema_version",
                EPOCH_TIMING_MARK_SCHEMA_VERSION,
            ),
            "mark_definitions": dict(
                marks.get("mark_definitions") or EPOCH_TIMING_MARK_DEFINITIONS
            ),
        }
        for field in EPOCH_TIMING_MARK_FIELDS:
            normalized[field] = marks.get(field)
        elapsed = dict(marks.get("monotonic_elapsed_seconds") or {})
        normalized["monotonic_elapsed_seconds"] = {
            field: elapsed.get(field)
            for field in EPOCH_TIMING_MARK_FIELDS
        }
        derived = dict(marks.get("derived_seconds") or {})
        normalized["derived_seconds"] = {
            field: derived.get(field)
            for field in EPOCH_TIMING_DERIVED_FIELDS
        }
        normalized["null_phase_explanations"] = dict(
            marks.get("null_phase_explanations") or {}
        )
        return normalized

    return {
        "schema_version": EPOCH_TIMING_MARK_SCHEMA_VERSION,
        "mark_definitions": dict(EPOCH_TIMING_MARK_DEFINITIONS),
        **{field: None for field in EPOCH_TIMING_MARK_FIELDS},
        "scheduled_activation_time_utc": record.get("scheduled_activation_time")
        or record.get("activation_time_utc"),
        "actual_epoch_start_time_utc": record.get("apply_start_time_utc"),
        "epoch_end_time_utc": record.get("apply_end_time_utc"),
        "monotonic_elapsed_seconds": {
            field: None for field in EPOCH_TIMING_MARK_FIELDS
        },
        "derived_seconds": {
            "actual_network_mutation_time_seconds": None,
            "blocking_cleanup_verification_time_seconds": None,
            "post_apply_non_blocking_validation_reporting_time_seconds": None,
            "total_epoch_wall_time_seconds": _timing_seconds(
                record,
                "transition_wall_seconds",
            ),
            "data_plane_ready_lag_seconds": None,
            "scheduler_blocking_after_data_plane_ready_seconds": None,
            "required_safety_gate_seconds": None,
            "artifact_write_blocking_seconds": None,
            "non_blocking_post_apply_seconds": None,
        },
        "null_phase_explanations": {
            "legacy_record": (
                "epoch_timing_marks were not emitted when this artifact was produced"
            )
        },
    }


def _epoch_timing_semantic_breakdown(record: Dict[str, Any]) -> Dict[str, Any]:
    epoch_type = str(record.get("epoch_type", ""))
    total_wall = _timing_seconds(record, "transition_wall_seconds")
    timing_marks = _epoch_timing_marks(record)
    timing_mark_derived = dict(timing_marks.get("derived_seconds") or {})
    activation_lag = _seconds(record.get("activation_lag_seconds"))
    bootstrap = (
        _timing_seconds(record, "host_runtime_prepare_wall_seconds")
        + _timing_seconds(record, "host_runtime_reconcile_wall_seconds")
    )
    pre_probe = _timing_seconds(record, "pre_transition_connectivity_probe_wall_seconds")
    post_probe = _timing_seconds(record, "post_transition_connectivity_probe_wall_seconds")
    host_apply = _timing_seconds(record, "host_operation_apply_wall_seconds")
    node_apply = _timing_seconds(record, "node_local_apply_wall_seconds")
    node_verify = _timing_seconds(record, "node_local_verify_wall_seconds")
    incremental_cleanup = _timing_seconds(
        record,
        "node_local_incremental_cleanup_wall_seconds",
    )
    access_warmup = _timing_seconds(record, "access_link_warmup_wall_seconds")
    packet_observation = _timing_seconds(record, "packet_observation_wall_seconds")
    reporting = _timing_seconds(record, "audit_write_seconds")
    post_apply_state_refresh = _timing_seconds(record, "post_apply_state_refresh_wall_seconds")
    post_apply_verification = _timing_seconds(record, "post_apply_verification_wall_seconds")

    def _derived_or_default(key: str, default: float) -> float:
        value = timing_mark_derived.get(key)
        if value is None:
            return _round_seconds(default)
        return _round_seconds(_seconds(value))

    if epoch_type == "parameter_epoch":
        qdisc_apply = _timing_seconds(record, "parameter_qdisc_apply_seconds")
        metadata_update = _timing_seconds(record, "parameter_metadata_update_seconds")
        dirty_reconcile = _timing_seconds(record, "parameter_dirty_reconcile_seconds")
        targeted_validation = _timing_seconds(record, "targeted_validation_seconds")
        recovery = _timing_seconds(record, "recovery_seconds")
        actual_parameter_mutation = qdisc_apply + metadata_update
        pre_apply = pre_probe
        data_plane_apply = qdisc_apply
        post_validation = max(targeted_validation, post_probe) + packet_observation
        post_cleanup_verification = post_apply_state_refresh + post_apply_verification
        accounted = (
            bootstrap
            + pre_apply
            + dirty_reconcile
            + actual_parameter_mutation
            + post_validation
            + post_cleanup_verification
            + recovery
            + reporting
        )
        non_escalated = not bool(record.get("escalated_to_runtime_epoch"))
        topology_or_forwarding_free = (
            int(record.get("topology_mutation_count") or 0) == 0
            and int(record.get("forwarding_mutation_count") or 0) == 0
        )
        return {
            "epoch_type": epoch_type,
            "classification": "parameter_epoch_timing",
            "bootstrap_pre_runtime_wall_seconds": _round_seconds(bootstrap),
            "scheduled_activation_lag_seconds": _round_seconds(activation_lag),
            "pre_apply_cleanup_or_blocking_verification_seconds": _round_seconds(
                pre_apply
            ),
            "data_plane_mutation_apply_seconds": _round_seconds(data_plane_apply),
            "post_apply_validation_probe_seconds": _round_seconds(post_validation),
            "post_apply_cleanup_verification_seconds": _round_seconds(
                post_cleanup_verification
            ),
            "reporting_artifact_write_seconds": _round_seconds(reporting),
            "total_epoch_wall_seconds": _round_seconds(total_wall),
            "data_plane_ready_lag_seconds": _round_seconds(
                activation_lag + bootstrap + pre_apply + dirty_reconcile + data_plane_apply
            ),
            "forwarding_changing_work_seconds": _round_seconds(data_plane_apply),
            "verification_cleanup_runner_overhead_seconds": _round_seconds(
                total_wall - data_plane_apply
            ),
            "scheduler_blocking_after_data_plane_ready_seconds": _derived_or_default(
                "scheduler_blocking_after_data_plane_ready_seconds",
                post_validation + post_cleanup_verification + recovery + reporting,
            ),
            "required_safety_gate_seconds": _round_seconds(
                targeted_validation + recovery + post_cleanup_verification
            ),
            "artifact_write_blocking_seconds": _derived_or_default(
                "artifact_write_blocking_seconds",
                reporting,
            ),
            "non_blocking_post_apply_seconds": _derived_or_default(
                "non_blocking_post_apply_seconds",
                0.0,
            ),
            "parameter_qdisc_apply_seconds": _round_seconds(qdisc_apply),
            "parameter_metadata_update_seconds": _round_seconds(metadata_update),
            "parameter_dirty_reconcile_or_cached_skip_seconds": _round_seconds(
                dirty_reconcile
            ),
            "parameter_actual_mutation_seconds": _round_seconds(
                actual_parameter_mutation
            ),
            "parameter_targeted_validation_seconds": _round_seconds(
                targeted_validation
            ),
            "parameter_recovery_fallback_seconds": _round_seconds(recovery),
            "non_escalated_parameter_epoch": non_escalated,
            "topology_or_forwarding_mutation_free": topology_or_forwarding_free,
            "parameter_epoch_lightweight_under_event_time": (
                non_escalated
                and topology_or_forwarding_free
                and recovery == 0.0
                and qdisc_apply <= total_wall
            ),
        }

    pre_apply = pre_probe + incremental_cleanup
    data_plane_apply = host_apply + node_apply
    post_validation = post_probe + access_warmup + packet_observation
    post_cleanup_verification = (
        node_verify + post_apply_state_refresh + post_apply_verification
    )
    accounted = (
        bootstrap
        + pre_apply
        + data_plane_apply
        + post_validation
        + post_cleanup_verification
        + reporting
    )
    return {
        "epoch_type": epoch_type,
        "classification": "runtime_epoch_timing",
        "bootstrap_pre_runtime_wall_seconds": _round_seconds(bootstrap),
        "scheduled_activation_lag_seconds": _round_seconds(activation_lag),
        "pre_apply_cleanup_or_blocking_verification_seconds": _round_seconds(pre_apply),
        "data_plane_mutation_apply_seconds": _round_seconds(data_plane_apply),
        "post_apply_validation_probe_seconds": _round_seconds(post_validation),
        "post_apply_cleanup_verification_seconds": _round_seconds(
            post_cleanup_verification
        ),
        "reporting_artifact_write_seconds": _round_seconds(reporting),
        "total_epoch_wall_seconds": _round_seconds(total_wall),
        "data_plane_ready_lag_seconds": _round_seconds(
            activation_lag + bootstrap + pre_apply + data_plane_apply
        ),
        "forwarding_changing_work_seconds": _round_seconds(data_plane_apply),
        "verification_cleanup_runner_overhead_seconds": _round_seconds(
            total_wall - data_plane_apply
        ),
        "scheduler_blocking_after_data_plane_ready_seconds": _derived_or_default(
            "scheduler_blocking_after_data_plane_ready_seconds",
            post_validation + post_cleanup_verification + reporting,
        ),
        "required_safety_gate_seconds": _round_seconds(
            node_verify + post_probe + access_warmup
        ),
        "artifact_write_blocking_seconds": _derived_or_default(
            "artifact_write_blocking_seconds",
            reporting,
        ),
        "non_blocking_post_apply_seconds": _derived_or_default(
            "non_blocking_post_apply_seconds",
            0.0,
        ),
        "runtime_host_apply_seconds": _round_seconds(host_apply),
        "runtime_node_local_apply_seconds": _round_seconds(node_apply),
        "runtime_mpls_route_nat_apply_seconds": _round_seconds(node_apply),
        "runtime_incremental_cleanup_seconds": _round_seconds(incremental_cleanup),
        "runtime_node_local_verification_seconds": _round_seconds(node_verify),
        "runtime_unclassified_wall_seconds": _round_seconds(total_wall - accounted),
    }


def _live_timing_summary(epoch_records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    runtime_epochs = [
        record for record in epoch_records if str(record.get("epoch_type")) == "runtime_epoch"
    ]
    initial_runtime_epoch = runtime_epochs[0] if runtime_epochs else None
    steady_runtime_epochs = list(runtime_epochs[1:]) if len(runtime_epochs) > 1 else []
    parameter_epochs = [
        record
        for record in epoch_records
        if str(record.get("epoch_type")) == "parameter_epoch"
        and not bool(record.get("noop_skipped"))
    ]
    noop_parameter_epochs = [
        record
        for record in epoch_records
        if str(record.get("epoch_type")) == "parameter_epoch"
        and bool(record.get("noop_skipped"))
    ]

    def _avg(records: Sequence[Dict[str, Any]], key: str) -> float:
        if not records:
            return 0.0
        return round(
            sum(float(record.get("timing", {}).get(key, 0.0)) for record in records)
            / float(len(records)),
            6,
        )

    def _p95(values: Sequence[float]) -> float:
        if not values:
            return 0.0
        sorted_values = sorted(values)
        index = max(0, min(len(sorted_values) - 1, math.ceil(0.95 * len(sorted_values)) - 1))
        return float(sorted_values[index])

    def _distribution(values: Sequence[float]) -> Dict[str, float]:
        if not values:
            return {
                "count": 0,
                "average_seconds": 0.0,
                "p95_seconds": 0.0,
                "worst_seconds": 0.0,
            }
        return {
            "count": len(values),
            "average_seconds": round(sum(values) / float(len(values)), 6),
            "p95_seconds": round(_p95(values), 6),
            "worst_seconds": round(max(values), 6),
        }

    def _record_distribution(
        records: Sequence[Dict[str, Any]],
        key: str,
        *,
        source: str = "timing",
    ) -> Dict[str, float]:
        values: List[float] = []
        for record in records:
            container = record.get("timing", {}) if source == "timing" else record
            if not isinstance(container, dict) or container.get(key) is None:
                continue
            values.append(float(container.get(key, 0.0)))
        return _distribution(values)

    def _breakdown(record: Dict[str, Any]) -> Dict[str, Any]:
        timing = record.get("timing", {})
        if isinstance(timing, dict) and isinstance(timing.get("semantic_breakdown"), dict):
            return dict(timing.get("semantic_breakdown") or {})
        return _epoch_timing_semantic_breakdown(record)

    def _breakdown_distribution(
        records: Sequence[Dict[str, Any]],
        key: str,
    ) -> Dict[str, float]:
        values: List[float] = []
        for record in records:
            breakdown = _breakdown(record)
            if breakdown.get(key) is None:
                continue
            values.append(_seconds(breakdown.get(key)))
        return _distribution(values)

    def _breakdown_boolean_count(
        records: Sequence[Dict[str, Any]],
        key: str,
    ) -> int:
        return sum(1 for record in records if bool(_breakdown(record).get(key)))

    def _semantic_summary(
        records: Sequence[Dict[str, Any]],
        *,
        extra_keys: Sequence[str] = (),
    ) -> Dict[str, Any]:
        keys = list(TIMING_BREAKDOWN_KEYS)
        for key in extra_keys:
            if key not in keys:
                keys.append(key)
        return {
            "definitions": TIMING_BREAKDOWN_DEFINITIONS,
            "distributions": {
                key: _breakdown_distribution(records, key)
                for key in keys
            },
        }

    return {
        "runtime_epoch_count": len(runtime_epochs),
        "parameter_epoch_count": len(parameter_epochs),
        "parameter_epoch_noop_skipped_count": len(noop_parameter_epochs),
        "deadline_miss_threshold_seconds": DEADLINE_MISS_THRESHOLD_SECONDS,
        "deadline_miss_definition": (
            "strict scheduler-boundary miss: activation_lag_seconds exceeds "
            f"{DEADLINE_MISS_THRESHOLD_SECONDS:.2f}s; this is separate from ping loss "
            "or temporal-audit failure"
        ),
        "deadline_miss_count": sum(
            1 for record in epoch_records if bool(record.get("deadline_miss"))
        ),
        "epoch_timing_marks": {
            "schema_version": EPOCH_TIMING_MARK_SCHEMA_VERSION,
            "field_names": list(EPOCH_TIMING_MARK_FIELDS),
            "derived_fields": list(EPOCH_TIMING_DERIVED_FIELDS),
            "mark_definitions": dict(EPOCH_TIMING_MARK_DEFINITIONS),
            "null_field_policy": (
                "all fields are emitted for every epoch; fields are null when a "
                "phase is not applicable or not separately observable, with "
                "per-epoch explanations under epoch_timing_marks.null_phase_explanations"
            ),
        },
        "post_data_plane_phase_classification": dict(
            POST_DATA_PLANE_PHASE_CLASSIFICATION
        ),
        "runtime_epochs": {
            "average_transition_wall_seconds": _avg(runtime_epochs, "transition_wall_seconds"),
            "transition_wall_seconds": _record_distribution(
                runtime_epochs,
                "transition_wall_seconds",
            ),
            "average_network_mutation_plan_wall_seconds": _avg(
                runtime_epochs, "network_mutation_plan_wall_seconds"
            ),
            "average_direct_backend_prepare_wall_seconds": _avg(
                runtime_epochs, "host_runtime_prepare_wall_seconds"
            ),
            "average_runtime_manager_reconcile_wall_seconds": _avg(
                runtime_epochs, "host_runtime_reconcile_wall_seconds"
            ),
            "average_host_mutation_apply_wall_seconds": _avg(
                runtime_epochs, "host_operation_apply_wall_seconds"
            ),
            "average_node_local_apply_wall_seconds": _avg(
                runtime_epochs, "node_local_apply_wall_seconds"
            ),
            "average_node_local_incremental_cleanup_wall_seconds": _avg(
                runtime_epochs, "node_local_incremental_cleanup_wall_seconds"
            ),
            "average_node_local_reapplied_flow_count": _avg(
                runtime_epochs, "node_local_reapplied_flow_count"
            ),
            "average_node_local_held_flow_count": _avg(
                runtime_epochs, "node_local_held_flow_count"
            ),
            "average_pre_transition_connectivity_probe_wall_seconds": _avg(
                runtime_epochs, "pre_transition_connectivity_probe_wall_seconds"
            ),
            "average_post_transition_connectivity_probe_wall_seconds": _avg(
                runtime_epochs, "post_transition_connectivity_probe_wall_seconds"
            ),
            "average_access_link_warmup_wall_seconds": _avg(
                runtime_epochs, "access_link_warmup_wall_seconds"
            ),
            "average_post_apply_state_refresh_wall_seconds": _avg(
                runtime_epochs, "post_apply_state_refresh_wall_seconds"
            ),
            "average_post_apply_verification_wall_seconds": _avg(
                runtime_epochs, "post_apply_verification_wall_seconds"
            ),
            "scheduler_blocking_after_data_plane_ready_seconds": _record_distribution(
                runtime_epochs,
                "scheduler_blocking_after_data_plane_ready_seconds",
            ),
            "required_safety_gate_seconds": _record_distribution(
                runtime_epochs,
                "required_safety_gate_seconds",
            ),
            "artifact_write_blocking_seconds": _record_distribution(
                runtime_epochs,
                "artifact_write_blocking_seconds",
            ),
            "non_blocking_post_apply_seconds": _record_distribution(
                runtime_epochs,
                "non_blocking_post_apply_seconds",
            ),
            "activation_lag_seconds": _record_distribution(
                runtime_epochs,
                "activation_lag_seconds",
                source="record",
            ),
            "semantic_timing_breakdown": _semantic_summary(
                runtime_epochs,
                extra_keys=(
                    "runtime_host_apply_seconds",
                    "runtime_node_local_apply_seconds",
                    "runtime_mpls_route_nat_apply_seconds",
                    "runtime_incremental_cleanup_seconds",
                    "runtime_node_local_verification_seconds",
                    "runtime_unclassified_wall_seconds",
                ),
            ),
        },
        "initial_runtime_epoch": (
            {
                "present": True,
                "sequence_index": initial_runtime_epoch.get("sequence_index"),
                "epoch_id": initial_runtime_epoch.get("epoch_id"),
                "segment_epoch_key": initial_runtime_epoch.get("segment_epoch_key"),
                "transition_wall_seconds": dict(
                    initial_runtime_epoch.get("timing") or {}
                ).get("transition_wall_seconds"),
                "scheduler_blocking_after_data_plane_ready_seconds": dict(
                    initial_runtime_epoch.get("timing") or {}
                ).get("scheduler_blocking_after_data_plane_ready_seconds"),
                "classification": "initial_materialization_epoch",
            }
            if initial_runtime_epoch is not None
            else {
                "present": False,
                "classification": "initial_materialization_epoch",
            }
        ),
        "runtime_epochs_excluding_initial": {
            "source_runtime_epoch_count": len(runtime_epochs),
            "excluded_initial_runtime_epoch_count": 1 if runtime_epochs else 0,
            "count": len(steady_runtime_epochs),
            "transition_wall_seconds": _record_distribution(
                steady_runtime_epochs,
                "transition_wall_seconds",
            ),
            "scheduler_blocking_after_data_plane_ready_seconds": _record_distribution(
                steady_runtime_epochs,
                "scheduler_blocking_after_data_plane_ready_seconds",
            ),
            "semantic_timing_breakdown": _semantic_summary(
                steady_runtime_epochs,
                extra_keys=(
                    "runtime_host_apply_seconds",
                    "runtime_node_local_apply_seconds",
                    "runtime_mpls_route_nat_apply_seconds",
                    "runtime_incremental_cleanup_seconds",
                    "runtime_node_local_verification_seconds",
                    "runtime_unclassified_wall_seconds",
                ),
            ),
        },
        "parameter_epochs": {
            "average_transition_wall_seconds": _avg(parameter_epochs, "transition_wall_seconds"),
            "transition_wall_seconds": _record_distribution(
                parameter_epochs,
                "transition_wall_seconds",
            ),
            "average_network_mutation_plan_wall_seconds": _avg(
                parameter_epochs, "network_mutation_plan_wall_seconds"
            ),
            "average_direct_backend_prepare_wall_seconds": _avg(
                parameter_epochs, "host_runtime_prepare_wall_seconds"
            ),
            "average_runtime_manager_reconcile_wall_seconds": _avg(
                parameter_epochs, "host_runtime_reconcile_wall_seconds"
            ),
            "average_host_mutation_apply_wall_seconds": _avg(
                parameter_epochs, "host_operation_apply_wall_seconds"
            ),
            "average_pre_transition_connectivity_probe_wall_seconds": _avg(
                parameter_epochs, "pre_transition_connectivity_probe_wall_seconds"
            ),
            "average_post_transition_connectivity_probe_wall_seconds": _avg(
                parameter_epochs, "post_transition_connectivity_probe_wall_seconds"
            ),
            "average_parameter_qdisc_apply_seconds": _avg(
                parameter_epochs, "parameter_qdisc_apply_seconds"
            ),
            "average_parameter_metadata_update_seconds": _avg(
                parameter_epochs, "parameter_metadata_update_seconds"
            ),
            "average_parameter_dirty_reconcile_seconds": _avg(
                parameter_epochs, "parameter_dirty_reconcile_seconds"
            ),
            "average_targeted_validation_seconds": _avg(
                parameter_epochs, "targeted_validation_seconds"
            ),
            "average_recovery_seconds": _avg(
                parameter_epochs, "recovery_seconds"
            ),
            "average_post_apply_state_refresh_wall_seconds": _avg(
                parameter_epochs, "post_apply_state_refresh_wall_seconds"
            ),
            "average_post_apply_verification_wall_seconds": _avg(
                parameter_epochs, "post_apply_verification_wall_seconds"
            ),
            "scheduler_blocking_after_data_plane_ready_seconds": _record_distribution(
                parameter_epochs,
                "scheduler_blocking_after_data_plane_ready_seconds",
            ),
            "required_safety_gate_seconds": _record_distribution(
                parameter_epochs,
                "required_safety_gate_seconds",
            ),
            "artifact_write_blocking_seconds": _record_distribution(
                parameter_epochs,
                "artifact_write_blocking_seconds",
            ),
            "non_blocking_post_apply_seconds": _record_distribution(
                parameter_epochs,
                "non_blocking_post_apply_seconds",
            ),
            "activation_lag_seconds": _record_distribution(
                parameter_epochs,
                "activation_lag_seconds",
                source="record",
            ),
            "semantic_timing_breakdown": {
                **_semantic_summary(
                    parameter_epochs,
                    extra_keys=(
                        "parameter_qdisc_apply_seconds",
                        "parameter_metadata_update_seconds",
                        "parameter_dirty_reconcile_or_cached_skip_seconds",
                        "parameter_actual_mutation_seconds",
                        "parameter_targeted_validation_seconds",
                        "parameter_recovery_fallback_seconds",
                    ),
                ),
                "non_escalated_parameter_epoch_count": _breakdown_boolean_count(
                    parameter_epochs,
                    "non_escalated_parameter_epoch",
                ),
                "topology_or_forwarding_mutation_free_count": _breakdown_boolean_count(
                    parameter_epochs,
                    "topology_or_forwarding_mutation_free",
                ),
                "lightweight_under_event_time_count": _breakdown_boolean_count(
                    parameter_epochs,
                    "parameter_epoch_lightweight_under_event_time",
                ),
            },
        },
    }


def _safe_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return list(value)


def _safe_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    return dict(value)


def _trim_text(value: Any, *, limit: int = 4000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "...<trimmed>"


def _compact_flow_result(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    keep_keys = {
        "flow_id",
        "source",
        "source_id",
        "target",
        "target_id",
        "destination",
        "destination_ip",
        "target_ip",
        "ping_success",
        "success",
        "path_hop_count",
        "forward_labels",
        "reverse_labels",
        "forward_label_stack",
        "reverse_label_stack",
        "selected_gateway_id",
        "gateway_id",
        "zone_id",
        "responsible_satellite_id",
        "serving_satellite_id",
        "error",
        "failure_reason",
    }
    return {key: value.get(key) for key in sorted(keep_keys) if key in value}


def _compact_connectivity(value: Any) -> Dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    payload = {
        key: value.get(key)
        for key in (
            "slice_id",
            "ping_success",
            "wall_seconds",
            "attempt_count",
            "retry_recovered",
            "validation_skipped",
            "skip_reason",
        )
        if key in value
    }
    flow_results = [
        _compact_flow_result(result)
        for result in list(value.get("flow_results", []))
        if isinstance(result, dict)
    ]
    if flow_results:
        payload["flow_results"] = flow_results
    return payload


def _compact_path_observations(value: Any) -> List[Dict[str, Any]]:
    observations: List[Dict[str, Any]] = []
    for item in list(value or []):
        if not isinstance(item, dict):
            continue
        compact = _compact_flow_result(item)
        for key in (
            "path",
            "path_ids",
            "node_ids",
            "satellite_path",
            "reverse_path",
            "return_path",
        ):
            if key in item:
                compact[key] = item.get(key)
        observations.append(compact)
    return observations


def _compact_packet_observation(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {"enabled": False, "paths": []}
    return {
        "enabled": bool(value.get("enabled")),
        "paths": _compact_path_observations(value.get("paths", [])),
    }


def _compact_host_mutation_execution(value: Any) -> Dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    outcome = dict(value.get("outcome") or {})
    compact_outcome = {
        "apply_success": outcome.get("apply_success"),
        "operation_count": outcome.get("operation_count"),
        "delta_summary": _safe_dict(outcome.get("delta_summary")),
        "operation_timing_summary": _safe_dict(outcome.get("operation_timing_summary")),
        "manager_timing_breakdown": _safe_dict(outcome.get("manager_timing_breakdown")),
    }
    return {
        "boundary": _safe_dict(value.get("boundary")),
        "outcome": compact_outcome,
        "apply_result_count": len(list(value.get("apply_results", []))),
    }


def _compact_node_local_execution(value: Any) -> Dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    preflight = dict(value.get("preflight") or {})
    target_slice = dict(value.get("target_slice") or {})
    outcome = dict(value.get("outcome") or {})
    timing = _safe_dict(value.get("timing"))
    timing.pop("flow_path_observations", None)
    return {
        "backend": value.get("backend"),
        "session_id": value.get("session_id"),
        "outcome": {
            "real_apply_success": outcome.get("real_apply_success"),
            "ping_success": outcome.get("ping_success"),
            "rollback_completed": outcome.get("rollback_completed"),
            "failure_reason": outcome.get("failure_reason"),
        },
        "timing": timing,
        "preflight": {
            "ping_validation_selection": _safe_dict(
                preflight.get("ping_validation_selection")
            ),
            "realization_support": _safe_dict(preflight.get("realization_support")),
        },
        "target_slice": {
            "slice_id": target_slice.get("slice_id"),
            "active_flow_count": target_slice.get("active_flow_count"),
            "reapplied_flow_count": target_slice.get("reapplied_flow_count"),
            "held_flow_count": target_slice.get("held_flow_count"),
            "incremental_cleanup_flow_count": target_slice.get(
                "incremental_cleanup_flow_count"
            ),
            "executed_flow_ids": _safe_list(target_slice.get("executed_flow_ids")),
            "reapplied_flow_ids": _safe_list(target_slice.get("reapplied_flow_ids")),
            "held_flow_ids": _safe_list(target_slice.get("held_flow_ids")),
            "incremental_cleanup_flow_ids": _safe_list(
                target_slice.get("incremental_cleanup_flow_ids")
            ),
        },
        "ping_result_count": len(list(value.get("ping_results", []))),
    }


def _compact_epoch_execution_record(
    epoch_record: Dict[str, Any],
    *,
    detail_jsonl: Path,
) -> Dict[str, Any]:
    scalar_keys = {
        "sequence_index",
        "epoch_id",
        "epoch_type",
        "original_epoch_type",
        "segment_id",
        "segment_epoch_key",
        "slice_id",
        "to_slice_id",
        "loop_iteration",
        "simulated_time_seconds",
        "snapshot_id",
        "activation_time_utc",
        "activation_relative_time_seconds",
        "activation_group_id",
        "execution_policy",
        "scheduled_activation_time",
        "apply_start_time_utc",
        "apply_end_time_utc",
        "epoch_timing_marks",
        "artifact_record_state",
        "activation_lag_seconds",
        "deadline_miss",
        "trigger_reasons",
        "scheduled_topology_mutation",
        "escalated_to_runtime_epoch",
        "noop_skipped",
        "parameter_refresh_recovery_used",
        "node_local_reapplied_flow_ids",
        "node_local_held_flow_ids",
        "applied_operations",
        "skipped_operations",
        "topology_mutation_count",
        "forwarding_mutation_count",
        "network_mutation_count",
        "host_prepare_summary",
        "host_reconcile_summary",
        "host_operation_timing_summary",
        "timing",
        "outcome",
    }
    compact = {
        key: epoch_record.get(key)
        for key in sorted(scalar_keys)
        if key in epoch_record
    }
    compact["pre_transition_connectivity"] = _compact_connectivity(
        epoch_record.get("pre_transition_connectivity")
    )
    compact["post_transition_connectivity"] = _compact_connectivity(
        epoch_record.get("post_transition_connectivity")
    )
    compact["pre_transition_paths"] = _compact_path_observations(
        epoch_record.get("pre_transition_paths")
    )
    compact["post_transition_paths"] = _compact_path_observations(
        epoch_record.get("post_transition_paths")
    )
    compact["packet_observation"] = _compact_packet_observation(
        epoch_record.get("packet_observation")
    )
    compact["host_mutation_execution"] = _compact_host_mutation_execution(
        epoch_record.get("host_mutation_execution")
    )
    compact["node_local_execution"] = _compact_node_local_execution(
        epoch_record.get("node_local_execution")
    )
    compact["node_local_rollback_before_transition"] = None
    compact["detail_reference"] = {
        "artifact": str(detail_jsonl),
        "sequence_index": epoch_record.get("sequence_index"),
        "segment_epoch_key": epoch_record.get("segment_epoch_key"),
        "record_model": "full_epoch_execution_record_jsonl",
    }
    return compact


def _selected_access_prewarm_resource_scope_by_slot(
    *,
    snapshot_preview_path: Path,
    selected_slice_ids: Sequence[str],
) -> Dict[str, List[str]]:
    if not snapshot_preview_path.exists():
        return {}
    payload = json.loads(snapshot_preview_path.read_text(encoding="utf-8"))
    snapshots = list(payload.get("snapshots", []))
    if not snapshots:
        return {}
    selected = {str(slice_id) for slice_id in selected_slice_ids if str(slice_id)}
    scope_by_slot: Dict[str, set[str]] = {}
    for snapshot in snapshots:
        time_slice = dict(snapshot.get("time_slice", {}))
        slice_id = str(time_slice.get("slice_id", ""))
        if slice_id not in selected:
            continue
        for link in list(snapshot.get("active_links", [])):
            slot_type = str(link.get("slot_type"))
            if slot_type not in {"ut_access", "gw_access"}:
                continue
            slot_name = str(link.get("link_id"))
            binding = dict(link.get("binding", {}) or {})
            if not slot_name or not binding:
                continue
            scope_by_slot.setdefault(slot_name, set()).add(
                dynamic_slot_binding_resource_key(slot_type, binding)
            )
    return {
        slot_name: sorted(resource_keys)
        for slot_name, resource_keys in sorted(scope_by_slot.items())
        if resource_keys
    }


def _runtime_exception_details(exc: BaseException) -> Dict[str, Any]:
    details: Dict[str, Any] = {
        "exception_type": type(exc).__name__,
        "message": str(exc),
    }
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        details["cause"] = _runtime_exception_details(cause)
    if isinstance(exc, NodeLocalBatchFailure):
        result = exc.result
        details["node_local_batch_failure"] = {
            "node_id": result.node_id,
            "phase": result.phase,
            "returncode": result.returncode,
            "command_count": result.command_count,
            "descriptions": list(result.descriptions),
            "stdout": _trim_text(result.stdout),
            "stderr": _trim_text(result.stderr),
            "wall_seconds": round(float(result.wall_seconds), 6),
        }
    elif isinstance(exc, RuntimeCommandFailure):
        result = exc.result
        details["runtime_command_failure"] = {
            "node_id": result.node_id,
            "phase": result.phase,
            "description": result.description,
            "command": list(result.command),
            "returncode": result.returncode,
            "stdout": _trim_text(result.stdout),
            "stderr": _trim_text(result.stderr),
        }
    return details


def _run_live_temporal_runtime(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    topology_path: Path,
    link_state_path: Path,
    initial_segment: Dict[str, Any],
    validation_targets: Sequence[Dict[str, Any]],
    observe_selectors: Sequence[str],
    stream_progress: bool,
    events: List[Dict[str, Any]],
    progress_update: Callable[..., None] | None = None,
    phase_timing: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    def _progress(
        *,
        phase: str,
        event_type: str | None = None,
        phase_detail: Dict[str, Any] | None = None,
        epoch_context: Dict[str, Any] | None = None,
        runtime_epochs_completed: int | None = None,
        parameter_epochs_completed: int | None = None,
        write_events: bool = True,
    ) -> None:
        if progress_update is None:
            return
        progress_update(
            phase=phase,
            event_type=event_type,
            phase_detail=phase_detail,
            epoch_context=epoch_context,
            runtime_epochs_completed=runtime_epochs_completed,
            parameter_epochs_completed=parameter_epochs_completed,
            write_events=write_events,
        )

    def _write_live_timing_checkpoint(epoch_records: Sequence[Dict[str, Any]]) -> None:
        epoch_record_snapshot = list(epoch_records)
        rows = _timing_rows({"iterations": epoch_record_snapshot})
        runtime_report_payload = _current_runtime_report_payload(finalized=False)
        runtime_report_payload["iterations"] = epoch_record_snapshot
        runtime_report_payload["timing_summary"] = _live_timing_summary(
            epoch_record_snapshot
        )
        runtime_report_payload["prepared_bundle_runtime"] = {
            **dict(runtime_report_payload.get("prepared_bundle_runtime") or {}),
            "epoch_execution_queue": epoch_record_snapshot,
        }
        _write_json(
            run_dir / "timing.json",
            {
                "finalized": False,
                "phase_timing": dict(phase_timing or {}),
                "timing_analysis": _timing_analysis(
                    phase_timing=dict(phase_timing or {}),
                    timing_rows=rows,
                ),
                "timing_summary": _live_timing_summary(epoch_record_snapshot),
                "rows": rows,
            },
        )
        _write_timing_csv(run_dir / "timing.csv", rows)
        _write_json(
            run_dir / "real_runtime_report.json",
            runtime_report_payload,
        )
        _write_json(
            run_dir / "temporal_audit.json",
            _current_temporal_audit_payload(finalized=False),
        )

    deferred_checkpoint_errors: List[str] = []
    checkpoint_queue: "queue.Queue[Sequence[Dict[str, Any]] | None]" = queue.Queue()
    checkpoint_stop = threading.Event()

    def _checkpoint_worker() -> None:
        while True:
            item = checkpoint_queue.get()
            try:
                if item is None:
                    return
                started = time.perf_counter()
                _write_live_timing_checkpoint(item)
                if phase_timing is not None:
                    phase_timing["last_deferred_live_timing_checkpoint_wall_seconds"] = round(
                        time.perf_counter() - started,
                        6,
                    )
                    phase_timing["deferred_live_timing_checkpoint_count"] = int(
                        phase_timing.get("deferred_live_timing_checkpoint_count", 0)
                    ) + 1
            except Exception as exc:  # noqa: BLE001
                deferred_checkpoint_errors.append(str(exc))
            finally:
                checkpoint_queue.task_done()

    checkpoint_thread = threading.Thread(
        target=_checkpoint_worker,
        name="deferred-live-timing-checkpoint",
        daemon=True,
    )

    def _queue_live_timing_checkpoint(epoch_records: Sequence[Dict[str, Any]]) -> None:
        checkpoint_queue.put(list(epoch_records))

    def _flush_live_timing_checkpoints() -> None:
        checkpoint_queue.join()
        if not checkpoint_stop.is_set():
            checkpoint_stop.set()
            checkpoint_queue.put(None)
            checkpoint_thread.join(timeout=30.0)

    real_installer = MinimalRealLinuxMplsInstaller()
    network_executor = MinimalRealNetworkMutationExecutor(
        max_host_workers=_resolved_max_host_workers(args=args)
    )
    _progress(
        phase="runtime_initial_readiness",
        event_type="initial_runtime_readiness_started",
        phase_detail={
            "gateway_count": len(
                dict(
                    next(iter(initial_segment["context_by_slice"].values()))["snapshot"].planner_hints.get(
                        "gateway_ix_catalog",
                        {},
                    )
                )
            )
            if initial_segment["context_by_slice"]
            else 0,
        },
    )
    initial_runtime_readiness = _await_initial_real_runtime_readiness(
        gateway_ids=sorted(
            str(gateway_id)
            for gateway_id in dict(
                next(iter(initial_segment["context_by_slice"].values()))["snapshot"].planner_hints.get(
                    "gateway_ix_catalog", {}
                )
            )
        )
        if initial_segment["context_by_slice"]
        else [],
        validation_targets=list(validation_targets),
    )
    if phase_timing is not None:
        phase_timing["initial_runtime_readiness_wall_seconds"] = round(
            float(initial_runtime_readiness.get("wall_seconds", 0.0)),
            6,
        )
    _progress(
        phase=(
            "runtime_initial_readiness_completed"
            if initial_runtime_readiness.get("ready") is True
            else "runtime_initial_readiness_failed"
        ),
        event_type="initial_runtime_readiness_completed",
        phase_detail={
            "ready": initial_runtime_readiness.get("ready"),
            "wall_seconds": initial_runtime_readiness.get("wall_seconds"),
            "reachable_gateways": initial_runtime_readiness.get("reachable_gateway_count"),
        },
    )
    if initial_runtime_readiness.get("ready") is not True:
        return {
            "finalized": True,
            "mode": "live_continuous_temporal_runtime",
            "initial_runtime_readiness": initial_runtime_readiness,
            "iterations": [],
            "timing_summary": _live_timing_summary([]),
            "final_cleanup": {
                "node_local": None,
                "host_rollbacks": [],
                "all_cleanup_success": True,
            },
            "temporal_audit": {
                "finalized": True,
                "no_future_active_link_violation": True,
                "no_future_forwarding_state_violation": True,
                "parameter_epoch_without_topology_mutation": True,
                "runtime_epoch_has_expected_mutation_scope": True,
                "rollback_completed": True,
                "queue_exhausted_or_stopped_by_bound": "initial_runtime_readiness_timeout",
                "horizon_replenishment_status": {
                    "mode": "live_continuous",
                    "initial_segment_count": 1,
                    "replenishment_event_count": 0,
                    "status": "not_started",
                },
            },
            "prepared_bundle_runtime": {
                "segments": [
                    {
                        "segment_id": initial_segment["segment_id"],
                        "start_time_utc": initial_segment.get("start_time_utc"),
                        "end_time_utc": initial_segment.get("end_time_utc"),
                        "summary": dict(initial_segment.get("summary", {})),
                    }
                ],
                "replenishment_events": [],
            },
            "outcome": {
                "real_apply_success": False,
                "ping_success": False,
                "rollback_completed": True,
                "failure_reason": "initial_runtime_readiness_timeout",
            },
        }

    current_dynamic_state = dynamic_slot_state_from_link_state(load_link_state(link_state_path))
    current_state_id = f"link_state:{link_state_path.name}"
    segment_queue: List[Dict[str, Any]] = list(initial_segment["selected_epoch_queue"])
    pre_epoch_host_prewarm: Dict[str, Any] | None = None
    segment_records = [
        {
            "segment_id": initial_segment["segment_id"],
            "segment_kind": "initial",
            "start_time_utc": initial_segment.get("start_time_utc"),
            "end_time_utc": initial_segment.get("end_time_utc"),
            "summary": dict(initial_segment.get("summary", {})),
        }
    ]
    initial_context_keys = _segment_context_keys_for_epoch_queue(
        segment_id=str(initial_segment["segment_id"]),
        epoch_queue=segment_queue,
    )
    context_by_key: Dict[str, Dict[str, Any]] = {
        f"{initial_segment['segment_id']}:{slice_id}": context
        for slice_id, context in initial_segment["context_by_slice"].items()
        if f"{initial_segment['segment_id']}:{slice_id}" in initial_context_keys
    }
    sample_interval = _sample_interval_seconds(
        args=args,
        temporal_plan=initial_segment["temporal_plan"],
    )
    requested_total_horizon = _requested_horizon_seconds(
        args=args,
        temporal_plan=initial_segment["temporal_plan"],
    )
    segment_horizon = _initial_continuous_horizon_seconds(args=args) or requested_total_horizon
    run_start_time_utc = str(initial_segment.get("start_time_utc"))
    run_end_time_utc = _format_utc_timestamp(
        _parse_utc_timestamp(run_start_time_utc) + timedelta(seconds=requested_total_horizon)
    )
    next_replenishment_start_utc = _format_utc_timestamp(
        _parse_utc_timestamp(initial_segment.get("end_time_utc")) + timedelta(seconds=sample_interval)
    )
    epoch_execution_records: List[Dict[str, Any]] = []
    epoch_execution_details_jsonl = run_dir / "epoch_execution_details.jsonl"
    epoch_execution_details_jsonl.write_text("", encoding="utf-8")
    replenishment_events: List[Dict[str, Any]] = []
    host_history: List[Dict[str, Any]] = []
    applied_sessions: List[Any] = []
    active_session = None
    failure_reason: str | None = None
    final_runtime_connectivity_validation: Dict[str, Any] | None = None
    final_cleanup: Dict[str, Any] = {
        "node_local": None,
        "host_rollbacks": [],
        "all_cleanup_success": False,
    }
    runtime_count = 0
    parameter_count = 0
    stopped_by_bound = "queue_exhausted"
    base_wall_seconds: float | None = None
    first_activation_relative_seconds: float | None = None
    current_epoch_context: Dict[str, Any] | None = None
    failure_context: Dict[str, Any] | None = None
    no_future_active_link_violation = True
    no_future_forwarding_state_violation = True
    parameter_epoch_without_topology_mutation = True
    runtime_epoch_has_expected_mutation_scope = True

    def _current_temporal_audit_payload(*, finalized: bool) -> Dict[str, Any]:
        return {
            "finalized": finalized,
            "no_future_active_link_violation": no_future_active_link_violation,
            "no_future_forwarding_state_violation": no_future_forwarding_state_violation,
            "parameter_epoch_without_topology_mutation": parameter_epoch_without_topology_mutation,
            "runtime_epoch_has_expected_mutation_scope": runtime_epoch_has_expected_mutation_scope,
            "rollback_completed": (
                bool(final_cleanup.get("all_cleanup_success")) if finalized else False
            ),
            "queue_exhausted_or_stopped_by_bound": (
                stopped_by_bound if finalized else "in_progress"
            ),
            "horizon_replenishment_status": {
                "mode": "live_continuous",
                "initial_segment_count": 1,
                "replenishment_event_count": len(replenishment_events),
                "status": (
                    "live_replenishment_executed"
                    if replenishment_events
                    else ("not_started" if not bool(args.continuous) else "prepared_or_pending")
                ),
            },
        }

    def _current_runtime_report_payload(*, finalized: bool) -> Dict[str, Any]:
        return {
            "finalized": finalized,
            "mode": "live_continuous_temporal_runtime",
            "initial_runtime_readiness": initial_runtime_readiness,
            "iterations": list(epoch_execution_records),
            "timing_summary": _live_timing_summary(epoch_execution_records),
            "final_cleanup": dict(final_cleanup),
            "temporal_audit": _current_temporal_audit_payload(finalized=finalized),
            "prepared_bundle_runtime": {
                "segments": list(segment_records),
                "replenishment_events": list(replenishment_events),
                "epoch_execution_queue": list(epoch_execution_records),
                "epoch_execution_details_jsonl": str(epoch_execution_details_jsonl),
                "epoch_execution_record_model": "compact_with_detail_jsonl",
            },
            "final_runtime_connectivity_validation": _safe_dict(
                final_runtime_connectivity_validation
            ),
            "outcome": {
                "real_apply_success": bool(epoch_execution_records)
                and all(record["outcome"]["success"] for record in epoch_execution_records),
                "ping_success": (
                    bool(final_runtime_connectivity_validation.get("ping_success"))
                    if final_runtime_connectivity_validation is not None
                    else (
                        bool(epoch_execution_records)
                        and all(record["outcome"]["success"] for record in epoch_execution_records)
                    )
                ),
                "rollback_completed": bool(final_cleanup.get("all_cleanup_success"))
                if finalized
                else False,
                "failure_reason": failure_reason,
            },
        }

    def maybe_replenish(current_activation_relative: float) -> None:
        nonlocal next_replenishment_start_utc
        if not bool(args.continuous):
            return
        last_queue_activation = max(
            (
                float(entry.get("activation_relative_time_seconds", 0.0))
                for entry in segment_queue
            ),
            default=current_activation_relative,
        )
        remaining_horizon = last_queue_activation - current_activation_relative
        produced_horizon = (
            _parse_utc_timestamp(next_replenishment_start_utc)
            - _parse_utc_timestamp(run_start_time_utc)
        ).total_seconds()
        if remaining_horizon > float(args.replenish_threshold_seconds):
            return
        if produced_horizon >= requested_total_horizon:
            return
        replenish_horizon = min(
            segment_horizon,
            max(0.0, requested_total_horizon - produced_horizon),
        )
        if replenish_horizon <= 0.0:
            return
        segment_index = len(segment_records)
        events.append(
            {
                "timestamp_utc": _timestamp_utc(),
                "event_type": "horizon_replenishment_started",
                "segment_index": segment_index,
                "start_time_utc": next_replenishment_start_utc,
                "horizon_seconds": replenish_horizon,
            }
        )
        _progress(
            phase="runtime_replenishment",
            event_type="horizon_replenishment_started",
            phase_detail={
                "segment_index": segment_index,
                "start_time_utc": next_replenishment_start_utc,
                "horizon_seconds": round(replenish_horizon, 6),
            },
            runtime_epochs_completed=runtime_count,
            parameter_epochs_completed=parameter_count,
        )
        segment = _run_replenishment_preview(
            args=args,
            run_dir=run_dir,
            segment_index=segment_index,
            start_time_utc=next_replenishment_start_utc,
            horizon_seconds=replenish_horizon,
            topology_path=topology_path,
            link_state_path=link_state_path,
            run_start_time_utc=run_start_time_utc,
        )
        segment_records.append(
            {
                "segment_id": segment["segment_id"],
                "segment_kind": "replenished",
                "start_time_utc": segment.get("start_time_utc"),
                "end_time_utc": segment.get("end_time_utc"),
                "summary": dict(segment.get("summary", {})),
            }
        )
        accepted_entries: List[Dict[str, Any]] = []
        latest_queued_activation_utc = max(
            (
                str(entry.get("activation_time_utc"))
                for entry in segment_queue
                if entry.get("activation_time_utc")
            ),
            default=None,
        )
        for entry in segment["selected_epoch_queue"]:
            absolute_time = str(entry.get("activation_time_utc"))
            if absolute_time <= run_start_time_utc:
                continue
            if absolute_time > run_end_time_utc:
                continue
            if (
                latest_queued_activation_utc is not None
                and absolute_time <= latest_queued_activation_utc
            ):
                continue
            accepted_entry = dict(entry)
            accepted_entries.append(accepted_entry)
            segment_queue.append(accepted_entry)
        accepted_context_keys = _segment_context_keys_for_epoch_queue(
            segment_id=str(segment["segment_id"]),
            epoch_queue=accepted_entries,
        )
        context_by_key.update(
            {
                f"{segment['segment_id']}:{slice_id}": context
                for slice_id, context in segment["context_by_slice"].items()
                if f"{segment['segment_id']}:{slice_id}" in accepted_context_keys
            }
        )
        segment_queue.sort(
            key=lambda item: (
                float(item.get("activation_relative_time_seconds", 0.0)),
                str(item.get("segment_epoch_key")),
            )
        )
        replenishment_events.append(
            {
                "segment_id": segment["segment_id"],
                "start_time_utc": segment.get("start_time_utc"),
                "end_time_utc": segment.get("end_time_utc"),
                "summary": dict(segment.get("summary", {})),
            }
        )
        segment_end_time_utc = segment.get("end_time_utc")
        if segment_end_time_utc:
            next_replenishment_start_utc = _format_utc_timestamp(
                _parse_utc_timestamp(str(segment_end_time_utc))
                + timedelta(seconds=sample_interval)
            )
        events.append(
            {
                "timestamp_utc": _timestamp_utc(),
                "event_type": "horizon_replenishment_completed",
                "segment_id": segment["segment_id"],
                "orbit_sample_count": segment["summary"]["orbit_sample_count"],
                "runtime_epoch_count": segment["summary"]["runtime_epoch_count"],
                "parameter_epoch_count": segment["summary"]["parameter_epoch_count"],
            }
        )
        _progress(
            phase="runtime_replenishment_completed",
            event_type="horizon_replenishment_completed",
            phase_detail={
                "segment_id": segment["segment_id"],
                "runtime_epoch_count": segment["summary"]["runtime_epoch_count"],
                "parameter_epoch_count": segment["summary"]["parameter_epoch_count"],
            },
            runtime_epochs_completed=runtime_count,
            parameter_epochs_completed=parameter_count,
        )

    runtime_parameter_mutation_policy = make_runtime_parameter_mutation_policy(
        delay_threshold_ms=float(args.delay_threshold_ms),
        loss_threshold_pct=float(args.loss_threshold_pct),
        bandwidth_threshold_pct=float(args.bandwidth_threshold_pct),
        routing_cost_threshold=int(args.routing_cost_threshold),
    )

    first_runtime_entry = next(
        (
            dict(entry)
            for entry in segment_queue
            if str(entry.get("classification")) == "runtime_epoch"
        ),
        None,
    )
    if first_runtime_entry is not None:
        first_segment_key = str(first_runtime_entry.get("segment_epoch_key"))
        first_context = context_by_key.get(first_segment_key)
        if first_context is not None:
            selected_slice_ids_for_prewarm = sorted(
                {
                    str(entry.get("slice_id"))
                    for entry in segment_queue
                    if str(entry.get("slice_id"))
                }
            )
            access_prewarm_resource_scope_by_slot = (
                _selected_access_prewarm_resource_scope_by_slot(
                    snapshot_preview_path=run_dir / "snapshot_preview.json",
                    selected_slice_ids=selected_slice_ids_for_prewarm,
                )
            )
            access_prewarm_scope_resource_count = sum(
                len(resource_keys)
                for resource_keys in access_prewarm_resource_scope_by_slot.values()
            )
            first_snapshot = first_context["snapshot"]
            first_host_plan = build_network_mutation_plan(
                source_state=current_dynamic_state,
                source_state_id=current_state_id,
                target_snapshot=enrich_snapshot_runtime_parameters(first_snapshot),
                transition_name=(
                    f"{current_state_id}_to_{first_snapshot.time_slice.get('slice_id')}"
                ),
                runtime_parameter_mutation_policy=runtime_parameter_mutation_policy,
            )
            prewarm_slot_names = sorted(
                {
                    str(operation.get("slot_name"))
                    for operation in list(getattr(first_host_plan, "operations", []) or [])
                    if str(operation.get("slot_name"))
                }
            )
            _progress(
                phase="runtime_host_prewarm",
                event_type="host_runtime_prewarm_started",
                phase_detail={
                    "segment_epoch_key": first_segment_key,
                    "reconcile_slot_count": len(prewarm_slot_names),
                    "access_prewarm_scope_slot_count": len(
                        access_prewarm_resource_scope_by_slot
                    ),
                    "access_prewarm_scope_resource_count": access_prewarm_scope_resource_count,
                },
            )
            def _prewarm_progress_callback(update: Dict[str, Any]) -> None:
                detail = {
                    "segment_epoch_key": first_segment_key,
                    "reconcile_slot_count": len(prewarm_slot_names),
                    "prewarm_stage": str(update.get("stage", "runtime_prewarm_progress")),
                    "access_prewarm_scope_slot_count": len(
                        access_prewarm_resource_scope_by_slot
                    ),
                    "access_prewarm_scope_resource_count": access_prewarm_scope_resource_count,
                }
                for key in (
                    "prepared_now",
                    "prepare_wall_seconds",
                    "processed_slot_count",
                    "total_slot_count",
                    "requested_slot_count",
                    "reconciled_slot_count",
                    "activated_slot_count",
                    "deactivated_slot_count",
                    "remaining_slot_count",
                    "elapsed_wall_seconds",
                    "bridge_count",
                    "preconnected_endpoint_count",
                    "candidate_resource_count",
                    "candidate_endpoint_count",
                    "reconcile_wall_seconds",
                    "record_prewarm_reconcile",
                    "reconcile_mode",
                    "current_slot_name",
                ):
                    value = update.get(key)
                    if value is not None:
                        detail[key] = value
                slot_counts_by_type = update.get("slot_counts_by_type")
                if isinstance(slot_counts_by_type, dict) and slot_counts_by_type:
                    detail["slot_counts_by_type"] = dict(slot_counts_by_type)
                prepared_resource_counts = update.get("prepared_resource_counts")
                if isinstance(prepared_resource_counts, dict) and prepared_resource_counts:
                    detail["prepared_resource_counts"] = dict(prepared_resource_counts)
                reconcile_summary = update.get("reconcile_summary")
                if isinstance(reconcile_summary, dict) and reconcile_summary:
                    detail["reconcile_summary"] = dict(reconcile_summary)
                _progress(
                    phase="runtime_host_prewarm",
                    event_type="host_runtime_prewarm_progress",
                    phase_detail=detail,
                    runtime_epochs_completed=runtime_count,
                    parameter_epochs_completed=parameter_count,
                    write_events=False,
                )
            pre_epoch_host_prewarm = network_executor.prewarm(
                topology_path=topology_path,
                link_state_path=link_state_path,
                reconcile_slot_names=prewarm_slot_names,
                prepared_resource_scope_by_slot=access_prewarm_resource_scope_by_slot,
                progress_callback=_prewarm_progress_callback,
            )
            if phase_timing is not None:
                phase_timing["pre_epoch_host_runtime_prewarm_wall_seconds"] = round(
                    float(pre_epoch_host_prewarm.get("prewarm_wall_seconds", 0.0)),
                    6,
                )
            current_dynamic_state = dynamic_slot_state_from_link_state(
                load_link_state(link_state_path)
            )
            events.append(
                {
                    "timestamp_utc": _timestamp_utc(),
                    "event_type": "host_runtime_prewarm_completed",
                    "segment_epoch_key": first_segment_key,
                    "prewarm_wall_seconds": pre_epoch_host_prewarm.get(
                        "prewarm_wall_seconds"
                    ),
                    "manager_timing_breakdown": dict(
                        pre_epoch_host_prewarm.get("manager_timing_breakdown", {})
                    ),
                    "reconcile_slot_count": len(prewarm_slot_names),
                }
            )
            _progress(
                phase="runtime_host_prewarm_completed",
                event_type="host_runtime_prewarm_completed",
                phase_detail={
                    "segment_epoch_key": first_segment_key,
                    "prewarm_wall_seconds": pre_epoch_host_prewarm.get("prewarm_wall_seconds"),
                    "reconcile_slot_count": len(prewarm_slot_names),
                    "access_prewarm_scope_slot_count": len(
                        access_prewarm_resource_scope_by_slot
                    ),
                    "access_prewarm_scope_resource_count": access_prewarm_scope_resource_count,
                },
            )

    checkpoint_thread.start()
    try:
        while segment_queue:
            next_entry = dict(segment_queue.pop(0))
            classification = str(next_entry.get("classification"))
            current_epoch_context = {
                "segment_id": next_entry.get("segment_id"),
                "segment_epoch_key": next_entry.get("segment_epoch_key"),
                "classification": classification,
                "activation_time_utc": next_entry.get("activation_time_utc"),
                "activation_relative_time_seconds": next_entry.get(
                    "activation_relative_time_seconds"
                ),
                "slice_id": next_entry.get("slice_id"),
                "epoch_id": next_entry.get("epoch_id"),
                "runtime_epochs_completed": runtime_count,
                "parameter_epochs_completed": parameter_count,
            }
            if args.max_epochs is not None and (
                runtime_count + parameter_count >= int(args.max_epochs)
            ):
                stopped_by_bound = "max_epochs_reached"
                break
            if classification == "runtime_epoch":
                if args.max_runtime_epochs is not None and runtime_count >= int(args.max_runtime_epochs):
                    stopped_by_bound = "max_runtime_epochs_reached"
                    maybe_replenish(
                        float(next_entry.get("activation_relative_time_seconds", 0.0))
                    )
                    continue
            elif classification == "parameter_epoch":
                if (
                    args.max_parameter_epochs is not None
                    and parameter_count >= int(args.max_parameter_epochs)
                ):
                    stopped_by_bound = "max_parameter_epochs_reached"
                    maybe_replenish(
                        float(next_entry.get("activation_relative_time_seconds", 0.0))
                    )
                    continue

            activation_relative = float(next_entry.get("activation_relative_time_seconds", 0.0))
            if base_wall_seconds is None:
                base_wall_seconds = time.perf_counter()
                first_activation_relative_seconds = activation_relative
            scheduled_offset = activation_relative - float(first_activation_relative_seconds or 0.0)
            scheduled_wall = float(base_wall_seconds) + scheduled_offset
            sleep_seconds = scheduled_wall - time.perf_counter()
            _progress(
                phase="scheduler_wait",
                event_type="epoch_scheduled",
                phase_detail={
                    "sleep_seconds": round(max(0.0, sleep_seconds), 6),
                    "activation_time_utc": next_entry.get("activation_time_utc"),
                    "activation_relative_time_seconds": activation_relative,
                    "epoch_type": classification,
                },
                epoch_context=current_epoch_context,
                runtime_epochs_completed=runtime_count,
                parameter_epochs_completed=parameter_count,
            )
            if sleep_seconds > 0.0:
                time.sleep(sleep_seconds)
            apply_started_wall = time.perf_counter()
            apply_started_epoch = time.time()
            epoch_timing_wall_marks: Dict[str, float | None] = {
                field: None for field in EPOCH_TIMING_MARK_FIELDS
            }
            epoch_timing_null_explanations: Dict[str, str] = {}
            epoch_timing_wall_marks["actual_epoch_start_time_utc"] = apply_started_wall
            activation_lag = max(0.0, apply_started_wall - scheduled_wall)
            deadline_miss = activation_lag > DEADLINE_MISS_THRESHOLD_SECONDS
            if deadline_miss:
                if bool(args.strict_activation_deadlines):
                    failure_reason = (
                        f"deadline_miss:{next_entry.get('epoch_id')}:{activation_lag:.3f}s"
                    )
                    epoch_execution_records.append(
                        {
                            "epoch_id": next_entry.get("epoch_id"),
                            "epoch_type": classification,
                            "segment_id": next_entry.get("segment_id"),
                            "segment_epoch_key": next_entry.get("segment_epoch_key"),
                            "activation_time_utc": next_entry.get("activation_time_utc"),
                            "activation_relative_time_seconds": activation_relative,
                            "activation_group_id": next_entry.get("activation_group_id"),
                            "execution_policy": next_entry.get("execution_policy"),
                            "apply_start_time_utc": _format_utc_timestamp(
                                datetime.fromtimestamp(apply_started_epoch, tz=timezone.utc)
                            ),
                            "apply_end_time_utc": _format_utc_timestamp(
                                datetime.fromtimestamp(apply_started_epoch, tz=timezone.utc)
                            ),
                            "activation_lag_seconds": round(activation_lag, 6),
                            "deadline_miss": True,
                            "outcome": {
                                "success": False,
                                "failure_reason": failure_reason,
                            },
                            "timing": {},
                        }
                    )
                    _write_live_timing_checkpoint(epoch_execution_records)
                    break

            segment_key = str(next_entry.get("segment_epoch_key"))
            context = context_by_key.pop(segment_key, None)
            if context is None:
                raise RuntimeError(f"missing slice context for {segment_key}")
            snapshot = context["snapshot"]
            enriched_snapshot = enrich_snapshot_runtime_parameters(snapshot)
            host_plan = build_network_mutation_plan(
                source_state=current_dynamic_state,
                source_state_id=current_state_id,
                target_snapshot=enriched_snapshot,
                transition_name=f"{current_state_id}_to_{snapshot.time_slice.get('slice_id')}",
                runtime_parameter_mutation_policy=runtime_parameter_mutation_policy,
            )
            planned_operations = list(getattr(host_plan, "operations", []) or [])
            parameter_safety_skipped_operations = 0
            if classification == "parameter_epoch":
                non_parameter_operations = [
                    operation
                    for operation in planned_operations
                    if str(operation.get("operation")) != "update_slot_parameters"
                ]
                parameter_update_operations = [
                    operation
                    for operation in planned_operations
                    if str(operation.get("operation")) == "update_slot_parameters"
                ]
                if not non_parameter_operations and len(parameter_update_operations) != len(planned_operations):
                    parameter_safety_skipped_operations = len(planned_operations) - len(
                        parameter_update_operations
                    )
                    host_plan = NetworkMutationPlan(
                        schema_version=host_plan.schema_version,
                        mutation_id=f"{host_plan.mutation_id}-safeparam",
                        source_snapshot=dict(host_plan.source_snapshot),
                        target_scope=dict(host_plan.target_scope),
                        execution_boundary=dict(host_plan.execution_boundary),
                        operations=[dict(operation) for operation in parameter_update_operations],
                        validation_expectations=dict(host_plan.validation_expectations),
                    )
                    planned_operations = list(parameter_update_operations)
            non_parameter_operations = [
                operation
                for operation in planned_operations
                if str(operation.get("operation")) != "update_slot_parameters"
            ]
            escalated = classification == "parameter_epoch" and bool(non_parameter_operations)
            effective_epoch_type = "runtime_epoch" if escalated else classification
            noop_parameter_epoch = (
                classification == "parameter_epoch"
                and effective_epoch_type == "parameter_epoch"
                and not planned_operations
            )
            _progress(
                phase=f"epoch:{effective_epoch_type}:host_apply",
                event_type="epoch_apply_started",
                phase_detail={
                    "epoch_id": next_entry.get("epoch_id"),
                    "segment_epoch_key": segment_key,
                    "scheduled_activation_time": next_entry.get("activation_time_utc"),
                    "activation_lag_seconds": round(activation_lag, 6),
                    "deadline_miss": deadline_miss,
                    "planned_operation_count": len(planned_operations),
                    "escalated_to_runtime_epoch": escalated,
                    "noop_parameter_epoch": noop_parameter_epoch,
                },
                epoch_context={
                    **dict(current_epoch_context or {}),
                    "effective_epoch_type": effective_epoch_type,
                },
                runtime_epochs_completed=runtime_count,
                parameter_epochs_completed=parameter_count,
            )
            pre_transition_connectivity = None
            pre_transition_paths: List[Dict[str, Any]] = []
            node_local_rollback = None
            if active_session is not None:
                should_run_pre_transition_probe = _should_run_sampled_validation(
                    epoch_index=runtime_count + 1,
                    interval=int(args.pre_transition_probe_interval),
                    always_first=False,
                )
                if should_run_pre_transition_probe:
                    probe_started = time.perf_counter()
                    epoch_timing_wall_marks["blocking_verification_start"] = probe_started
                    pre_transition_connectivity = real_installer.probe_connectivity(active_session)
                    probe_completed = time.perf_counter()
                    epoch_timing_wall_marks["blocking_verification_end"] = probe_completed
                    pre_transition_probe_wall_seconds = probe_completed - probe_started
                else:
                    pre_transition_connectivity = _skipped_connectivity_summary(
                        slice_id=str(snapshot.time_slice.get("slice_id")),
                        skip_reason="sampled_pre_transition_probe_skipped",
                    )
                    pre_transition_probe_wall_seconds = 0.0
                    epoch_timing_null_explanations["blocking_verification"] = (
                        "pre-transition connectivity probe was skipped by sampling policy"
                    )
                pre_transition_paths = list(
                    active_session.timing.get("flow_path_observations") or []
                )
                if str(args.runtime_mode) == "correctness" and effective_epoch_type == "runtime_epoch":
                    cleanup_started = time.perf_counter()
                    epoch_timing_wall_marks["pre_apply_cleanup_start"] = cleanup_started
                    node_local_rollback = real_installer.rollback_session(
                        active_session,
                        max_node_workers=int(args.max_node_workers),
                    )
                    epoch_timing_wall_marks["pre_apply_cleanup_end"] = time.perf_counter()
                    if node_local_rollback["rollback_verification"]["cleanup_success"] is not True:
                        raise RuntimeError(
                            "node-local rollback failed before the next runtime epoch"
                        )
                    active_session = None
            else:
                pre_transition_probe_wall_seconds = 0.0

            reconcile_slot_names = _reconcile_slot_names_for_epoch(
                planned_operations=planned_operations,
                effective_epoch_type=effective_epoch_type,
            )
            host_apply_started_at = time.perf_counter()
            epoch_timing_wall_marks["data_plane_apply_start_utc"] = host_apply_started_at
            epoch_timing_wall_marks["host_apply_start"] = host_apply_started_at
            if noop_parameter_epoch:
                host_apply = {
                    "boundary": {
                        "consumer": "host_network_runtime_manager",
                        "envelope_kind": "noop_parameter_epoch",
                        "concurrency_model": "skipped_noop_parameter_epoch",
                    },
                    "apply_results": [],
                    "outcome": {
                        "apply_success": True,
                        "operation_count": 0,
                        "applied_operations": [],
                        "delta_summary": {
                            "managed_slot_count": 0,
                            "expected_operations": 0,
                        },
                        "preparation_summary": {
                            "prepare_summary": {},
                            "reconcile_summary": {},
                        },
                        "operation_timing_summary": {},
                        "manager_timing_breakdown": {},
                    },
                }
            else:
                host_apply_result = network_executor.apply(
                    topology_path=topology_path,
                    link_state_path=link_state_path,
                    network_mutation_plan=host_plan,
                    reconcile_slot_names=reconcile_slot_names,
                )
                host_apply = host_apply_result.to_dict()
            host_apply_wall_seconds = time.perf_counter() - host_apply_started_at
            host_apply_completed_wall = time.perf_counter()
            epoch_timing_wall_marks["host_apply_end"] = host_apply_completed_wall
            host_apply_completed_epoch = time.time()
            if host_apply["outcome"]["apply_success"] is not True:
                raise RuntimeError(
                    f"host-side epoch apply failed for {segment_key}"
                )
            if not noop_parameter_epoch:
                host_history.append(
                    {
                        "epoch_type": effective_epoch_type,
                        "segment_epoch_key": segment_key,
                        "host_plan": host_plan,
                        "applied_operations": _safe_list(
                            host_apply["outcome"].get("applied_operations")
                        ),
                    }
                )

            node_local_execution_public: Dict[str, Any] | None = None
            node_local_apply_wall_seconds = 0.0
            node_local_verify_wall_seconds = 0.0
            node_local_warmup_wall_seconds = 0.0
            node_local_ping_wall_seconds = 0.0
            node_local_incremental_cleanup_wall_seconds = 0.0
            packet_observation_wall_seconds = 0.0
            parameter_refresh_recovery_used = False
            parameter_qdisc_apply_seconds = 0.0
            parameter_metadata_update_seconds = 0.0
            parameter_dirty_reconcile_seconds = 0.0
            targeted_validation_seconds = 0.0
            recovery_seconds = 0.0
            recovery_reason = None
            recovery_operations: Dict[str, Any] = {}
            recovery_changed_topology = False
            recovery_changed_forwarding = False
            recovery_escalation_more_appropriate = False
            packet_observation = {
                "enabled": bool(observe_selectors),
                "paths": [],
            }
            post_transition_paths: List[Dict[str, Any]] = []
            manager_timing = _safe_dict(
                host_apply.get("outcome", {}).get("manager_timing_breakdown")
            )
            if effective_epoch_type == "parameter_epoch":
                parameter_qdisc_apply_seconds = round(
                    sum(
                        float(result.get("qdisc_apply_wall_seconds", 0.0))
                        for result in host_apply.get("apply_results", [])
                        if str(result.get("operation")) == "update_slot_parameters"
                    ),
                    6,
                )
                parameter_metadata_update_seconds = round(
                    sum(
                        float(result.get("metadata_update_wall_seconds", 0.0))
                        for result in host_apply.get("apply_results", [])
                        if str(result.get("operation")) == "update_slot_parameters"
                    )
                    + float(
                        manager_timing.get("batched_state_persist_wall_seconds", 0.0)
                    ),
                    6,
                )
                parameter_dirty_reconcile_seconds = float(
                    manager_timing.get("runtime_reconcile_wall_seconds", 0.0)
                )
            if effective_epoch_type == "runtime_epoch":
                should_run_runtime_epoch_validation = _should_run_sampled_validation(
                    epoch_index=runtime_count + 1,
                    interval=int(args.runtime_epoch_validation_interval),
                    always_first=True,
                )
                _progress(
                    phase="epoch:runtime_epoch:node_local_apply",
                    event_type="runtime_epoch_node_local_apply_started",
                    phase_detail={
                        "epoch_id": next_entry.get("epoch_id"),
                        "segment_epoch_key": segment_key,
                        "host_apply_wall_seconds": round(host_apply_wall_seconds, 6),
                        "validation_scheduled": should_run_runtime_epoch_validation,
                        "validation_scope": str(args.runtime_epoch_validation_scope),
                    },
                    epoch_context={
                        **dict(current_epoch_context or {}),
                        "effective_epoch_type": effective_epoch_type,
                    },
                    runtime_epochs_completed=runtime_count,
                    parameter_epochs_completed=parameter_count,
                )
                transition_stub = {"host_plan": host_plan}
                node_local_apply_started_wall = time.perf_counter()
                epoch_timing_wall_marks["node_local_apply_start"] = (
                    node_local_apply_started_wall
                )
                active_session = real_installer.apply_and_validate(
                    topology_path=topology_path,
                    topology_snapshot=snapshot,
                    runtime_update_plan=context["runtime_update_plan"],
                    distribution_batch=context["distribution_batch"],
                    require_preview_validation=True,
                    host_runtime_summary=host_apply["boundary"],
                    existing_session=active_session,
                    max_node_workers=int(args.max_node_workers),
                    runtime_mode=str(args.runtime_mode),
                    warmup_ut_ids=_warmup_ut_ids_for_transition(transition_stub),
                    ping_validation_mode=(
                        "full" if should_run_runtime_epoch_validation else "none"
                    ),
                    ping_validation_scope=str(args.runtime_epoch_validation_scope),
                )
                applied_sessions.append(active_session)
                if should_run_runtime_epoch_validation:
                    post_transition_connectivity = _connectivity_summary(
                        active_session.ping_results
                    )
                else:
                    post_transition_connectivity = _skipped_connectivity_summary(
                        slice_id=str(snapshot.time_slice.get("slice_id")),
                        skip_reason="sampled_runtime_epoch_validation_skipped",
                    )
                observation_started = time.perf_counter()
                packet_observation = real_installer.observe_packet_path(
                    active_session,
                    observe_selectors=observe_selectors,
                )
                packet_observation_wall_seconds = time.perf_counter() - observation_started
                node_local_execution_public = active_session.to_public_dict()
                post_transition_paths = _safe_list(
                    active_session.timing.get("flow_path_observations") or []
                )
                node_local_apply_wall_seconds = float(active_session.timing.get("apply_wall_seconds", 0.0))
                node_local_verify_wall_seconds = float(active_session.timing.get("verify_wall_seconds", 0.0))
                node_local_warmup_wall_seconds = float(
                    active_session.timing.get("access_link_warmup_wall_seconds", 0.0)
                )
                node_local_ping_wall_seconds = float(active_session.timing.get("ping_wall_seconds", 0.0))
                node_local_incremental_cleanup_wall_seconds = float(
                    active_session.timing.get("incremental_cleanup_wall_seconds", 0.0)
                )
                _record_node_local_timing_marks(
                    epoch_timing_wall_marks,
                    node_local_apply_started_wall=node_local_apply_started_wall,
                    node_local_apply_wall_seconds=node_local_apply_wall_seconds,
                    node_local_incremental_cleanup_wall_seconds=(
                        node_local_incremental_cleanup_wall_seconds
                    ),
                    node_local_verify_wall_seconds=node_local_verify_wall_seconds,
                )
                if epoch_timing_wall_marks["data_plane_ready_time_utc"] is not None:
                    epoch_timing_wall_marks["post_apply_validation_start"] = float(
                        epoch_timing_wall_marks["data_plane_ready_time_utc"]
                    )
                    epoch_timing_wall_marks["post_apply_validation_end"] = (
                        time.perf_counter()
                    )
                runtime_count += 1
            else:
                epoch_timing_wall_marks["data_plane_ready_time_utc"] = (
                    host_apply_completed_wall
                )
                if noop_parameter_epoch:
                    post_transition_connectivity = {
                        "slice_id": snapshot.time_slice.get("slice_id"),
                        "flow_results": [],
                        "ping_success": True,
                        "wall_seconds": 0.0,
                        "attempts": [],
                        "attempt_count": 0,
                        "retry_recovered": False,
                        "validation_skipped": True,
                        "skip_reason": "noop_parameter_epoch",
                    }
                    if active_session is not None:
                        post_transition_paths = _safe_list(
                            active_session.timing.get("flow_path_observations") or []
                        )
                    _progress(
                        phase="epoch:parameter_epoch:noop_skipped",
                        event_type="parameter_epoch_noop_skipped",
                        phase_detail={
                            "epoch_id": next_entry.get("epoch_id"),
                            "segment_epoch_key": segment_key,
                            "host_apply_wall_seconds": round(host_apply_wall_seconds, 6),
                            "parameter_safety_skipped_operations": parameter_safety_skipped_operations,
                        },
                        epoch_context={
                            **dict(current_epoch_context or {}),
                            "effective_epoch_type": effective_epoch_type,
                        },
                        runtime_epochs_completed=runtime_count,
                        parameter_epochs_completed=parameter_count,
                    )
                else:
                    _progress(
                        phase="epoch:parameter_epoch:targeted_validation",
                        event_type="parameter_epoch_targeted_validation_started",
                        phase_detail={
                            "epoch_id": next_entry.get("epoch_id"),
                            "segment_epoch_key": segment_key,
                            "host_apply_wall_seconds": round(host_apply_wall_seconds, 6),
                            "validation_scheduled": _should_run_sampled_validation(
                                epoch_index=parameter_count + 1,
                                interval=int(args.parameter_epoch_validation_interval),
                                always_first=True,
                            ),
                        },
                        epoch_context={
                            **dict(current_epoch_context or {}),
                            "effective_epoch_type": effective_epoch_type,
                        },
                        runtime_epochs_completed=runtime_count,
                        parameter_epochs_completed=parameter_count,
                    )
                    should_run_parameter_epoch_validation = _should_run_sampled_validation(
                        epoch_index=parameter_count + 1,
                        interval=int(args.parameter_epoch_validation_interval),
                        always_first=True,
                    )
                    if should_run_parameter_epoch_validation:
                        targeted_validation_started_at = time.perf_counter()
                        epoch_timing_wall_marks["post_apply_validation_start"] = (
                            targeted_validation_started_at
                        )
                        post_transition_connectivity = (
                            _probe_connectivity_with_retries(
                                installer=real_installer,
                                session=active_session,
                                attempts=2,
                                primary_only=True,
                                retry_delay_seconds=0.5,
                            )
                            if active_session is not None
                            else {
                                "slice_id": snapshot.time_slice.get("slice_id"),
                                "flow_results": [],
                                "ping_success": True,
                                "wall_seconds": 0.0,
                                "attempts": [],
                                "attempt_count": 0,
                                "retry_recovered": False,
                            }
                        )
                        targeted_validation_completed_at = time.perf_counter()
                        targeted_validation_seconds = (
                            targeted_validation_completed_at
                            - targeted_validation_started_at
                        )
                        epoch_timing_wall_marks["post_apply_validation_end"] = (
                            targeted_validation_completed_at
                        )
                        node_local_ping_wall_seconds = float(
                            post_transition_connectivity.get("wall_seconds", 0.0)
                        )
                    else:
                        post_transition_connectivity = _skipped_connectivity_summary(
                            slice_id=str(snapshot.time_slice.get("slice_id")),
                            skip_reason="sampled_parameter_epoch_validation_skipped",
                        )
                        targeted_validation_seconds = 0.0
                        node_local_ping_wall_seconds = 0.0
                        epoch_timing_null_explanations["post_apply_validation"] = (
                            "parameter epoch targeted validation was skipped by sampling policy"
                        )
                    if (
                        should_run_parameter_epoch_validation
                        and active_session is not None
                        and post_transition_connectivity.get("ping_success") is not True
                    ):
                        _progress(
                            phase="epoch:parameter_epoch:recovery",
                            event_type="parameter_epoch_recovery_started",
                            phase_detail={
                                "epoch_id": next_entry.get("epoch_id"),
                                "segment_epoch_key": segment_key,
                                "reason": "targeted_parameter_validation_failed",
                            },
                            epoch_context={
                                **dict(current_epoch_context or {}),
                                "effective_epoch_type": effective_epoch_type,
                            },
                            runtime_epochs_completed=runtime_count,
                            parameter_epochs_completed=parameter_count,
                        )
                        recovery_started_at = time.perf_counter()
                        epoch_timing_wall_marks["node_local_apply_start"] = (
                            recovery_started_at
                        )
                        recovery_reason = "targeted_parameter_validation_failed"
                        refreshed_session = real_installer.apply_and_validate(
                            topology_path=topology_path,
                            topology_snapshot=snapshot,
                            runtime_update_plan=context["runtime_update_plan"],
                            distribution_batch=context["distribution_batch"],
                            require_preview_validation=True,
                            host_runtime_summary=host_apply["boundary"],
                            max_node_workers=int(args.max_node_workers),
                            runtime_mode=str(args.runtime_mode),
                            warmup_ut_ids=[],
                        )
                        recovery_seconds = time.perf_counter() - recovery_started_at
                        applied_sessions.append(refreshed_session)
                        active_session = refreshed_session
                        post_transition_connectivity = _connectivity_summary(
                            refreshed_session.ping_results
                        )
                        node_local_execution_public = refreshed_session.to_public_dict()
                        node_local_apply_wall_seconds = float(
                            refreshed_session.timing.get("apply_wall_seconds", 0.0)
                        )
                        node_local_verify_wall_seconds = float(
                            refreshed_session.timing.get("verify_wall_seconds", 0.0)
                        )
                        node_local_warmup_wall_seconds = float(
                            refreshed_session.timing.get("access_link_warmup_wall_seconds", 0.0)
                        )
                        node_local_ping_wall_seconds = float(
                            refreshed_session.timing.get("ping_wall_seconds", 0.0)
                        )
                        node_local_incremental_cleanup_wall_seconds = float(
                            refreshed_session.timing.get("incremental_cleanup_wall_seconds", 0.0)
                        )
                        _record_node_local_timing_marks(
                            epoch_timing_wall_marks,
                            node_local_apply_started_wall=recovery_started_at,
                            node_local_apply_wall_seconds=node_local_apply_wall_seconds,
                            node_local_incremental_cleanup_wall_seconds=(
                                node_local_incremental_cleanup_wall_seconds
                            ),
                            node_local_verify_wall_seconds=node_local_verify_wall_seconds,
                        )
                        post_transition_paths = list(
                            refreshed_session.timing.get("flow_path_observations") or []
                        )
                        parameter_refresh_recovery_used = True
                        recovery_operations = {
                            "runtime_update_action_count": _count_runtime_update_actions(
                                context["runtime_update_plan"].to_dict()
                            ),
                            "node_apply_batch_count": len(
                                _safe_list(
                                    refreshed_session.preflight.get("node_local_batch_plan", {}).get(
                                        "apply", []
                                    )
                                )
                            ),
                            "node_verify_batch_count": len(
                                _safe_list(
                                    refreshed_session.preflight.get("node_local_batch_plan", {}).get(
                                        "verify", []
                                    )
                                )
                            ),
                            "ping_validation_target_count": len(
                                _safe_list(refreshed_session.ping_results)
                            ),
                        }
                    if active_session is not None:
                        observation_started = time.perf_counter()
                        if epoch_timing_wall_marks.get("post_apply_validation_start") is None:
                            epoch_timing_wall_marks["post_apply_validation_start"] = (
                                observation_started
                            )
                        packet_observation = real_installer.observe_packet_path(
                            active_session,
                            observe_selectors=observe_selectors,
                        )
                        observation_completed_at = time.perf_counter()
                        packet_observation_wall_seconds = (
                            observation_completed_at - observation_started
                        )
                        epoch_timing_wall_marks["post_apply_validation_end"] = (
                            observation_completed_at
                        )
                        if parameter_refresh_recovery_used and node_local_execution_public is None:
                            node_local_execution_public = active_session.to_public_dict()
                        post_transition_paths = _safe_list(
                            active_session.timing.get("flow_path_observations") or []
                        )
                    parameter_count += 1

            if not noop_parameter_epoch:
                current_dynamic_state = dynamic_slot_state_from_link_state(
                    load_link_state(link_state_path)
                )
            current_state_id = (
                f"live_state:{snapshot.snapshot_id}:{next_entry.get('epoch_id')}"
            )
            apply_end_wall = time.perf_counter()
            apply_end_epoch = time.time()
            epoch_timing_wall_marks["epoch_end_time_utc"] = apply_end_wall
            if epoch_timing_wall_marks.get("data_plane_ready_time_utc") is None:
                epoch_timing_wall_marks["data_plane_ready_time_utc"] = host_apply_completed_wall
            transition_wall_seconds = apply_end_wall - apply_started_wall
            applied_operation_count = len(
                _safe_list(host_apply["outcome"].get("applied_operations"))
            )
            skipped_operation_count = max(
                0,
                int(host_apply["outcome"].get("delta_summary", {}).get("managed_slot_count", 0))
                - int(host_apply["outcome"].get("operation_count", applied_operation_count)),
            ) + int(parameter_safety_skipped_operations)
            topology_mutation_count = (
                len(non_parameter_operations) if effective_epoch_type == "parameter_epoch" else applied_operation_count
            )
            forwarding_mutation_count = (
                _count_runtime_update_actions(context["runtime_update_plan"].to_dict())
                if effective_epoch_type == "runtime_epoch"
                else 0
            )
            if effective_epoch_type == "parameter_epoch" and (topology_mutation_count or forwarding_mutation_count):
                parameter_epoch_without_topology_mutation = False
            if effective_epoch_type == "runtime_epoch" and applied_operation_count == 0 and forwarding_mutation_count == 0:
                runtime_epoch_has_expected_mutation_scope = False

            epoch_record = {
                "sequence_index": len(epoch_execution_records) + 1,
                "epoch_id": next_entry.get("epoch_id"),
                "epoch_type": effective_epoch_type,
                "original_epoch_type": classification,
                "segment_id": next_entry.get("segment_id"),
                "segment_epoch_key": segment_key,
                "slice_id": snapshot.time_slice.get("slice_id"),
                "to_slice_id": snapshot.time_slice.get("slice_id"),
                "loop_iteration": 1,
                "simulated_time_seconds": activation_relative,
                "snapshot_id": snapshot.snapshot_id,
                "activation_time_utc": next_entry.get("activation_time_utc"),
                "activation_relative_time_seconds": activation_relative,
                "activation_group_id": next_entry.get("activation_group_id"),
                "execution_policy": next_entry.get("execution_policy"),
                "scheduled_activation_time": next_entry.get("activation_time_utc"),
                "apply_start_time_utc": _format_utc_timestamp(
                    datetime.fromtimestamp(apply_started_epoch, tz=timezone.utc)
                ),
                "apply_end_time_utc": _format_utc_timestamp(
                    datetime.fromtimestamp(apply_end_epoch, tz=timezone.utc)
                ),
                "epoch_timing_marks": None,
                "activation_lag_seconds": round(activation_lag, 6),
                "deadline_miss": deadline_miss,
                "trigger_reasons": _safe_list(next_entry.get("trigger_reasons")),
                "scheduled_topology_mutation": classification == "runtime_epoch",
                "escalated_to_runtime_epoch": escalated,
                "noop_skipped": noop_parameter_epoch,
                "parameter_refresh_recovery_used": parameter_refresh_recovery_used,
                "parameter_refresh_recovery": {
                    "used": parameter_refresh_recovery_used,
                    "reason": recovery_reason,
                    "wall_seconds": round(recovery_seconds, 6),
                    "operations": recovery_operations,
                    "changed_topology": recovery_changed_topology,
                    "changed_forwarding": recovery_changed_forwarding,
                    "escalation_more_appropriate": recovery_escalation_more_appropriate,
                },
                "pre_transition_connectivity": pre_transition_connectivity,
                "post_transition_connectivity": post_transition_connectivity,
                "pre_transition_paths": pre_transition_paths,
                "post_transition_paths": post_transition_paths,
                "packet_observation": packet_observation,
                "host_mutation_execution": host_apply,
                "node_local_execution": node_local_execution_public,
                "node_local_rollback_before_transition": node_local_rollback,
                "node_local_reapplied_flow_ids": _safe_list(
                    (node_local_execution_public or {})
                    .get("target_slice", {})
                    .get("reapplied_flow_ids")
                ),
                "node_local_held_flow_ids": _safe_list(
                    (node_local_execution_public or {})
                    .get("target_slice", {})
                    .get("held_flow_ids")
                ),
                "applied_operations": applied_operation_count,
                "skipped_operations": skipped_operation_count,
                "topology_mutation_count": topology_mutation_count,
                "forwarding_mutation_count": forwarding_mutation_count,
                "network_mutation_count": int(host_apply["outcome"].get("operation_count", 0)),
                "host_prepare_summary": _safe_dict(
                    (host_apply["outcome"].get("preparation_summary") or {}).get(
                        "prepare_summary"
                    )
                ),
                "host_reconcile_summary": _safe_dict(
                    (host_apply["outcome"].get("preparation_summary") or {}).get(
                        "reconcile_summary"
                    )
                ),
                "host_operation_timing_summary": _safe_dict(
                    host_apply["outcome"].get("operation_timing_summary")
                ),
                "host_typed_operation_metrics": _safe_dict(
                    host_apply["outcome"].get("typed_operation_metrics")
                ),
                "timing": {
                    "transition_wall_seconds": round(transition_wall_seconds, 6),
                    "network_mutation_plan_wall_seconds": round(host_apply_wall_seconds, 6),
                    "host_runtime_prepare_wall_seconds": float(
                        manager_timing.get("runtime_prepare_wall_seconds", 0.0)
                    ),
                    "host_runtime_reconcile_wall_seconds": float(
                        manager_timing.get("runtime_reconcile_wall_seconds", 0.0)
                    ),
                    "host_operation_apply_wall_seconds": float(
                        manager_timing.get("operation_apply_wall_seconds", 0.0)
                    ),
                    "pre_transition_connectivity_probe_wall_seconds": round(
                        float(pre_transition_probe_wall_seconds),
                        6,
                    ),
                    "post_transition_connectivity_probe_wall_seconds": round(
                        float(node_local_ping_wall_seconds),
                        6,
                    ),
                    "node_local_apply_wall_seconds": round(node_local_apply_wall_seconds, 6),
                    "node_local_verify_wall_seconds": round(node_local_verify_wall_seconds, 6),
                    "node_local_incremental_cleanup_wall_seconds": round(
                        node_local_incremental_cleanup_wall_seconds,
                        6,
                    ),
                    "node_local_reapplied_flow_count": len(
                        _safe_list(
                            (node_local_execution_public or {})
                            .get("target_slice", {})
                            .get("reapplied_flow_ids")
                        )
                    ),
                    "node_local_held_flow_count": len(
                        _safe_list(
                            (node_local_execution_public or {})
                            .get("target_slice", {})
                            .get("held_flow_ids")
                        )
                    ),
                    "access_link_warmup_wall_seconds": round(node_local_warmup_wall_seconds, 6),
                    "packet_observation_wall_seconds": round(packet_observation_wall_seconds, 6),
                    "parameter_qdisc_apply_seconds": round(
                        float(parameter_qdisc_apply_seconds),
                        6,
                    ),
                    "parameter_metadata_update_seconds": round(
                        float(parameter_metadata_update_seconds),
                        6,
                    ),
                    "parameter_dirty_reconcile_seconds": round(
                        float(parameter_dirty_reconcile_seconds),
                        6,
                    ),
                    "targeted_validation_seconds": round(
                        float(targeted_validation_seconds),
                        6,
                    ),
                    "recovery_seconds": round(float(recovery_seconds), 6),
                    "host_reconcile_requested_slot_count": (
                        None
                        if reconcile_slot_names is None
                        else len(reconcile_slot_names)
                    ),
                    "audit_write_seconds": 0.0,
                    "total_parameter_epoch_wall_seconds": round(
                        transition_wall_seconds if effective_epoch_type == "parameter_epoch" else 0.0,
                        6,
                    ),
                },
                "outcome": {
                    "success": bool(post_transition_connectivity.get("ping_success", True)),
                    "failure_reason": None,
                },
            }
            artifact_reporting_started_at = time.perf_counter()
            epoch_timing_wall_marks["artifact_reporting_start"] = (
                artifact_reporting_started_at
            )
            artifact_reporting_completed_at = time.perf_counter()
            epoch_timing_wall_marks["artifact_reporting_end"] = (
                artifact_reporting_completed_at
            )
            epoch_timing_wall_marks["epoch_end_time_utc"] = (
                artifact_reporting_completed_at
            )
            epoch_record["epoch_timing_marks"] = _build_epoch_timing_marks(
                scheduled_activation_time_utc=next_entry.get("activation_time_utc"),
                actual_epoch_start_wall=apply_started_wall,
                actual_epoch_start_epoch=apply_started_epoch,
                activation_lag_seconds=activation_lag,
                mark_wall_times=epoch_timing_wall_marks,
                null_phase_explanations=epoch_timing_null_explanations,
            )
            timing_mark_derived = dict(
                epoch_record["epoch_timing_marks"].get("derived_seconds") or {}
            )
            for timing_key in (
                "scheduler_blocking_after_data_plane_ready_seconds",
                "required_safety_gate_seconds",
                "artifact_write_blocking_seconds",
                "non_blocking_post_apply_seconds",
            ):
                epoch_record["timing"][timing_key] = timing_mark_derived.get(
                    timing_key
                )
            epoch_record["timing"]["audit_write_seconds"] = round(
                artifact_reporting_completed_at - artifact_reporting_started_at,
                6,
            )
            semantic_breakdown = _epoch_timing_semantic_breakdown(epoch_record)
            epoch_record["timing"]["semantic_breakdown"] = semantic_breakdown
            epoch_record["timing"]["required_safety_gate_seconds"] = (
                semantic_breakdown.get("required_safety_gate_seconds")
            )
            epoch_record["artifact_record_state"] = "final"
            _append_jsonl(epoch_execution_details_jsonl, epoch_record)
            compact_epoch_record = _compact_epoch_execution_record(
                epoch_record,
                detail_jsonl=epoch_execution_details_jsonl,
            )
            epoch_execution_records.append(compact_epoch_record)
            completion_event_type = (
                "parameter_epoch_noop_skipped"
                if noop_parameter_epoch
                else f"{effective_epoch_type}_result"
            )
            events.append(
                {
                    "timestamp_utc": _timestamp_utc(),
                    "event_type": completion_event_type,
                    "segment_epoch_key": segment_key,
                    "transition_wall_seconds": compact_epoch_record["timing"][
                        "transition_wall_seconds"
                    ],
                    "activation_lag_seconds": compact_epoch_record[
                        "activation_lag_seconds"
                    ],
                    "deadline_miss": deadline_miss,
                    "escalated_to_runtime_epoch": escalated,
                    "ping_success": bool(post_transition_connectivity.get("ping_success", True)),
                    "noop_skipped": noop_parameter_epoch,
                }
            )
            _queue_live_timing_checkpoint(epoch_execution_records)
            _progress(
                phase=f"epoch:{effective_epoch_type}:completed",
                event_type=completion_event_type,
                phase_detail={
                    "epoch_id": next_entry.get("epoch_id"),
                    "segment_epoch_key": segment_key,
                    "transition_wall_seconds": compact_epoch_record["timing"][
                        "transition_wall_seconds"
                    ],
                    "activation_lag_seconds": compact_epoch_record[
                        "activation_lag_seconds"
                    ],
                    "deadline_miss": deadline_miss,
                    "ping_success": bool(post_transition_connectivity.get("ping_success", True)),
                    "parameter_refresh_recovery_used": parameter_refresh_recovery_used,
                    "noop_skipped": noop_parameter_epoch,
                },
                epoch_context={
                    **dict(current_epoch_context or {}),
                    "effective_epoch_type": effective_epoch_type,
                },
                runtime_epochs_completed=runtime_count,
                parameter_epochs_completed=parameter_count,
            )
            current_epoch_context = None
            maybe_replenish(activation_relative)
        if active_session is not None:
            _progress(
                phase="final_runtime_connectivity_validation",
                event_type="final_runtime_connectivity_validation_started",
                phase_detail={
                    "session_id": getattr(active_session, "session_id", None),
                    "runtime_epochs_completed": runtime_count,
                    "parameter_epochs_completed": parameter_count,
                },
                runtime_epochs_completed=runtime_count,
                parameter_epochs_completed=parameter_count,
            )
            final_runtime_connectivity_validation = _probe_connectivity_with_retries(
                installer=real_installer,
                session=active_session,
                attempts=2,
                primary_only=False,
                warm_access_links=True,
                retry_delay_seconds=0.5,
            )
            if final_runtime_connectivity_validation.get("ping_success") is not True:
                failure_reason = "final_runtime_connectivity_validation_failed"
            _progress(
                phase="final_runtime_connectivity_validation:completed",
                event_type="final_runtime_connectivity_validation_completed",
                phase_detail={
                    "ping_success": final_runtime_connectivity_validation.get(
                        "ping_success"
                    ),
                    "wall_seconds": final_runtime_connectivity_validation.get(
                        "wall_seconds"
                    ),
                },
                runtime_epochs_completed=runtime_count,
                parameter_epochs_completed=parameter_count,
            )
    except Exception as exc:  # noqa: BLE001
        failure_reason = str(exc)
        failure_context = {
            "current_epoch_context": _safe_dict(current_epoch_context),
            "exception": _runtime_exception_details(exc),
        }
        events.append(
            {
                "timestamp_utc": _timestamp_utc(),
                "event_type": "runtime_exception",
                "failure_reason": failure_reason,
                "failure_context": _json_safe(failure_context),
            }
        )
        _progress(
            phase="runtime_exception",
            event_type="runtime_exception",
            phase_detail={
                "failure_reason": failure_reason,
            },
            epoch_context=current_epoch_context,
            runtime_epochs_completed=runtime_count,
            parameter_epochs_completed=parameter_count,
        )
    finally:
        _progress(
            phase="runtime_rollback",
            event_type="runtime_cleanup_started",
            phase_detail={
                "host_history_count": len(host_history),
                "applied_session_count": len(applied_sessions),
            },
            runtime_epochs_completed=runtime_count,
            parameter_epochs_completed=parameter_count,
        )
        node_cleanup = None
        if str(args.runtime_mode) == "correctness":
            if active_session is not None:
                node_cleanup = real_installer.rollback_session(
                    active_session,
                    max_node_workers=int(args.max_node_workers),
                )
        else:
            cleanup_reports: List[Dict[str, Any]] = []
            total_node_rollbacks = len(applied_sessions)
            for cleanup_index, session in enumerate(reversed(applied_sessions), start=1):
                cleanup_reports.append(
                    real_installer.rollback_session(
                        session,
                        max_node_workers=int(args.max_node_workers),
                    )
                )
                _progress(
                    phase="runtime_rollback:node_local",
                    event_type="runtime_cleanup_node_local_progress",
                    phase_detail={
                        "completed_session_rollbacks": cleanup_index,
                        "total_session_rollbacks": total_node_rollbacks,
                        "session_id": getattr(session, "session_id", None),
                    },
                    runtime_epochs_completed=runtime_count,
                    parameter_epochs_completed=parameter_count,
                )
            if cleanup_reports:
                node_cleanup = {
                    "rollback_results": [
                        result
                        for report in cleanup_reports
                        for result in report["rollback_results"]
                    ],
                    "rollback_verification": {
                        "contexts": [
                            context
                            for report in cleanup_reports
                            for context in report["rollback_verification"]["contexts"]
                        ],
                        "cleanup_success": all(
                            report["rollback_verification"]["cleanup_success"]
                            for report in cleanup_reports
                        ),
                    },
                    "timing": {
                        "rollback_wall_seconds": sum(
                            float(report["timing"]["rollback_wall_seconds"])
                            for report in cleanup_reports
                        ),
                    },
                    "session_cleanup_count": len(cleanup_reports),
                }
        host_rollbacks: List[Dict[str, Any]] = []
        host_cleanup_success = True
        total_host_rollbacks = len(host_history)
        if host_history and hasattr(network_executor, "rollback_many"):
            def _host_rollback_progress(update: Dict[str, Any]) -> None:
                _progress(
                    phase="runtime_rollback:host",
                    event_type="runtime_cleanup_host_progress",
                    phase_detail=dict(update),
                    runtime_epochs_completed=runtime_count,
                    parameter_epochs_completed=parameter_count,
                )

            host_rollback_batch = network_executor.rollback_many(
                topology_path=topology_path,
                link_state_path=link_state_path,
                rollback_requests=host_history,
                progress_callback=_host_rollback_progress,
            )
            host_rollbacks = list(host_rollback_batch.get("host_rollbacks") or [])
            if host_rollback_batch.get("outcome", {}).get("rollback_completed") is not True:
                host_cleanup_success = False
            _progress(
                phase="runtime_rollback:host",
                event_type="runtime_cleanup_host_batch_completed",
                phase_detail={
                    "completed_host_rollbacks": len(host_rollbacks),
                    "total_host_rollbacks": total_host_rollbacks,
                    "rollback_completed": host_rollback_batch.get("outcome", {}).get(
                        "rollback_completed"
                    ),
                    "rollback_batch_mode": host_rollback_batch.get("timing", {}).get(
                        "rollback_batch_mode"
                    ),
                    "initial_state_refresh_wall_seconds": host_rollback_batch.get(
                        "timing",
                        {},
                    ).get("initial_state_refresh_wall_seconds"),
                    "final_state_refresh_wall_seconds": host_rollback_batch.get(
                        "timing",
                        {},
                    ).get("final_state_refresh_wall_seconds"),
                    "host_rollback_wall_seconds": host_rollback_batch.get(
                        "timing",
                        {},
                    ).get("host_rollback_wall_seconds"),
                },
                runtime_epochs_completed=runtime_count,
                parameter_epochs_completed=parameter_count,
            )
        else:
            for host_cleanup_index, host_entry in enumerate(reversed(host_history), start=1):
                rollback_started_at = time.perf_counter()
                rollback_result = network_executor.rollback(
                    topology_path=topology_path,
                    link_state_path=link_state_path,
                    network_mutation_plan=host_entry["host_plan"],
                    applied_operations=host_entry["applied_operations"],
                ).to_dict()
                rollback_result["timing"] = {
                    "rollback_wall_seconds": time.perf_counter() - rollback_started_at,
                }
                host_rollbacks.append(rollback_result)
                if rollback_result["outcome"]["rollback_completed"] is not True:
                    host_cleanup_success = False
                _progress(
                    phase="runtime_rollback:host",
                    event_type="runtime_cleanup_host_progress",
                    phase_detail={
                        "completed_host_rollbacks": host_cleanup_index,
                        "total_host_rollbacks": total_host_rollbacks,
                        "rollback_completed": rollback_result["outcome"].get("rollback_completed"),
                        "rollback_wall_seconds": rollback_result["timing"].get("rollback_wall_seconds"),
                    },
                    runtime_epochs_completed=runtime_count,
                    parameter_epochs_completed=parameter_count,
                )
        _progress(
            phase="runtime_rollback:artifact_cleanup",
            event_type="runtime_cleanup_host_artifact_cleanup_started",
            phase_detail={
                "completed_host_rollbacks": len(host_rollbacks),
                "total_host_rollbacks": total_host_rollbacks,
            },
            runtime_epochs_completed=runtime_count,
            parameter_epochs_completed=parameter_count,
        )
        host_artifact_cleanup_started_at = time.perf_counter()
        host_artifact_cleanup = network_executor.cleanup_runtime_artifacts(
            topology_path=topology_path,
            link_state_path=link_state_path,
        )
        host_artifact_cleanup = {
            **dict(host_artifact_cleanup),
            "timing": {
                **dict(dict(host_artifact_cleanup).get("timing") or {}),
                "cleanup_wall_seconds": time.perf_counter()
                - host_artifact_cleanup_started_at,
            },
        }
        post_cleanup_direct_backend_artifacts = direct_backend_host_artifacts_report()
        if host_artifact_cleanup.get("cleanup_completed") is not True:
            host_cleanup_success = False
        if list(post_cleanup_direct_backend_artifacts.get("interfaces", [])):
            host_cleanup_success = False
        final_cleanup = {
            "node_local": node_cleanup,
            "host_rollbacks": host_rollbacks,
            "host_artifact_cleanup": host_artifact_cleanup,
            "post_cleanup_direct_backend_artifacts": post_cleanup_direct_backend_artifacts,
            "all_cleanup_success": (
                (node_cleanup is None or node_cleanup["rollback_verification"]["cleanup_success"])
                and host_cleanup_success
            ),
        }
        _progress(
            phase="runtime_rollback_completed",
            event_type="runtime_cleanup_completed",
            phase_detail={
                "all_cleanup_success": final_cleanup.get("all_cleanup_success"),
                "host_rollback_count": len(host_rollbacks),
                "remaining_host_interface_count": len(
                    list(
                        (post_cleanup_direct_backend_artifacts or {}).get(
                            "interfaces",
                            [],
                        )
                    )
                ),
            },
            runtime_epochs_completed=runtime_count,
            parameter_epochs_completed=parameter_count,
        )
        _flush_live_timing_checkpoints()
        if deferred_checkpoint_errors:
            if failure_reason is None:
                failure_reason = (
                    "deferred_live_timing_checkpoint_failed:"
                    + ";".join(deferred_checkpoint_errors[:3])
                )
            events.append(
                {
                    "timestamp_utc": _timestamp_utc(),
                    "event_type": "deferred_live_timing_checkpoint_failed",
                    "errors": list(deferred_checkpoint_errors),
                }
            )

    return {
        **_current_runtime_report_payload(finalized=True),
        "prepared_bundle_runtime": {
            **_current_runtime_report_payload(finalized=True)["prepared_bundle_runtime"],
            "pre_epoch_host_prewarm": pre_epoch_host_prewarm,
        },
        "failure_context": failure_context,
        "outcome": {
            **_current_runtime_report_payload(finalized=True)["outcome"],
            "real_apply_success": bool(epoch_execution_records)
            and failure_reason is None
            and all(record["outcome"]["success"] for record in epoch_execution_records),
        },
    }


def _timing_analysis(
    *,
    phase_timing: Dict[str, Any],
    timing_rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    first_transition = float(phase_timing.get("first_runtime_epoch_transition_wall_seconds", 0.0))
    first_prepare = float(
        phase_timing.get("first_runtime_epoch_direct_backend_prepare_wall_seconds", 0.0)
    )
    first_reconcile = float(
        phase_timing.get("first_runtime_epoch_direct_backend_reconcile_wall_seconds", 0.0)
    )
    first_prepare_reconcile = round(first_prepare + first_reconcile, 6)
    first_execution_after_prepare_reconcile = round(
        max(0.0, first_transition - first_prepare - first_reconcile),
        6,
    )
    cold_start_before_first_epoch_execution = round(
        float(phase_timing.get("compose_up_wall_seconds", 0.0))
        + float(phase_timing.get("container_readiness_wall_seconds", 0.0))
        + float(phase_timing.get("initial_runtime_readiness_wall_seconds", 0.0))
        + float(phase_timing.get("pre_epoch_host_runtime_prewarm_wall_seconds", 0.0))
        + first_prepare
        + first_reconcile,
        6,
    )
    runtime_rows = [
        row for row in timing_rows if str(row.get("epoch_type", "runtime_epoch")) == "runtime_epoch"
    ]
    parameter_rows = [
        row for row in timing_rows if str(row.get("epoch_type", "runtime_epoch")) == "parameter_epoch"
    ]
    steady_rows = list(runtime_rows[1:]) if len(runtime_rows) > 1 else []

    def _avg(rows: Sequence[Dict[str, Any]], key: str) -> float:
        if not rows:
            return 0.0
        return round(
            sum(float(row.get(key, 0.0)) for row in rows) / float(len(rows)),
            6,
        )

    def _distribution(rows: Sequence[Dict[str, Any]], key: str) -> Dict[str, Any]:
        values = [
            float(row.get(key, 0.0))
            for row in rows
            if row.get(key) is not None
        ]
        if not values:
            return {
                "count": 0,
                "average_seconds": 0.0,
                "p50_seconds": 0.0,
                "p95_seconds": 0.0,
                "worst_seconds": 0.0,
            }
        sorted_values = sorted(values)
        return {
            "count": len(values),
            "average_seconds": round(sum(values) / float(len(values)), 6),
            "p50_seconds": round(sorted_values[len(sorted_values) // 2], 6),
            "p95_seconds": round(
                sorted_values[
                    max(
                        0,
                        min(
                            len(sorted_values) - 1,
                            math.ceil(0.95 * len(sorted_values)) - 1,
                        ),
                    )
                ],
                6,
            ),
            "worst_seconds": round(max(values), 6),
        }

    return {
        "startup_preparation": {
            "compose_up_wall_seconds": float(phase_timing.get("compose_up_wall_seconds", 0.0)),
            "container_readiness_wall_seconds": float(
                phase_timing.get("container_readiness_wall_seconds", 0.0)
            ),
            "base_runtime_readiness_wall_seconds": float(
                phase_timing.get("initial_runtime_readiness_wall_seconds", 0.0)
            ),
            "pre_epoch_host_runtime_prewarm_wall_seconds": float(
                phase_timing.get("pre_epoch_host_runtime_prewarm_wall_seconds", 0.0)
            ),
            "pre_epoch_host_runtime_prepare_wall_seconds": float(
                phase_timing.get("pre_epoch_host_runtime_prepare_wall_seconds", 0.0)
            ),
            "pre_epoch_host_runtime_reconcile_wall_seconds": float(
                phase_timing.get("pre_epoch_host_runtime_reconcile_wall_seconds", 0.0)
            ),
            "pre_first_epoch_direct_backend_prepare_wall_seconds": first_prepare,
            "pre_first_epoch_runtime_manager_reconcile_wall_seconds": first_reconcile,
            "ready_to_execute_epochs_wall_seconds": round(
                float(phase_timing.get("initial_runtime_readiness_wall_seconds", 0.0))
                + float(phase_timing.get("pre_epoch_host_runtime_prewarm_wall_seconds", 0.0))
                + first_prepare
                + first_reconcile,
                6,
            ),
            "cold_start_before_first_epoch_execution_wall_seconds": (
                cold_start_before_first_epoch_execution
            ),
        },
        "initial_activation": {
            "classification": "bootstrap_plus_initial_runtime_epoch",
            "includes_first_runtime_epoch": bool(runtime_rows),
            "steady_runtime_excludes_initial_activation": True,
            "cold_start_before_first_epoch_execution_wall_seconds": (
                cold_start_before_first_epoch_execution
            ),
            "first_runtime_epoch_transition_wall_seconds": first_transition,
            "first_runtime_epoch_prepare_reconcile_wall_seconds": first_prepare_reconcile,
            "first_runtime_epoch_execution_after_prepare_reconcile_wall_seconds": (
                first_execution_after_prepare_reconcile
            ),
            "initial_activation_wall_seconds": round(
                cold_start_before_first_epoch_execution
                + first_execution_after_prepare_reconcile,
                6,
            ),
        },
        "first_runtime_epoch": {
            "transition_wall_seconds": first_transition,
            "classification": "initial_materialization_epoch",
            "execution_after_prepare_reconcile_wall_seconds": (
                first_execution_after_prepare_reconcile
            ),
            "network_mutation_plan_wall_seconds": float(
                phase_timing.get("first_runtime_epoch_network_mutation_wall_seconds", 0.0)
            ),
            "node_local_apply_wall_seconds": float(
                phase_timing.get("first_runtime_epoch_node_local_apply_wall_seconds", 0.0)
            ),
        },
        "initial_materialization_epoch": {
            "classification": "initial_materialization_epoch",
            "source": "first_runtime_epoch",
            "included_in_runtime_epochs_including_initial": bool(runtime_rows),
            "excluded_from_steady_state_runtime_epochs": bool(runtime_rows),
            "transition_wall_seconds": first_transition,
            "execution_after_prepare_reconcile_wall_seconds": (
                first_execution_after_prepare_reconcile
            ),
            "network_mutation_plan_wall_seconds": float(
                phase_timing.get("first_runtime_epoch_network_mutation_wall_seconds", 0.0)
            ),
            "node_local_apply_wall_seconds": float(
                phase_timing.get("first_runtime_epoch_node_local_apply_wall_seconds", 0.0)
            ),
            "scheduler_blocking_after_data_plane_ready_seconds": (
                float(
                    runtime_rows[0].get(
                        "scheduler_blocking_after_data_plane_ready_seconds",
                        0.0,
                    )
                    or 0.0
                )
                if runtime_rows
                else 0.0
            ),
        },
        "runtime_epochs_including_initial": {
            "count": len(runtime_rows),
            "transition_wall_seconds": _distribution(
                runtime_rows,
                "transition_wall_seconds",
            ),
            "network_mutation_plan_wall_seconds": _distribution(
                runtime_rows,
                "network_mutation_plan_wall_seconds",
            ),
            "scheduler_blocking_after_data_plane_ready_seconds": _distribution(
                runtime_rows,
                "scheduler_blocking_after_data_plane_ready_seconds",
            ),
        },
        "steady_state_runtime_epochs": {
            "source_runtime_epoch_count": len(runtime_rows),
            "excluded_initial_runtime_epoch_count": 1 if runtime_rows else 0,
            "count": len(steady_rows),
            "transition_wall_seconds": _distribution(
                steady_rows,
                "transition_wall_seconds",
            ),
            "scheduler_blocking_after_data_plane_ready_seconds": _distribution(
                steady_rows,
                "scheduler_blocking_after_data_plane_ready_seconds",
            ),
            "average_transition_wall_seconds": _avg(steady_rows, "transition_wall_seconds"),
            "average_network_mutation_plan_wall_seconds": _avg(
                steady_rows,
                "network_mutation_plan_wall_seconds",
            ),
            "average_direct_backend_prepare_wall_seconds": _avg(
                steady_rows,
                "host_runtime_prepare_wall_seconds",
            ),
            "average_runtime_manager_reconcile_wall_seconds": _avg(
                steady_rows,
                "host_runtime_reconcile_wall_seconds",
            ),
            "average_host_mutation_apply_wall_seconds": _avg(
                steady_rows,
                "host_operation_apply_wall_seconds",
            ),
            "average_node_local_apply_wall_seconds": _avg(
                steady_rows,
                "node_local_apply_wall_seconds",
            ),
            "average_node_local_incremental_cleanup_wall_seconds": _avg(
                steady_rows,
                "node_local_incremental_cleanup_wall_seconds",
            ),
            "average_node_local_reapplied_flow_count": _avg(
                steady_rows,
                "node_local_reapplied_flow_count",
            ),
            "average_node_local_held_flow_count": _avg(
                steady_rows,
                "node_local_held_flow_count",
            ),
            "average_pre_transition_connectivity_probe_wall_seconds": _avg(
                steady_rows,
                "pre_transition_connectivity_probe_wall_seconds",
            ),
            "average_post_transition_connectivity_probe_wall_seconds": _avg(
                steady_rows,
                "node_local_ping_wall_seconds",
            ),
            "average_access_link_warmup_wall_seconds": _avg(
                steady_rows,
                "node_local_access_link_warmup_wall_seconds",
            ),
        },
        "parameter_epochs": {
            "count": len(parameter_rows),
            "average_transition_wall_seconds": _avg(
                parameter_rows,
                "transition_wall_seconds",
            ),
            "average_network_mutation_plan_wall_seconds": _avg(
                parameter_rows,
                "network_mutation_plan_wall_seconds",
            ),
            "average_direct_backend_prepare_wall_seconds": _avg(
                parameter_rows,
                "host_runtime_prepare_wall_seconds",
            ),
            "average_runtime_manager_reconcile_wall_seconds": _avg(
                parameter_rows,
                "host_runtime_reconcile_wall_seconds",
            ),
            "average_host_mutation_apply_wall_seconds": _avg(
                parameter_rows,
                "host_operation_apply_wall_seconds",
            ),
            "average_pre_transition_connectivity_probe_wall_seconds": _avg(
                parameter_rows,
                "pre_transition_connectivity_probe_wall_seconds",
            ),
            "average_post_transition_connectivity_probe_wall_seconds": _avg(
                parameter_rows,
                "node_local_ping_wall_seconds",
            ),
        },
    }


def _resolve_target_ip(
    *,
    selector_value: str,
    validation_targets: Sequence[Dict[str, Any]],
) -> str:
    token = str(selector_value).strip()
    if not token:
        raise ValueError("watch selector target token is empty")
    for target in validation_targets:
        if token == str(target.get("node_id")):
            return str(target.get("ip_address"))
        if token == str(target.get("ip_address")):
            return str(target.get("ip_address"))
    raise ValueError(f"unknown ping-watch target {token}")


def _parse_ping_watch_selectors(
    selectors: Sequence[str],
    *,
    validation_targets: Sequence[Dict[str, Any]],
) -> List[Dict[str, str]]:
    watches: List[Dict[str, str]] = []
    for raw_selector in selectors:
        parts = str(raw_selector).split(":", 1)
        if len(parts) != 2:
            raise ValueError(
                f"invalid --ping-watch selector {raw_selector!r}; expected ut_id:target"
            )
        ut_id = str(parts[0]).strip()
        target_token = str(parts[1]).strip()
        watches.append(
            {
                "ut_id": ut_id,
                "target": target_token,
                "target_ip": _resolve_target_ip(
                    selector_value=target_token,
                    validation_targets=validation_targets,
                ),
            }
        )
    return watches


def _wait_for_named_containers(node_ids: Sequence[str], *, attempts: int = 30) -> None:
    if not node_ids:
        return
    required = set(str(node_id) for node_id in node_ids)
    for _attempt in range(attempts):
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            text=True,
            capture_output=True,
            check=False,
        )
        running = set(
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        )
        if required.issubset(running):
            return
        time.sleep(1.0)
    missing = ", ".join(sorted(required - running))
    raise RuntimeError(f"timed out waiting for containers: {missing}")


def _probe_connectivity_with_retries(
    *,
    installer: MinimalRealLinuxMplsInstaller,
    session: Any,
    attempts: int = 3,
    primary_only: bool = False,
    warm_access_links: bool = False,
    retry_delay_seconds: float = 0.0,
) -> Dict[str, Any]:
    probe_attempts: List[Dict[str, Any]] = []
    last_result: Dict[str, Any] | None = None
    for _index in range(max(1, int(attempts))):
        last_result = installer.probe_connectivity(
            session,
            primary_only=primary_only,
            warm_access_links=warm_access_links,
        )
        probe_attempts.append(dict(last_result))
        if bool(last_result.get("ping_success")):
            break
        if retry_delay_seconds > 0.0:
            time.sleep(float(retry_delay_seconds))
    result = dict(last_result or {})
    result["attempts"] = probe_attempts
    result["attempt_count"] = len(probe_attempts)
    result["wall_seconds"] = round(
        sum(float(attempt.get("wall_seconds", 0.0)) for attempt in probe_attempts),
        6,
    )
    result["retry_recovered"] = bool(result.get("ping_success")) and len(probe_attempts) > 1
    return result


def _wait_for_compose_containers(
    *,
    container_names: Sequence[str],
    timeout_seconds: float,
    poll_interval_seconds: float = 1.0,
) -> Dict[str, Any]:
    started_at = time.perf_counter()
    attempts: List[Dict[str, Any]] = []
    required = set(str(name) for name in container_names if str(name).strip())
    if not required:
        return {
            "ready": True,
            "required_container_count": 0,
            "wall_seconds": 0.0,
            "attempts": [],
        }
    while True:
        completed = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            text=True,
            capture_output=True,
            check=False,
        )
        running_status = {}
        for line in completed.stdout.splitlines():
            parts = line.strip().split("\t", 1)
            if len(parts) != 2:
                continue
            running_status[parts[0]] = parts[1]
        missing = sorted(required - set(running_status))
        attempt = {
            "attempt_index": len(attempts) + 1,
            "missing_container_count": len(missing),
            "missing_containers": missing,
            "running_container_count": len(required) - len(missing),
        }
        attempts.append(attempt)
        if not missing:
            return {
                "ready": True,
                "required_container_count": len(required),
                "wall_seconds": time.perf_counter() - started_at,
                "attempts": attempts,
            }
        elapsed = time.perf_counter() - started_at
        if float(timeout_seconds) > 0.0 and elapsed >= float(timeout_seconds):
            return {
                "ready": False,
                "required_container_count": len(required),
                "wall_seconds": elapsed,
                "attempts": attempts,
            }
        time.sleep(float(poll_interval_seconds))


def _start_ping_watcher(
    *,
    ut_id: str,
    target_ip: str,
    label: str,
    outfile: Path,
) -> subprocess.Popen[str]:
    command = [
        "docker",
        "exec",
        ut_id,
        "sh",
        "-lc",
        (
            "while true; do "
            "ts=$(date +%s.%3N); "
            f"out=$(ping -n -c 1 -W 1 {target_ip} 2>&1 || true); "
            "if printf '%s\\n' \"$out\" | grep -q 'bytes from'; then status=success; "
            "elif printf '%s\\n' \"$out\" | grep -q 'Network is unreachable'; then status=unreachable; "
            "elif printf '%s\\n' \"$out\" | grep -q '100% packet loss'; then status=loss; "
            "else status=other; fi; "
            "last_line=$(printf '%s\\n' \"$out\" | tail -n 1 | tr '\\t' ' '); "
            f"printf '%s %s %s %s\\n' \"$ts\" \"{label}\" \"$status\" \"$last_line\"; "
            "sleep 0.2; "
            "done"
        ),
    ]
    outfile.parent.mkdir(parents=True, exist_ok=True)
    handle = outfile.open("w", encoding="utf-8")
    return subprocess.Popen(
        command,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _stop_ping_watcher(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _summarize_watcher(path: Path) -> Dict[str, Any]:
    counts = {"success": 0, "loss": 0, "unreachable": 0, "other": 0}
    first_success = None
    first_non_success_after_success = None
    saw_success = False
    if not path.exists():
        return {
            "sample_count": 0,
            "counts": counts,
            "first_success_timestamp": None,
            "first_non_success_after_first_success": None,
        }
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ", 3)
        if len(parts) < 3:
            continue
        timestamp = parts[0]
        status = parts[2]
        counts[status] = counts.get(status, 0) + 1
        if status == "success" and first_success is None:
            first_success = timestamp
            saw_success = True
        elif saw_success and status != "success" and first_non_success_after_success is None:
            first_non_success_after_success = timestamp
    return {
        "sample_count": sum(counts.values()),
        "counts": counts,
        "first_success_timestamp": first_success,
        "first_non_success_after_first_success": first_non_success_after_success,
    }


def _write_events_jsonl(path: Path, events: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def _process_status_snapshot(*, pid: int) -> Dict[str, Any]:
    status_path = Path(f"/proc/{pid}/status")
    snapshot: Dict[str, Any] = {"pid": pid}
    try:
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key == "Name":
                snapshot["name"] = value.strip()
            elif key == "State":
                snapshot["state"] = value.strip()
            elif key == "Threads":
                snapshot["threads"] = int(value.strip())
            elif key == "VmRSS":
                snapshot["vmrss_kib"] = int(value.strip().split()[0])
        fd_path = Path(f"/proc/{pid}/fd")
        if fd_path.exists():
            snapshot["open_fd_count"] = len(list(fd_path.iterdir()))
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError, ValueError) as exc:
        snapshot["error"] = str(exc)
    return snapshot


def _host_memory_snapshot() -> Dict[str, Any]:
    meminfo_path = Path("/proc/meminfo")
    snapshot: Dict[str, Any] = {}
    try:
        for line in meminfo_path.read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, raw_value = line.split(":", 1)
            parts = raw_value.strip().split()
            if not parts:
                continue
            try:
                value_kib = int(parts[0])
            except ValueError:
                continue
            if key in {
                "MemTotal",
                "MemFree",
                "MemAvailable",
                "Buffers",
                "Cached",
                "SwapTotal",
                "SwapFree",
            }:
                snapshot[f"{key.lower()}_kib"] = value_kib
    except (FileNotFoundError, PermissionError, OSError) as exc:
        snapshot["error"] = str(exc)
    return snapshot


def _directory_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        root_path = Path(root)
        for filename in files:
            file_path = root_path / filename
            try:
                total += file_path.stat().st_size
            except (FileNotFoundError, PermissionError, OSError):
                continue
    return total


def _host_resource_snapshot(*, run_dir: Path) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {
        "memory": _host_memory_snapshot(),
    }
    try:
        workspace_usage = shutil.disk_usage(SATELLITE_DIR)
        snapshot["workspace_disk"] = {
            "path": str(SATELLITE_DIR),
            "total_bytes": int(workspace_usage.total),
            "used_bytes": int(workspace_usage.used),
            "free_bytes": int(workspace_usage.free),
        }
    except OSError as exc:
        snapshot["workspace_disk"] = {"path": str(SATELLITE_DIR), "error": str(exc)}
    try:
        snapshot["run_dir"] = {
            "path": str(run_dir),
            "size_bytes": _directory_size_bytes(run_dir),
        }
    except OSError as exc:
        snapshot["run_dir"] = {"path": str(run_dir), "error": str(exc)}
    return snapshot


def _child_process_snapshots(*, parent_pid: int, limit: int = 5) -> List[Dict[str, Any]]:
    completed = subprocess.run(
        ["ps", "--ppid", str(parent_pid), "-o", "pid=,etimes=,cmd="],
        capture_output=True,
        text=True,
        check=False,
        timeout=10.0,
    )
    if completed.returncode != 0:
        return [
            {
                "error": completed.stderr.strip() or completed.stdout.strip(),
                "returncode": completed.returncode,
            }
        ]
    children: List[Dict[str, Any]] = []
    for line in completed.stdout.splitlines()[: max(1, int(limit))]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            elapsed_seconds = int(parts[1])
        except ValueError:
            continue
        children.append(
            {
                "pid": pid,
                "elapsed_seconds": elapsed_seconds,
                "cmd": parts[2],
            }
        )
    return children


def _docker_runtime_snapshot() -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Label \"com.docker.compose.project\"}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "docker_ps_timeout"}
    if completed.returncode != 0:
        return {
            "ok": False,
            "returncode": completed.returncode,
            "error": completed.stderr.strip() or completed.stdout.strip(),
        }
    compose_project_container_count = 0
    running_count = 0
    exited_count = 0
    sample: List[Dict[str, str]] = []
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, status, project = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if project != "seed_output":
            continue
        compose_project_container_count += 1
        if status.startswith("Up"):
            running_count += 1
        else:
            exited_count += 1
        if len(sample) < 5:
            sample.append({"name": name, "status": status})
    return {
        "ok": True,
        "compose_project_container_count": compose_project_container_count,
        "compose_project_running_count": running_count,
        "compose_project_non_running_count": exited_count,
        "sample": sample,
    }


def _new_runtime_progress_state(*, run_id: str, run_mode: str) -> Dict[str, Any]:
    return {
        "lock": threading.Lock(),
        "warning_keys": set(),
        "data": {
            "run_id": run_id,
            "run_mode": run_mode,
            "current_phase": "initialized",
            "phase_detail": {},
            "phase_started_utc": _timestamp_utc(),
            "phase_started_monotonic": time.monotonic(),
            "last_progress_utc": _timestamp_utc(),
            "last_progress_monotonic": time.monotonic(),
            "last_event_type": "run_started",
            "current_epoch_context": None,
            "runtime_epochs_completed": 0,
            "parameter_epochs_completed": 0,
            "stall_warning_count": 0,
            "latest_stall_warning": None,
        },
    }


def _mark_runtime_progress(
    progress_state: Dict[str, Any],
    *,
    phase: str,
    event_type: str | None = None,
    phase_detail: Dict[str, Any] | None = None,
    epoch_context: Dict[str, Any] | None = None,
    runtime_epochs_completed: int | None = None,
    parameter_epochs_completed: int | None = None,
) -> None:
    now_utc = _timestamp_utc()
    now_monotonic = time.monotonic()
    with progress_state["lock"]:
        data = progress_state["data"]
        if phase != data.get("current_phase"):
            data["current_phase"] = phase
            data["phase_started_utc"] = now_utc
            data["phase_started_monotonic"] = now_monotonic
        if phase_detail is not None:
            data["phase_detail"] = _safe_dict(phase_detail)
        if event_type is not None:
            data["last_event_type"] = event_type
        if epoch_context is not None:
            data["current_epoch_context"] = _safe_dict(epoch_context)
        if runtime_epochs_completed is not None:
            data["runtime_epochs_completed"] = int(runtime_epochs_completed)
        if parameter_epochs_completed is not None:
            data["parameter_epochs_completed"] = int(parameter_epochs_completed)
        data["last_progress_utc"] = now_utc
        data["last_progress_monotonic"] = now_monotonic


def _stall_warning_threshold_seconds(*, phase: str, args: argparse.Namespace) -> float:
    if phase.startswith("epoch:"):
        return float(args.epoch_stall_warning_seconds)
    return float(args.startup_stall_warning_seconds)


def _runtime_progress_snapshot(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    progress_state: Dict[str, Any],
    phase_timing: Dict[str, Any],
    event_count: int,
) -> Dict[str, Any]:
    now_monotonic = time.monotonic()
    with progress_state["lock"]:
        data = dict(progress_state["data"])
    phase_started_monotonic = float(data.get("phase_started_monotonic", now_monotonic))
    last_progress_monotonic = float(data.get("last_progress_monotonic", now_monotonic))
    current_phase = str(data.get("current_phase", "unknown"))
    threshold_seconds = _stall_warning_threshold_seconds(
        phase=current_phase,
        args=args,
    )
    seconds_in_phase = max(0.0, now_monotonic - phase_started_monotonic)
    seconds_since_progress = max(0.0, now_monotonic - last_progress_monotonic)
    return {
        "run_id": data.get("run_id"),
        "run_mode": data.get("run_mode"),
        "current_phase": current_phase,
        "phase_detail": _safe_dict(data.get("phase_detail")),
        "phase_started_utc": data.get("phase_started_utc"),
        "last_progress_utc": data.get("last_progress_utc"),
        "last_event_type": data.get("last_event_type"),
        "current_epoch_context": _safe_dict(data.get("current_epoch_context")),
        "runtime_epochs_completed": int(data.get("runtime_epochs_completed", 0)),
        "parameter_epochs_completed": int(data.get("parameter_epochs_completed", 0)),
        "stall_warning_count": int(data.get("stall_warning_count", 0)),
        "latest_stall_warning": _safe_dict(data.get("latest_stall_warning")),
        "event_count": int(event_count),
        "seconds_in_phase": round(seconds_in_phase, 6),
        "seconds_since_progress": round(seconds_since_progress, 6),
        "stall_warning_threshold_seconds": round(threshold_seconds, 6),
        "phase_timing": dict(phase_timing),
        "self_process": _process_status_snapshot(pid=os.getpid()),
        "host_resources": _host_resource_snapshot(run_dir=run_dir),
        "child_processes": _child_process_snapshots(parent_pid=os.getpid()),
        "docker_runtime": _docker_runtime_snapshot(),
        "direct_backend_host_interface_count": len(
            list(direct_backend_host_artifacts_report().get("interfaces", []))
        ),
        "heartbeat_utc": _timestamp_utc(),
    }


def _append_resource_usage_sample(
    *,
    run_dir: Path,
    snapshot: Dict[str, Any],
    sample_reason: str,
) -> None:
    row = {
        "timestamp_utc": snapshot.get("heartbeat_utc") or _timestamp_utc(),
        "sample_reason": sample_reason,
        "run_id": snapshot.get("run_id"),
        "run_mode": snapshot.get("run_mode"),
        "current_phase": snapshot.get("current_phase"),
        "phase_started_utc": snapshot.get("phase_started_utc"),
        "seconds_in_phase": snapshot.get("seconds_in_phase"),
        "seconds_since_progress": snapshot.get("seconds_since_progress"),
        "runtime_epochs_completed": snapshot.get("runtime_epochs_completed"),
        "parameter_epochs_completed": snapshot.get("parameter_epochs_completed"),
        "current_epoch_context": _safe_dict(snapshot.get("current_epoch_context")),
        "self_process": _safe_dict(snapshot.get("self_process")),
        "host_resources": _safe_dict(snapshot.get("host_resources")),
        "docker_runtime": _safe_dict(snapshot.get("docker_runtime")),
        "direct_backend_host_interface_count": snapshot.get(
            "direct_backend_host_interface_count"
        ),
    }
    path = run_dir / "resource_usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _flush_runtime_progress_artifacts(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    progress_state: Dict[str, Any],
    phase_timing: Dict[str, Any],
    events: Sequence[Dict[str, Any]],
    write_events: bool = True,
    resource_sample_reason: str | None = "progress_update",
) -> None:
    snapshot = _runtime_progress_snapshot(
        args=args,
        run_dir=run_dir,
        progress_state=progress_state,
        phase_timing=phase_timing,
        event_count=len(events),
    )
    _write_json(
        run_dir / "runtime_progress.json",
        snapshot,
    )
    if resource_sample_reason:
        _append_resource_usage_sample(
            run_dir=run_dir,
            snapshot=snapshot,
            sample_reason=resource_sample_reason,
        )
    if write_events:
        _write_events_jsonl(run_dir / "events.jsonl", list(events))


def _start_runtime_progress_heartbeat(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    progress_state: Dict[str, Any],
    phase_timing: Dict[str, Any],
    events: List[Dict[str, Any]],
) -> Tuple[threading.Event, threading.Thread]:
    stop_event = threading.Event()

    def _worker() -> None:
        interval_seconds = max(5.0, float(args.progress_heartbeat_seconds))
        last_written_event_count = -1
        while not stop_event.wait(interval_seconds):
            snapshot = _runtime_progress_snapshot(
                args=args,
                run_dir=run_dir,
                progress_state=progress_state,
                phase_timing=phase_timing,
                event_count=len(events),
            )
            threshold_seconds = float(snapshot.get("stall_warning_threshold_seconds", 0.0))
            seconds_since_progress = float(snapshot.get("seconds_since_progress", 0.0))
            if threshold_seconds > 0.0 and seconds_since_progress >= threshold_seconds:
                warning_key = (
                    f"{snapshot.get('current_phase')}:{int(seconds_since_progress // threshold_seconds)}"
                )
                with progress_state["lock"]:
                    warning_keys = progress_state["warning_keys"]
                    if warning_key not in warning_keys:
                        warning_keys.add(warning_key)
                        warning = {
                            "timestamp_utc": _timestamp_utc(),
                            "phase": snapshot.get("current_phase"),
                            "seconds_since_progress": seconds_since_progress,
                            "threshold_seconds": threshold_seconds,
                            "current_epoch_context": _safe_dict(
                                snapshot.get("current_epoch_context")
                            ),
                            "child_processes": _safe_list(snapshot.get("child_processes")),
                        }
                        progress_state["data"]["stall_warning_count"] = int(
                            progress_state["data"].get("stall_warning_count", 0)
                        ) + 1
                        progress_state["data"]["latest_stall_warning"] = warning
                        events.append(
                            {
                                "timestamp_utc": warning["timestamp_utc"],
                                "event_type": "runtime_progress_stall_warning",
                                "phase": warning["phase"],
                                "seconds_since_progress": round(
                                    warning["seconds_since_progress"],
                                    6,
                                ),
                                "threshold_seconds": round(
                                    warning["threshold_seconds"],
                                    6,
                                ),
                                "current_epoch_context": warning["current_epoch_context"],
                            }
                        )
                        last_written_event_count = -1
                snapshot = _runtime_progress_snapshot(
                    args=args,
                    run_dir=run_dir,
                    progress_state=progress_state,
                    phase_timing=phase_timing,
                    event_count=len(events),
                )
            _write_json(run_dir / "runtime_progress.json", snapshot)
            _append_resource_usage_sample(
                run_dir=run_dir,
                snapshot=snapshot,
                sample_reason="heartbeat",
            )
            if len(events) != last_written_event_count:
                _write_events_jsonl(run_dir / "events.jsonl", list(events))
                last_written_event_count = len(events)

    thread = threading.Thread(
        target=_worker,
        name="runtime-progress-heartbeat",
        daemon=True,
    )
    thread.start()
    return stop_event, thread


def _timing_rows(runtime_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for iteration in list(runtime_report.get("iterations", [])):
        timing = dict(iteration.get("timing", {}))
        manager = dict(
            iteration.get("host_mutation_execution", {})
            .get("outcome", {})
            .get("manager_timing_breakdown", {})
        )
        node_local_execution = iteration.get("node_local_execution") or {}
        node_timing = dict(dict(node_local_execution).get("timing", {}))
        semantic_breakdown = timing.get("semantic_breakdown")
        if not isinstance(semantic_breakdown, dict):
            semantic_breakdown = _epoch_timing_semantic_breakdown(iteration)
        timing_marks = _epoch_timing_marks(iteration)
        timing_mark_elapsed = dict(timing_marks.get("monotonic_elapsed_seconds") or {})
        timing_mark_derived = dict(timing_marks.get("derived_seconds") or {})
        rows.append(
            {
                "sequence_index": iteration.get("sequence_index"),
                "epoch_id": iteration.get("epoch_id"),
                "epoch_type": iteration.get("epoch_type", "runtime_epoch"),
                "loop_iteration": iteration.get("loop_iteration"),
                "to_slice_id": iteration.get("to_slice_id"),
                "segment_id": iteration.get("segment_id"),
                "noop_skipped": iteration.get("noop_skipped", False),
                "simulated_time_seconds": iteration.get("simulated_time_seconds"),
                "activation_time_utc": iteration.get("activation_time_utc"),
                "activation_lag_seconds": iteration.get("activation_lag_seconds"),
                "deadline_miss": iteration.get("deadline_miss"),
                "transition_wall_seconds": timing.get("transition_wall_seconds"),
                "network_mutation_plan_wall_seconds": timing.get(
                    "network_mutation_plan_wall_seconds"
                ),
                "pre_transition_connectivity_probe_wall_seconds": timing.get(
                    "pre_transition_connectivity_probe_wall_seconds"
                ),
                "node_local_apply_wall_seconds": timing.get("node_local_apply_wall_seconds"),
                "node_local_incremental_cleanup_wall_seconds": timing.get(
                    "node_local_incremental_cleanup_wall_seconds"
                ),
                "node_local_reapplied_flow_count": timing.get(
                    "node_local_reapplied_flow_count"
                ),
                "node_local_held_flow_count": timing.get(
                    "node_local_held_flow_count"
                ),
                "node_local_access_link_warmup_wall_seconds": timing.get(
                    "node_local_access_link_warmup_wall_seconds",
                    timing.get("access_link_warmup_wall_seconds"),
                ),
                "node_local_ping_wall_seconds": timing.get(
                    "node_local_ping_wall_seconds",
                    timing.get("post_transition_connectivity_probe_wall_seconds"),
                ),
                "packet_observation_wall_seconds": timing.get(
                    "packet_observation_wall_seconds"
                ),
                "host_mpls_prepare_wall_seconds": node_timing.get(
                    "host_mpls_prepare_wall_seconds"
                ),
                "host_controller_setup_wall_seconds": manager.get(
                    "controller_setup_wall_seconds"
                ),
                "host_runtime_prepare_wall_seconds": manager.get(
                    "runtime_prepare_wall_seconds",
                    timing.get("host_runtime_prepare_wall_seconds"),
                ),
                "host_runtime_reconcile_wall_seconds": manager.get(
                    "runtime_reconcile_wall_seconds",
                    timing.get("host_runtime_reconcile_wall_seconds"),
                ),
                "host_operation_apply_wall_seconds": manager.get(
                    "operation_apply_wall_seconds",
                    timing.get("host_operation_apply_wall_seconds"),
                ),
                "post_apply_state_refresh_wall_seconds": manager.get(
                    "post_apply_state_refresh_wall_seconds"
                ),
                "post_apply_verification_wall_seconds": manager.get(
                    "post_apply_verification_wall_seconds"
                ),
                "parameter_qdisc_apply_seconds": timing.get(
                    "parameter_qdisc_apply_seconds"
                ),
                "parameter_metadata_update_seconds": timing.get(
                    "parameter_metadata_update_seconds"
                ),
                "parameter_dirty_reconcile_seconds": timing.get(
                    "parameter_dirty_reconcile_seconds"
                ),
                "targeted_validation_seconds": timing.get("targeted_validation_seconds"),
                "recovery_seconds": timing.get("recovery_seconds"),
                "audit_write_seconds": timing.get("audit_write_seconds"),
                "scheduler_blocking_after_data_plane_ready_seconds": timing.get(
                    "scheduler_blocking_after_data_plane_ready_seconds"
                ),
                "required_safety_gate_seconds": timing.get(
                    "required_safety_gate_seconds"
                ),
                "artifact_write_blocking_seconds": timing.get(
                    "artifact_write_blocking_seconds"
                ),
                "non_blocking_post_apply_seconds": timing.get(
                    "non_blocking_post_apply_seconds"
                ),
                "total_parameter_epoch_wall_seconds": timing.get(
                    "total_parameter_epoch_wall_seconds"
                ),
                "host_total_wall_seconds": manager.get("total_wall_seconds"),
                "semantic_bootstrap_pre_runtime_wall_seconds": semantic_breakdown.get(
                    "bootstrap_pre_runtime_wall_seconds"
                ),
                "semantic_scheduled_activation_lag_seconds": semantic_breakdown.get(
                    "scheduled_activation_lag_seconds"
                ),
                "semantic_pre_apply_cleanup_or_blocking_verification_seconds": (
                    semantic_breakdown.get(
                        "pre_apply_cleanup_or_blocking_verification_seconds"
                    )
                ),
                "semantic_data_plane_mutation_apply_seconds": semantic_breakdown.get(
                    "data_plane_mutation_apply_seconds"
                ),
                "semantic_post_apply_validation_probe_seconds": semantic_breakdown.get(
                    "post_apply_validation_probe_seconds"
                ),
                "semantic_post_apply_cleanup_verification_seconds": semantic_breakdown.get(
                    "post_apply_cleanup_verification_seconds"
                ),
                "semantic_reporting_artifact_write_seconds": semantic_breakdown.get(
                    "reporting_artifact_write_seconds"
                ),
                "semantic_total_epoch_wall_seconds": semantic_breakdown.get(
                    "total_epoch_wall_seconds"
                ),
                "semantic_data_plane_ready_lag_seconds": semantic_breakdown.get(
                    "data_plane_ready_lag_seconds"
                ),
                "semantic_forwarding_changing_work_seconds": semantic_breakdown.get(
                    "forwarding_changing_work_seconds"
                ),
                "semantic_verification_cleanup_runner_overhead_seconds": (
                    semantic_breakdown.get(
                        "verification_cleanup_runner_overhead_seconds"
                    )
                ),
                "semantic_scheduler_blocking_after_data_plane_ready_seconds": (
                    semantic_breakdown.get(
                        "scheduler_blocking_after_data_plane_ready_seconds"
                    )
                ),
                "semantic_required_safety_gate_seconds": semantic_breakdown.get(
                    "required_safety_gate_seconds"
                ),
                "semantic_artifact_write_blocking_seconds": semantic_breakdown.get(
                    "artifact_write_blocking_seconds"
                ),
                "semantic_non_blocking_post_apply_seconds": semantic_breakdown.get(
                    "non_blocking_post_apply_seconds"
                ),
                "semantic_runtime_host_apply_seconds": semantic_breakdown.get(
                    "runtime_host_apply_seconds"
                ),
                "semantic_runtime_node_local_apply_seconds": semantic_breakdown.get(
                    "runtime_node_local_apply_seconds"
                ),
                "semantic_runtime_mpls_route_nat_apply_seconds": semantic_breakdown.get(
                    "runtime_mpls_route_nat_apply_seconds"
                ),
                "semantic_parameter_actual_mutation_seconds": semantic_breakdown.get(
                    "parameter_actual_mutation_seconds"
                ),
                "semantic_parameter_targeted_validation_seconds": semantic_breakdown.get(
                    "parameter_targeted_validation_seconds"
                ),
                "semantic_parameter_recovery_fallback_seconds": semantic_breakdown.get(
                    "parameter_recovery_fallback_seconds"
                ),
                "semantic_parameter_epoch_lightweight_under_event_time": (
                    semantic_breakdown.get(
                        "parameter_epoch_lightweight_under_event_time"
                    )
                ),
                "semantic_topology_or_forwarding_mutation_free": semantic_breakdown.get(
                    "topology_or_forwarding_mutation_free"
                ),
                "epoch_timing_marks_schema_version": timing_marks.get(
                    "schema_version"
                ),
                **{
                    f"timing_mark_{field}": timing_marks.get(field)
                    for field in EPOCH_TIMING_MARK_FIELDS
                },
                **{
                    f"timing_mark_elapsed_{field}_seconds": timing_mark_elapsed.get(
                        field
                    )
                    for field in EPOCH_TIMING_MARK_FIELDS
                },
                **{
                    f"derived_{field}": timing_mark_derived.get(field)
                    for field in EPOCH_TIMING_DERIVED_FIELDS
                },
            }
        )
    return rows


def _runtime_report_for_final_timing(
    run_dir: Path,
    runtime_report: Dict[str, Any] | None,
) -> Dict[str, Any] | None:
    if runtime_report is not None:
        return runtime_report
    checkpoint_path = run_dir / "real_runtime_report.json"
    if not checkpoint_path.exists():
        return None
    try:
        checkpoint = _read_json(checkpoint_path)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(checkpoint, dict):
        return None
    if not isinstance(checkpoint.get("iterations"), list):
        return None
    if not checkpoint.get("iterations"):
        return None
    return checkpoint


def _write_timing_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _manual_commands_markdown(
    *,
    run_id: str,
    run_dir: Path,
    output_dir: Path,
    topology_path: Path,
    selected_ut_ids: Sequence[str],
    selected_gateway_ids: Sequence[str],
    selected_target_ips: Sequence[str],
    example_satellite_ids: Sequence[str],
    observe_selectors: Sequence[str],
    keep_environment: bool,
) -> str:
    lines: List[str] = []
    lines.append(f"# Manual Test Commands for {run_id}")
    lines.append("")
    lines.append(f"Run directory: `{run_dir}`")
    lines.append(f"Docker compose output: `{output_dir}`")
    lines.append(f"Topology inventory: `{topology_path}`")
    lines.append("")
    lines.append("These commands assume the environment is still running.")
    if not keep_environment:
        lines.append(
            "Rerun the experiment with `--keep-environment` if you want to execute these after the automated run."
        )
    lines.append("")
    lines.append("## Basic Reachability")
    for ut_id in selected_ut_ids:
        for target_ip in selected_target_ips:
            lines.append(f"`docker exec {ut_id} ping -n -c 1 -W 1 {target_ip}`")
    lines.append("")
    lines.append("## MPLS State")
    for satellite_id in example_satellite_ids:
        lines.append(f"`docker exec {satellite_id} ip -f mpls route show`")
        lines.append(f"`docker exec {satellite_id} ip rule show`")
    for gateway_id in selected_gateway_ids:
        lines.append(f"`docker exec {gateway_id} ip -f mpls route show`")
        lines.append(f"`docker exec {gateway_id} sh -lc 'iptables -t nat -S; ip rule show'`")
    lines.append("")
    lines.append("## Counters and Queues")
    for node_id in list(selected_ut_ids) + list(selected_gateway_ids) + list(example_satellite_ids):
        lines.append(f"`docker exec {node_id} ip -s link show`")
        lines.append(f"`docker exec {node_id} tc -s qdisc show`")
    lines.append("")
    lines.append("## Runtime Observation")
    if observe_selectors:
        lines.append(
            "The automated run used packet observation selectors: "
            + ", ".join(f"`{selector}`" for selector in observe_selectors)
        )
    else:
        lines.append(
            "To collect path observation on startup, rerun with selectors such as:"
        )
        if selected_ut_ids and selected_target_ips:
            lines.append(
                f"`python3 satellite/apps/run_starlink_shell_experiment.py --run-mode real-runtime --max-satellites {len(example_satellite_ids) or 24} --keep-environment --observe-flow {selected_ut_ids[0]}:{selected_target_ips[0]}`"
            )
    lines.append("")
    lines.append("## Compose Lifecycle")
    lines.append(f"`cd {output_dir} && docker-compose ps`")
    lines.append(f"`cd {output_dir} && docker-compose logs --tail=100`")
    lines.append(f"`cd {output_dir} && docker-compose down --remove-orphans -v`")
    return "\n".join(lines) + "\n"


def _experiment_summary_payload(
    *,
    args: argparse.Namespace,
    run_id: str,
    run_dir: Path,
    preview_summary: Dict[str, Any],
    temporal_payload: Dict[str, Any],
    selected_runtime_slice_ids: Sequence[str],
    selected_parameter_slice_ids: Sequence[str],
    preview_gate: Dict[str, Any],
    runtime_report: Dict[str, Any] | None,
    runtime_blocker: Dict[str, Any] | None,
    watcher_summary: Dict[str, Any],
    phase_timing: Dict[str, Any],
    timing_analysis: Dict[str, Any],
    compose_audit: Dict[str, Any],
    startup_strategy: Dict[str, Any] | None,
    mini_internet_summary: Dict[str, Any],
    continuous_operation_summary: Dict[str, Any],
    data_provenance: Dict[str, Any],
    artifacts: Dict[str, str],
    preflight_report: Dict[str, Any] | None,
    host_prepare_report: Dict[str, Any] | None,
    post_run_preflight_report: Dict[str, Any] | None,
) -> Dict[str, Any]:
    resolved_max_host_workers = _resolved_max_host_workers(args=args)
    runtime_outcome = (
        dict(runtime_report.get("outcome") or {})
        if runtime_report is not None
        else None
    )
    if runtime_outcome is None and runtime_blocker is not None:
        runtime_outcome = {
            "real_apply_success": False,
            "ping_success": False,
            "rollback_completed": None,
            "failure_reason": str(
                runtime_blocker.get("classified_blocker")
                or runtime_blocker.get("stage")
                or "runtime_blocked"
            ),
        }
    initial_runtime_readiness = (
        dict(runtime_report.get("initial_runtime_readiness") or {})
        if runtime_report is not None
        else None
    )
    runtime_report_manifest_summary: Dict[str, Any] | None = None
    prepared_bundle_manifest_summary: Dict[str, Any] = {}
    if runtime_report is not None:
        prepared_bundle_runtime = dict(runtime_report.get("prepared_bundle_runtime") or {})
        epoch_execution_queue = list(
            prepared_bundle_runtime.get("epoch_execution_queue", [])
        )
        replenishment_events = list(
            prepared_bundle_runtime.get("replenishment_events", [])
        )
        prepared_bundle_manifest_summary = {
            "segments": list(prepared_bundle_runtime.get("segments", [])),
            "replenishment_event_count": len(replenishment_events),
            "epoch_execution_queue_count": len(epoch_execution_queue),
            "epoch_execution_record_model": prepared_bundle_runtime.get(
                "epoch_execution_record_model"
            ),
            "epoch_execution_details_jsonl": prepared_bundle_runtime.get(
                "epoch_execution_details_jsonl"
            ),
        }
        runtime_report_manifest_summary = {
            "artifact": artifacts.get("real_runtime_report_json"),
            "finalized": runtime_report.get("finalized"),
            "mode": runtime_report.get("mode"),
            "outcome": runtime_outcome,
            "timing_summary": dict(runtime_report.get("timing_summary") or {}),
            "temporal_audit": dict(runtime_report.get("temporal_audit") or {}),
            "iteration_count": len(list(runtime_report.get("iterations", []))),
            "prepared_bundle_runtime": prepared_bundle_manifest_summary,
        }
    return {
        "prepared_bundle_runtime": prepared_bundle_manifest_summary,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "run_mode": str(args.run_mode),
        "requested_mode": str(args.run_mode),
        "continuous_mode": bool(args.continuous),
        "requested_satellite_count": int(args.max_satellites),
        "requested_ut_catalog": None if args.ut_catalog is None else str(args.ut_catalog),
        "requested_gateway_catalog": (
            None if args.gateway_catalog is None else str(args.gateway_catalog)
        ),
        "requested_zone_catalog": (
            None if args.zone_catalog is None else str(args.zone_catalog)
        ),
        "requested_ut_ids": [part for part in str(args.ut_ids).split(",") if part],
        "requested_gateway_ids": [part for part in str(args.gateway_ids).split(",") if part],
        "host_runtime_configuration": {
            "requested_max_host_workers": int(args.max_host_workers),
            "effective_max_host_workers": int(resolved_max_host_workers),
            "selection_policy": (
                "explicit"
                if int(args.max_host_workers) > 0
                else "auto_bounded_conflict_safe_default"
            ),
        },
        "preview_summary": preview_summary,
        "temporal_summary": {
            "orbit_sample_count": len(list(temporal_payload.get("orbit_samples", []))),
            "runtime_epoch_count": len(list(temporal_payload.get("runtime_epochs", []))),
            "parameter_epoch_count": len(list(temporal_payload.get("parameter_epochs", []))),
            "no_op_sample_count": temporal_payload.get("no_op_sample_count"),
            "selected_runtime_slice_ids": list(selected_runtime_slice_ids),
            "selected_parameter_slice_ids": list(selected_parameter_slice_ids),
        },
        "preview_gate": preview_gate,
        "host_prepare": host_prepare_report,
        "preflight": preflight_report,
        "post_run_preflight": post_run_preflight_report,
        "post_run_clean": (
            None
            if post_run_preflight_report is None
            else post_run_preflight_report.get("clean_after_run")
        ),
        "initial_runtime_readiness": initial_runtime_readiness,
        "real_apply_success": None if runtime_outcome is None else runtime_outcome.get("real_apply_success"),
        "ping_success": None if runtime_outcome is None else runtime_outcome.get("ping_success"),
        "rollback_completed": None if runtime_outcome is None else runtime_outcome.get("rollback_completed"),
        "failure_reason": None if runtime_outcome is None else runtime_outcome.get("failure_reason"),
        "outcome": runtime_outcome,
        "runtime_report": runtime_report_manifest_summary,
        "runtime_report_artifact": artifacts.get("real_runtime_report_json"),
        "runtime_blocker": runtime_blocker,
        "watcher_summary": watcher_summary,
        "phase_timing": phase_timing,
        "timing_analysis": timing_analysis,
        "compose_audit": compose_audit,
        "startup_strategy": startup_strategy,
        "mini_internet_summary": mini_internet_summary,
        "continuous_operation_summary": continuous_operation_summary,
        "temporal_audit": None if runtime_report is None else runtime_report.get("temporal_audit"),
        "data_provenance": data_provenance,
        "generated_artifacts": artifacts,
    }


def main() -> int:
    args = parse_args()
    run_id = _generated_run_id(args)
    run_root = Path(args.run_root).resolve()
    run_dir = run_root / run_id
    config_dir = run_dir / "config"
    output_dir = run_dir / "seed_output"
    run_dir.mkdir(parents=True, exist_ok=True)
    events: List[Dict[str, Any]] = [
        {
            "timestamp_utc": _timestamp_utc(),
            "event_type": "run_started",
            "run_id": run_id,
            "run_mode": args.run_mode,
        }
    ]

    preview_args = args
    if args.run_mode == "real-runtime" and bool(args.continuous):
        initial_horizon_seconds = _initial_continuous_horizon_seconds(args=args)
        if initial_horizon_seconds is not None and initial_horizon_seconds > 0.0:
            preview_args = _namespace_with_overrides(
                args,
                horizon_seconds=float(initial_horizon_seconds),
                slice_count=max(
                    1,
                    int(
                        math.floor(
                            float(initial_horizon_seconds)
                            / _sample_interval_seconds(args=args)
                        )
                    )
                    + 1,
                ),
            )
            events.append(
                {
                    "timestamp_utc": _timestamp_utc(),
                    "event_type": "continuous_initial_horizon_selected",
                    "requested_horizon_seconds": _requested_horizon_seconds(args=args),
                    "initial_horizon_seconds": float(initial_horizon_seconds),
                }
            )

    preview_summary = _run_preview(
        args=preview_args,
        run_dir=run_dir,
        config_dir=config_dir,
        output_dir=output_dir,
        events=events,
    )
    seed_topology_summary = dict(preview_summary.get("seed_topology", {}))
    seed_topology_timing = dict(seed_topology_summary.get("timing", {}))
    phase_timing: Dict[str, Any] = {
        "preview_wall_seconds": round(
            next(
                (
                    float(event.get("wall_seconds", 0.0))
                    for event in reversed(events)
                    if event.get("event_type") == "preview_completed"
                ),
                0.0,
            ),
            6,
        ),
        "seed_render_wall_seconds": float(seed_topology_timing.get("render_wall_seconds", 0.0)),
        "seed_compile_output_wall_seconds": float(
            seed_topology_timing.get("compile_output_wall_seconds", 0.0)
        ),
        "compose_build_wall_seconds": 0.0,
        "compose_up_wall_seconds": 0.0,
        "container_readiness_wall_seconds": 0.0,
        "host_prepare_wall_seconds": 0.0,
        "post_run_preflight_wall_seconds": 0.0,
        "initial_runtime_readiness_wall_seconds": 0.0,
        "pre_epoch_host_runtime_prewarm_wall_seconds": 0.0,
        "pre_epoch_host_runtime_prepare_wall_seconds": 0.0,
        "pre_epoch_host_runtime_reconcile_wall_seconds": 0.0,
        "first_runtime_epoch_transition_wall_seconds": 0.0,
        "first_runtime_epoch_network_mutation_wall_seconds": 0.0,
        "first_runtime_epoch_node_local_apply_wall_seconds": 0.0,
        "first_runtime_epoch_direct_backend_prepare_wall_seconds": 0.0,
        "first_runtime_epoch_direct_backend_reconcile_wall_seconds": 0.0,
    }
    original_sigterm_handler = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _sigterm_as_keyboard_interrupt)
    progress_state = _new_runtime_progress_state(
        run_id=run_id,
        run_mode=str(args.run_mode),
    )
    _mark_runtime_progress(
        progress_state,
        phase="preview_completed",
        event_type="preview_completed",
        phase_detail={"run_dir": str(run_dir)},
    )
    progress_stop_event, progress_thread = _start_runtime_progress_heartbeat(
        args=args,
        run_dir=run_dir,
        progress_state=progress_state,
        phase_timing=phase_timing,
        events=events,
    )

    def _progress_update(
        *,
        phase: str,
        event_type: str | None = None,
        phase_detail: Dict[str, Any] | None = None,
        epoch_context: Dict[str, Any] | None = None,
        runtime_epochs_completed: int | None = None,
        parameter_epochs_completed: int | None = None,
        write_events: bool = True,
    ) -> None:
        _mark_runtime_progress(
            progress_state,
            phase=phase,
            event_type=event_type,
            phase_detail=phase_detail,
            epoch_context=epoch_context,
            runtime_epochs_completed=runtime_epochs_completed,
            parameter_epochs_completed=parameter_epochs_completed,
        )
        _flush_runtime_progress_artifacts(
            args=args,
            run_dir=run_dir,
            progress_state=progress_state,
            phase_timing=phase_timing,
            events=events,
            write_events=write_events,
        )

    _flush_runtime_progress_artifacts(
        args=args,
        run_dir=run_dir,
        progress_state=progress_state,
        phase_timing=phase_timing,
        events=events,
        write_events=True,
    )
    temporal_artifacts = _load_temporal_artifacts(preview_summary)
    artifacts = dict(temporal_artifacts["artifacts"])
    planner_preview = dict(temporal_artifacts["planner_preview"])
    temporal_plan = dict(temporal_artifacts["temporal_plan"])
    shell_manifest = dict(temporal_artifacts["shell_manifest"])

    (
        all_sample_frames,
        selected_epoch_queue,
        selected_runtime_slice_ids,
        selected_parameter_slice_ids,
    ) = _temporal_execution_queue(
        temporal_plan,
        max_epochs=args.max_epochs,
        max_runtime_epochs=args.max_runtime_epochs,
        max_parameter_epochs=args.max_parameter_epochs,
    )
    runtime_update_plan_summary, network_mutation_plan_summary, runtime_epoch_summary = (
        _build_plan_summaries(
            planner_preview=planner_preview,
            temporal_plan=temporal_plan,
            selected_runtime_slice_ids=selected_runtime_slice_ids,
        )
    )
    return_path_audit = audit_planner_preview(
        planner_preview,
        selected_scope_ids=selected_runtime_slice_ids,
    )
    parameter_epoch_summary = _parameter_epoch_summary(temporal_plan)
    observation_frame_summary = _observation_frame_summary(temporal_plan)
    resource_budget = _resource_budget_summary(temporal_plan)

    # Copy or materialize stable run artifacts.
    _copy_artifact(Path(artifacts["shell_manifest_json"]).resolve(), run_dir / "shell_manifest.json")
    _copy_artifact(Path(artifacts["temporal_plan_json"]).resolve(), run_dir / "temporal_plan.json")
    _copy_artifact(Path(artifacts["planner_preview_json"]).resolve(), run_dir / "planner_preview.json")
    _copy_artifact(Path(artifacts["snapshot_preview_json"]).resolve(), run_dir / "snapshot_preview.json")
    _copy_artifact(Path(artifacts["topology_json"]).resolve(), run_dir / "topology.json")
    _copy_artifact(Path(artifacts["link_state_json"]).resolve(), run_dir / "link_state.json")
    _copy_artifact(
        Path(artifacts["propagated_positions_json"]).resolve(),
        run_dir / "propagated_positions.json",
    )
    planning_artifact_specs = (
        ("zone_model_json", "zone_model.json"),
        ("zone_anchor_schedule_json", "zone_anchor_schedule.json"),
        ("gz_fec_plan_json", "gz_fec_plan.json"),
        ("gz_update_diff_summary_json", "gz_update_diff_summary.json"),
        ("zone_anchor_quality_summary_json", "zone_anchor_quality_summary.json"),
        ("zone_availability_analysis_json", "zone_availability_analysis.json"),
    )
    prepared_planning_artifacts: Dict[str, str] = {}
    for artifact_key, filename in planning_artifact_specs:
        artifact_path = artifacts.get(artifact_key)
        if not artifact_path:
            continue
        destination = run_dir / filename
        _copy_artifact(Path(artifact_path).resolve(), destination)
        prepared_planning_artifacts[artifact_key] = str(destination)
    _write_json(
        run_dir / "ground_manifest.json",
        {
            "ground_model": dict(preview_summary.get("ground_model", {})),
            "effective_ground_locations": dict(
                preview_summary.get("effective_ground_locations", {})
            ),
            "data_provenance": dict(
                shell_manifest.get("data_provenance", {}).get("ground", {})
            ),
        },
    )
    _write_json(run_dir / "resource_budget.json", resource_budget)
    _write_json(run_dir / "runtime_epoch_summary.json", runtime_epoch_summary)
    _write_json(run_dir / "parameter_epoch_summary.json", parameter_epoch_summary)
    _write_json(run_dir / "observation_frame_summary.json", observation_frame_summary)
    _write_json(run_dir / "return_path_audit.json", return_path_audit)
    _write_json(run_dir / "epoch_execution_queue.json", selected_epoch_queue)
    _write_json(
        run_dir / "runtime_update_plan_summary.json",
        runtime_update_plan_summary,
    )
    _write_json(
        run_dir / "network_mutation_plan_summary.json",
        network_mutation_plan_summary,
    )
    _write_json(
        run_dir / "prepared_update_bundle.json",
        {
            "strict_mode": True,
            "selected_runtime_slice_ids": list(selected_runtime_slice_ids),
            "selected_parameter_slice_ids": list(selected_parameter_slice_ids),
            "orbit_sample_count": len(list(temporal_plan.get("orbit_samples", []))),
            "runtime_epoch_count": len(list(temporal_plan.get("runtime_epochs", []))),
            "parameter_epoch_count": len(list(temporal_plan.get("parameter_epochs", []))),
            "prepared_artifacts": {
                "shell_manifest_json": str(run_dir / "shell_manifest.json"),
                "ground_manifest_json": str(run_dir / "ground_manifest.json"),
                "observation_frame_summary_json": str(run_dir / "observation_frame_summary.json"),
                "return_path_audit_json": str(run_dir / "return_path_audit.json"),
                "epoch_execution_queue_json": str(run_dir / "epoch_execution_queue.json"),
                "temporal_plan_json": str(run_dir / "temporal_plan.json"),
                "resource_budget_json": str(run_dir / "resource_budget.json"),
                "runtime_epoch_summary_json": str(run_dir / "runtime_epoch_summary.json"),
                "parameter_epoch_summary_json": str(run_dir / "parameter_epoch_summary.json"),
                "runtime_update_plan_summary_json": str(
                    run_dir / "runtime_update_plan_summary.json"
                ),
                "network_mutation_plan_summary_json": str(
                    run_dir / "network_mutation_plan_summary.json"
                ),
                "snapshot_preview_json": str(run_dir / "snapshot_preview.json"),
                "planner_preview_json": str(run_dir / "planner_preview.json"),
                "topology_json": str(run_dir / "topology.json"),
                "link_state_json": str(run_dir / "link_state.json"),
                **prepared_planning_artifacts,
            },
            "resource_budget": resource_budget,
            "all_sample_frames": all_sample_frames,
            "selected_epoch_queue": selected_epoch_queue,
            "runtime_epoch_summaries": runtime_epoch_summary,
            "parameter_epoch_summaries": parameter_epoch_summary,
            "observation_frame_summaries": observation_frame_summary,
            "return_path_audit": return_path_audit,
            "continuous_operation_prepared": dict(
                temporal_plan.get("preparation_plan", {}).get("continuous_operation_prepared", {})
            ),
        },
    )
    _write_json(
        run_dir / "ping_results.json",
        {
            "initial_runtime_readiness": None,
            "post_transition_connectivity": [],
            "watchers": {},
        },
    )
    _write_json(
        run_dir / "path_observation_results.json",
        {
            "observe_selectors": list(args.observe_flow),
            "iterations": [],
        },
    )
    _write_json(
        run_dir / "timing.json",
        {
            "finalized": False,
            "phase_timing": phase_timing,
            "timing_analysis": {},
            "timing_summary": {},
            "rows": [],
        },
    )
    _write_timing_csv(
        run_dir / "timing.csv",
        [],
    )

    topology_path = Path(artifacts["topology_json"]).resolve()
    link_state_path = Path(artifacts["link_state_json"]).resolve()
    snapshot_preview_json = Path(artifacts["snapshot_preview_json"]).resolve()
    topology_inventory = _read_json(topology_path)
    compose_path = output_dir / "docker-compose.yml"
    if compose_path.exists():
        compose_metadata = _compose_service_metadata(output_dir)
        output_digest = _hash_directory(output_dir)
        compose_audit = _compose_audit(
            topology=topology_inventory,
            output_dir=output_dir,
        )
    else:
        compose_metadata = {
            "container_names": [],
            "service_image_tags": {},
            "service_name_by_container": {},
        }
        output_digest = None
        compose_audit = _compose_audit_unavailable(
            output_dir=output_dir,
            reason="compiled_seed_output_not_present",
        )
    _write_json(run_dir / "compose_audit.json", compose_audit)
    startup_strategy: Dict[str, Any] | None = None
    mini_internet_summary = _mini_internet_summary(
        topology=topology_inventory,
        compose_audit=compose_audit,
    )
    _write_json(run_dir / "mini_internet_summary.json", mini_internet_summary)
    data_provenance = _data_provenance_summary(
        preview_summary=preview_summary,
        shell_manifest=shell_manifest,
        mini_internet_summary=mini_internet_summary,
    )
    _write_json(
        run_dir / "ground_manifest.json",
        {
            "ground_model": dict(preview_summary.get("ground_model", {})),
            "effective_ground_locations": dict(
                preview_summary.get("effective_ground_locations", {})
            ),
            "data_provenance": {
                "ground": dict(data_provenance.get("ground", {})),
                "mini_internet": dict(data_provenance.get("mini_internet", {})),
            },
        },
    )
    all_snapshots = _load_snapshots(snapshot_preview_json)
    selected_epoch_slice_ids = [
        str(entry.get("slice_id"))
        for entry in selected_epoch_queue
    ]
    execution_snapshots = _filter_snapshots_by_slice_ids(
        all_snapshots,
        selected_epoch_slice_ids,
    )
    slice_contexts = _build_slice_contexts(
        topology_path=topology_path,
        link_state_path=link_state_path,
        snapshots=execution_snapshots,
    )
    preview_gate = {
        "all_preview_valid": all(
            context["slice_report"]["validation"]["all_valid"] is True
            for context in slice_contexts
        ),
        "all_real_supported": all(
            context["slice_report"]["real_execution_support"]["supported"] is True
            for context in slice_contexts
        ),
        "selected_runtime_epoch_count": len(
            [
                context
                for context in slice_contexts
                if str(context["snapshot"].time_slice.get("slice_id")) in set(selected_runtime_slice_ids)
            ]
        ),
        "selected_parameter_epoch_count": len(
            [
                context
                for context in slice_contexts
                if str(context["snapshot"].time_slice.get("slice_id")) in set(selected_parameter_slice_ids)
            ]
        ),
        "unsupported_slices": [
            {
                "slice_id": context["snapshot"].time_slice.get("slice_id"),
                "blockers": list(context["slice_report"]["real_execution_support"]["blockers"]),
            }
            for context in slice_contexts
            if context["slice_report"]["real_execution_support"]["supported"] is not True
        ],
    }

    runtime_report: Dict[str, Any] | None = None
    runtime_blocker: Dict[str, Any] | None = None
    watcher_summary: Dict[str, Any] = {"enabled": False, "watchers": {}}
    host_prepare_report: Dict[str, Any] | None = None
    preflight_report: Dict[str, Any] | None = None
    post_run_preflight_report: Dict[str, Any] | None = None
    compose_environment_reuse_summary: Dict[str, Any] | None = None
    compose_up_stage_results_summary: List[Dict[str, Any]] = []
    force_compose_down_after = False
    if args.run_mode == "real-runtime":
        runtime_lock_path = SATELLITE_DIR / "output" / "skyseed_real_runtime_active.lock"
        runtime_lock_metadata_path = run_dir / "runtime_lock_metadata.json"
        runtime_lock_active = False
        if str(args.host_prepare_policy) != "off":
            _progress_update(
                phase="host_prepare",
                event_type="host_prepare_started",
                phase_detail={"policy": str(args.host_prepare_policy)},
            )
            host_prepare_started_at = time.perf_counter()
            try:
                host_prepare_report = _prepare_real_runtime_host_environment()
                host_prepare_report["policy"] = str(args.host_prepare_policy)
                host_prepare_report["prepared_at_utc"] = _timestamp_utc()
                host_prepare_report["wall_seconds"] = round(
                    time.perf_counter() - host_prepare_started_at,
                    6,
                )
                phase_timing["host_prepare_wall_seconds"] = float(
                    host_prepare_report["wall_seconds"]
                )
                _write_json(run_dir / "host_prepare.json", host_prepare_report)
                events.append(
                    {
                        "timestamp_utc": _timestamp_utc(),
                        "event_type": "host_prepare_completed",
                        "wall_seconds": host_prepare_report["wall_seconds"],
                    }
                )
                _progress_update(
                    phase="host_prepare_completed",
                    event_type="host_prepare_completed",
                    phase_detail={"wall_seconds": host_prepare_report["wall_seconds"]},
                )
            except (HostMplsPreparationError, RuntimeError, subprocess.SubprocessError) as exc:
                phase_timing["host_prepare_wall_seconds"] = round(
                    time.perf_counter() - host_prepare_started_at,
                    6,
                )
                host_prepare_report = {
                    "prepared": False,
                    "policy": str(args.host_prepare_policy),
                    "wall_seconds": phase_timing["host_prepare_wall_seconds"],
                    "error": str(exc),
                }
                _write_json(run_dir / "host_prepare.json", host_prepare_report)
                events.append(
                    {
                        "timestamp_utc": _timestamp_utc(),
                        "event_type": "host_prepare_failed",
                        "error": str(exc),
                    }
                )
                _progress_update(
                    phase="host_prepare_failed",
                    event_type="host_prepare_failed",
                    phase_detail={"error": str(exc)},
                )
                runtime_blocker = {
                    "stage": "host_prepare",
                    "classified_blocker": "host_prepare_failed",
                    "host_prepare": host_prepare_report,
                }
        else:
            host_prepare_report = {
                "prepared": False,
                "skipped": True,
                "policy": str(args.host_prepare_policy),
            }
            _write_json(run_dir / "host_prepare.json", host_prepare_report)
        if str(args.preflight_policy) != "off":
            _progress_update(
                phase="preflight",
                event_type="preflight_started",
            )
            if runtime_blocker is None:
                preflight_report = run_skyseed_preflight(
                    ignore_pids=_current_preflight_ignore_pids()
                )
                _write_json(run_dir / "preflight.json", preflight_report)
                events.append(
                    {
                        "timestamp_utc": _timestamp_utc(),
                        "event_type": "preflight_completed",
                        "ready": preflight_report.get("assessment", {}).get("ready"),
                        "blocking_reasons": list(
                            preflight_report.get("assessment", {}).get("blocking_reasons", [])
                        ),
                        "warning_reasons": list(
                            preflight_report.get("assessment", {}).get("warning_reasons", [])
                        ),
                    }
                )
                if (
                    str(args.preflight_policy) == "require-clean"
                    and preflight_report.get("assessment", {}).get("ready") is not True
                ):
                    _progress_update(
                        phase="preflight_blocked",
                        event_type="preflight_blocked",
                        phase_detail={
                            "blocking_reasons": list(
                                preflight_report.get("assessment", {}).get("blocking_reasons", [])
                            )
                        },
                    )
                    runtime_blocker = {
                        "stage": "preflight",
                        "classified_blocker": "preflight_blocked_real_runtime",
                        "preflight": preflight_report,
                    }
            else:
                preflight_report = {
                    "skipped": True,
                    "reason": "host_prepare_failed",
                }
                _write_json(run_dir / "preflight.json", preflight_report)
        compose_down_before = None
        compose_network_scrub_before = None
        compose_build = None
        compose_up = None
        compose_up_stage_results = []
        compose_down_after = None
        container_readiness = None
        compose_service_snapshot = None
        compose_environment_reuse: Dict[str, Any] | None = None
        should_build, build_reuse_summary = _should_run_compose_build(
            policy=str(args.compose_build_policy),
            output_digest=output_digest,
            output_dir=output_dir,
            service_image_tags=dict(compose_metadata.get("service_image_tags", {})),
            cache_dir=DEFAULT_BUILD_CACHE_DIR,
        )
        if bool(args.reuse_running_compose_environment):
            compose_service_snapshot = _compose_service_state_snapshot(
                output_dir=output_dir,
                container_names=list(compose_metadata.get("container_names", [])),
            )
            reuse_ready = (
                str(build_reuse_summary.get("reason")) == "cache_digest_match_and_images_present"
                and int(compose_service_snapshot.get("state_counts", {}).get("running", 0))
                == len(list(compose_metadata.get("container_names", [])))
                and int(compose_service_snapshot.get("state_counts", {}).get("missing", 0)) == 0
                and int(compose_service_snapshot.get("state_counts", {}).get("created", 0)) == 0
                and int(compose_service_snapshot.get("state_counts", {}).get("exited", 0)) == 0
                and int(compose_service_snapshot.get("state_counts", {}).get("restarting", 0)) == 0
            )
            compose_environment_reuse = {
                "requested": True,
                "eligible": reuse_ready,
                "reason": (
                    "cache_digest_match_and_all_required_containers_running"
                    if reuse_ready
                    else "environment_not_ready_for_safe_reuse"
                ),
                "state_counts": dict(compose_service_snapshot.get("state_counts", {})),
            }
        else:
            compose_environment_reuse = {
                "requested": False,
                "eligible": False,
                "reason": "not_requested",
            }
        compose_environment_reuse_summary = dict(compose_environment_reuse)
        startup_strategy = _resolve_compose_startup_strategy(
            requested_mode=str(args.compose_startup_mode),
            compose_audit=compose_audit,
            compose_environment_reuse_requested=bool(args.reuse_running_compose_environment),
        )
        watcher_processes: List[Tuple[Dict[str, str], subprocess.Popen[str], Path]] = []
        if runtime_blocker is None:
            _write_runtime_lock(
                runtime_lock_path,
                run_id=run_id,
                run_mode=str(args.run_mode),
            )
            _write_json(
                runtime_lock_metadata_path,
                {
                    "active_lock_path": str(runtime_lock_path),
                    "run_id": run_id,
                    "run_mode": str(args.run_mode),
                    "pid": os.getpid(),
                    "created_at_utc": _timestamp_utc(),
                },
            )
            runtime_lock_active = True
            events.append(
                {
                    "timestamp_utc": _timestamp_utc(),
                    "event_type": "runtime_lock_created",
                    "lock_path": str(runtime_lock_path),
                }
            )
        try:
            if runtime_blocker is None and preview_gate["all_preview_valid"] and preview_gate["all_real_supported"]:
                try:
                    _write_json(
                        run_dir / "compose_build_reuse.json",
                        {
                            "output_digest": output_digest,
                            "compose_metadata": compose_metadata,
                            "decision": build_reuse_summary,
                            "environment_reuse": compose_environment_reuse,
                        },
                    )
                    if bool(compose_environment_reuse.get("eligible")):
                        events.append(
                            {
                                "timestamp_utc": _timestamp_utc(),
                                "event_type": "compose_environment_reused",
                                "output_dir": str(output_dir),
                                "reason": compose_environment_reuse.get("reason"),
                            }
                        )
                        _progress_update(
                            phase="container_readiness",
                            event_type="compose_environment_reused",
                            phase_detail={
                                "reused_existing_environment": True,
                                "reason": compose_environment_reuse.get("reason"),
                            },
                        )
                        container_readiness = {
                            "ready": True,
                            "required_container_count": len(
                                list(compose_metadata.get("container_names", []))
                            ),
                            "wall_seconds": 0.0,
                            "reused_existing_environment": True,
                        }
                    else:
                        compose_down_before = _compose_down(output_dir)
                        compose_network_scrub_before = _compose_force_remove_stale_networks(
                            output_dir
                        )
                        prestart_preflight = run_skyseed_preflight(
                            ignore_pids=_current_preflight_ignore_pids()
                        )
                        prestart_residue = _compose_residue_summary(prestart_preflight)
                        auto_prestart_cleanup = None
                        direct_backend_only_residue = (
                            int(prestart_residue.get("direct_backend_host_interface_count", 0)) > 0
                            and int(prestart_residue.get("compose_project_container_count", 0)) == 0
                            and int(prestart_residue.get("named_skyseed_container_count", 0)) == 0
                            and int(prestart_residue.get("compose_project_network_count", 0)) == 0
                            and int(prestart_residue.get("named_skyseed_network_count", 0)) == 0
                            and int(prestart_residue.get("compose_process_count", 0)) == 0
                        )
                        if direct_backend_only_residue:
                            auto_prestart_cleanup = cleanup_direct_backend_host_artifacts(
                                list(
                                    prestart_preflight.get("checks", {})
                                    .get("direct_backend_host_artifacts", {})
                                    .get("interfaces", [])
                                )
                            )
                            events.append(
                                {
                                    "timestamp_utc": _timestamp_utc(),
                                    "event_type": "prestart_direct_backend_cleanup_completed",
                                    "removed_interface_count": int(
                                        auto_prestart_cleanup.get("removed_interface_count", 0)
                                    ),
                                    "remaining_interface_count": int(
                                        auto_prestart_cleanup.get("remaining_interface_count", 0)
                                    ),
                                    "failed_interface_removal_count": len(
                                        list(
                                            auto_prestart_cleanup.get(
                                                "failed_interface_removals", []
                                            )
                                        )
                                    ),
                                }
                            )
                            prestart_preflight = run_skyseed_preflight(
                                ignore_pids=_current_preflight_ignore_pids()
                            )
                            prestart_residue = _compose_residue_summary(prestart_preflight)
                        if any(int(value) > 0 for value in prestart_residue.values()):
                            runtime_blocker = {
                                "stage": "compose_cleanup_before_startup",
                                "classified_blocker": "stale_compose_environment_after_requested_cleanup",
                                "compose_down_before": (
                                    None
                                    if compose_down_before is None
                                    else {
                                        "returncode": compose_down_before.returncode,
                                        "stdout": compose_down_before.stdout,
                                        "stderr": compose_down_before.stderr,
                                    }
                                ),
                                "compose_network_scrub_before": compose_network_scrub_before,
                                "auto_prestart_cleanup": auto_prestart_cleanup,
                                "prestart_residue": prestart_residue,
                                "prestart_preflight": prestart_preflight,
                            }
                            raise RuntimeError(
                                "compose cleanup left scoped Skyseed residue before startup"
                            )
                    if not bool(compose_environment_reuse.get("eligible")) and should_build:
                        events.append(
                            {
                                "timestamp_utc": _timestamp_utc(),
                                "event_type": "compose_build_started",
                                "output_dir": str(output_dir),
                                "policy": str(args.compose_build_policy),
                            }
                        )
                        _progress_update(
                            phase="compose_build",
                            event_type="compose_build_started",
                            phase_detail={
                                "policy": str(args.compose_build_policy),
                                "compose_parallel_limit": int(args.compose_parallel_limit),
                            },
                        )
                        compose_build_result = _compose_build_with_timeout(
                            output_dir=output_dir,
                            timeout_seconds=float(args.compose_build_timeout_seconds),
                            compose_parallel_limit=int(args.compose_parallel_limit),
                        )
                        phase_timing["compose_build_wall_seconds"] = round(
                            float(compose_build_result.get("wall_seconds", 0.0)),
                            6,
                        )
                        if compose_build_result.get("timed_out") is True:
                            runtime_blocker = {
                                "stage": "docker_compose_build",
                                "classified_blocker": "docker_compose_build_timeout",
                                "timeout_seconds": float(
                                    args.compose_build_timeout_seconds
                                ),
                                "wall_seconds": phase_timing[
                                    "compose_build_wall_seconds"
                                ],
                                "stdout": compose_build_result.get("stdout"),
                                "stderr": compose_build_result.get("stderr"),
                            }
                            raise RuntimeError("docker-compose build timed out")
                        compose_build = compose_build_result.get("completed")
                        if compose_build.returncode != 0:
                            runtime_blocker = {
                                "stage": "docker_compose_build",
                                "returncode": compose_build.returncode,
                                "stdout": compose_build.stdout,
                                "stderr": compose_build.stderr,
                                "classified_blocker": "docker_compose_build_failed",
                            }
                            compose_build = None
                            raise RuntimeError("docker-compose build failed")
                        _save_compose_build_cache(
                            cache_dir=DEFAULT_BUILD_CACHE_DIR,
                            output_dir=output_dir,
                            payload={
                                "output_digest": output_digest,
                                "saved_at_utc": _timestamp_utc(),
                                "service_image_tags": dict(
                                    compose_metadata.get("service_image_tags", {})
                                ),
                            },
                        )
                        events.append(
                            {
                                "timestamp_utc": _timestamp_utc(),
                                "event_type": "compose_build_completed",
                                "wall_seconds": phase_timing["compose_build_wall_seconds"],
                                "output_dir": str(output_dir),
                            }
                        )
                        _progress_update(
                            phase="compose_build_completed",
                            event_type="compose_build_completed",
                            phase_detail={
                                "wall_seconds": phase_timing["compose_build_wall_seconds"],
                            },
                        )
                    else:
                        events.append(
                            {
                                "timestamp_utc": _timestamp_utc(),
                                "event_type": "compose_build_skipped",
                                "reason": build_reuse_summary.get("reason"),
                                "output_dir": str(output_dir),
                            }
                        )
                        _progress_update(
                            phase="compose_build_skipped",
                            event_type="compose_build_skipped",
                            phase_detail={
                                "reason": build_reuse_summary.get("reason"),
                            },
                        )
                    if not bool(compose_environment_reuse.get("eligible")):
                        events.append(
                            {
                                "timestamp_utc": _timestamp_utc(),
                                "event_type": "compose_up_started",
                                "output_dir": str(output_dir),
                                "startup_mode": str(
                                    startup_strategy.get("effective_mode", args.compose_startup_mode)
                                ),
                            }
                        )
                        _progress_update(
                            phase="compose_up",
                            event_type="compose_up_started",
                            phase_detail={
                                "startup_mode": str(
                                    startup_strategy.get(
                                        "effective_mode",
                                        args.compose_startup_mode,
                                    )
                                ),
                                "compose_parallel_limit": int(args.compose_parallel_limit),
                            },
                        )
                        compose_startup_stages = _compose_startup_stages(
                            compose_audit=compose_audit,
                            service_name_by_container=dict(
                                compose_metadata.get("service_name_by_container", {})
                            ),
                            mode=str(
                                startup_strategy.get("effective_mode", args.compose_startup_mode)
                            ),
                            satellite_batch_size=int(args.compose_satellite_batch_size),
                        )
                        compose_service_name_by_container = dict(
                            compose_metadata.get("service_name_by_container", {})
                        )
                        compose_up_started_at = time.perf_counter()
                        remaining_timeout = float(args.compose_up_timeout_seconds)
                        compose_up_overall_deadline_warning_emitted = False
                        for stage in compose_startup_stages:
                            stage_services = list(stage.get("service_names", []))
                            stage_id = str(stage.get("stage_id"))
                            stage_type = str(stage.get("stage_type"))
                            compose_up_elapsed_before_stage = (
                                time.perf_counter() - compose_up_started_at
                            )
                            overall_startup_deadline_exceeded = (
                                float(args.compose_up_timeout_seconds) > 0.0
                                and compose_up_elapsed_before_stage
                                > float(args.compose_up_timeout_seconds)
                            )
                            if (
                                overall_startup_deadline_exceeded
                                and not compose_up_overall_deadline_warning_emitted
                            ):
                                compose_up_overall_deadline_warning_emitted = True
                                events.append(
                                    {
                                        "timestamp_utc": _timestamp_utc(),
                                        "event_type": "compose_up_overall_deadline_exceeded",
                                        "configured_timeout_seconds": float(
                                            args.compose_up_timeout_seconds
                                        ),
                                        "elapsed_seconds": round(
                                            compose_up_elapsed_before_stage,
                                            6,
                                        ),
                                        "continuing_with_stage_watchdog": True,
                                        "next_stage_id": stage_id,
                                    }
                                )
                                _progress_update(
                                    phase="compose_up_deadline_warning",
                                    event_type="compose_up_overall_deadline_exceeded",
                                    phase_detail={
                                        "configured_timeout_seconds": float(
                                            args.compose_up_timeout_seconds
                                        ),
                                        "elapsed_seconds": round(
                                            compose_up_elapsed_before_stage,
                                            6,
                                        ),
                                        "continuing_with_stage_watchdog": True,
                                        "next_stage_id": stage_id,
                                    },
                                )
                            events.append(
                                {
                                    "timestamp_utc": _timestamp_utc(),
                                    "event_type": "compose_up_stage_started",
                                    "stage_id": stage_id,
                                    "stage_type": stage_type,
                                    "service_count": len(stage_services),
                                    "service_names": stage_services,
                                }
                            )
                            _progress_update(
                                phase="compose_up_stage",
                                event_type="compose_up_stage_started",
                                phase_detail={
                                    "stage_id": stage_id,
                                    "stage_type": stage_type,
                                    "service_count": len(stage_services),
                                },
                            )
                            stage_timeout_seconds = _compose_stage_timeout_seconds(
                                remaining_timeout=remaining_timeout,
                                stage_type=stage_type,
                            )
                            compose_up_result = _compose_up_with_timeout(
                                output_dir=output_dir,
                                timeout_seconds=stage_timeout_seconds,
                                services=stage_services,
                                no_deps=bool(stage.get("no_deps", False)),
                                compose_parallel_limit=int(args.compose_parallel_limit),
                            )
                            stage_wall_seconds = round(
                                float(compose_up_result.get("wall_seconds", 0.0)),
                                6,
                            )
                            remaining_timeout = max(
                                0.0,
                                float(args.compose_up_timeout_seconds)
                                - (time.perf_counter() - compose_up_started_at),
                            )
                            stage_result = {
                                "stage_id": stage_id,
                                "stage_type": stage_type,
                                "service_count": len(stage_services),
                                "service_names": stage_services,
                                "overall_startup_deadline_exceeded_before_stage": (
                                    overall_startup_deadline_exceeded
                                ),
                                "no_deps": bool(stage.get("no_deps", False)),
                                "timeout_seconds": round(stage_timeout_seconds, 6),
                                "wall_seconds": stage_wall_seconds,
                                "timed_out": bool(compose_up_result.get("timed_out")),
                                "returncode": None,
                            }
                            completed = compose_up_result.get("completed")
                            if completed is not None:
                                stage_result.update(
                                    {
                                        "returncode": completed.returncode,
                                        "stdout": completed.stdout,
                                        "stderr": completed.stderr,
                                    }
                                )
                            else:
                                stage_result.update(
                                    {
                                        "stdout": compose_up_result.get("stdout"),
                                        "stderr": compose_up_result.get("stderr"),
                                    }
                                )
                            compose_up_stage_results.append(stage_result)
                            compose_up_stage_results_summary = list(compose_up_stage_results)
                            events.append(
                                {
                                    "timestamp_utc": _timestamp_utc(),
                                    "event_type": "compose_up_stage_completed",
                                    "stage_id": stage_id,
                                    "timed_out": bool(compose_up_result.get("timed_out")),
                                    "wall_seconds": stage_wall_seconds,
                                    "service_count": len(stage_services),
                                }
                            )
                            _progress_update(
                                phase="compose_up_stage",
                                event_type="compose_up_stage_completed",
                                phase_detail={
                                    "stage_id": stage_id,
                                    "stage_type": stage_type,
                                    "service_count": len(stage_services),
                                    "timed_out": bool(compose_up_result.get("timed_out")),
                                    "wall_seconds": stage_wall_seconds,
                                },
                            )
                            if compose_up_result.get("timed_out") is True:
                                compose_service_snapshot = _compose_service_state_snapshot(
                                    output_dir=output_dir,
                                    container_names=list(
                                        compose_metadata.get("container_names", [])
                                    ),
                                )
                                running_stage_services = _stage_running_service_names(
                                    compose_service_snapshot=compose_service_snapshot,
                                    stage_services=stage_services,
                                    service_name_by_container=compose_service_name_by_container,
                                )
                                retry_stage_services = [
                                    service_name
                                    for service_name in stage_services
                                    if service_name not in set(running_stage_services)
                                ]
                                retry_results: List[Dict[str, Any]] = []
                                retry_failed = False
                                recovery_timeout_seconds = _compose_stage_recovery_timeout_seconds(
                                    stage_type=stage_type
                                )
                                if retry_stage_services:
                                    events.append(
                                        {
                                            "timestamp_utc": _timestamp_utc(),
                                            "event_type": "compose_up_stage_retry_started",
                                            "stage_id": stage_id,
                                            "retry_service_names": list(retry_stage_services),
                                            "already_running_service_names": list(running_stage_services),
                                            "recovery_timeout_seconds": recovery_timeout_seconds,
                                        }
                                    )
                                    recovery_started_at = time.perf_counter()
                                    for retry_batch in _compose_stage_retry_batches(
                                        service_names=retry_stage_services,
                                        stage_type=stage_type,
                                    ):
                                        retry_remaining_timeout = max(
                                            0.0,
                                            recovery_timeout_seconds
                                            - (time.perf_counter() - recovery_started_at),
                                        )
                                        if retry_remaining_timeout <= 0.0:
                                            retry_failed = True
                                            break
                                        retry_timeout_seconds = _compose_stage_timeout_seconds(
                                            remaining_timeout=retry_remaining_timeout,
                                            stage_type=stage_type,
                                        )
                                        retry_result = _compose_up_with_timeout(
                                            output_dir=output_dir,
                                            timeout_seconds=retry_timeout_seconds,
                                            services=list(retry_batch),
                                            no_deps=bool(stage.get("no_deps", False)),
                                            compose_parallel_limit=2,
                                        )
                                        retry_entry = {
                                            "service_names": list(retry_batch),
                                            "timeout_seconds": round(retry_timeout_seconds, 6),
                                            "wall_seconds": round(
                                                float(retry_result.get("wall_seconds", 0.0)),
                                                6,
                                            ),
                                            "timed_out": bool(retry_result.get("timed_out")),
                                        }
                                        retry_completed = retry_result.get("completed")
                                        if retry_completed is not None:
                                            retry_entry["returncode"] = retry_completed.returncode
                                            retry_entry["stdout"] = retry_completed.stdout
                                            retry_entry["stderr"] = retry_completed.stderr
                                        else:
                                            retry_entry["stdout"] = retry_result.get("stdout")
                                            retry_entry["stderr"] = retry_result.get("stderr")
                                        retry_results.append(retry_entry)
                                        if (
                                            retry_result.get("timed_out") is True
                                            or retry_completed is None
                                            or retry_completed.returncode != 0
                                        ):
                                            retry_failed = True
                                            break
                                    compose_service_snapshot = _compose_service_state_snapshot(
                                        output_dir=output_dir,
                                        container_names=list(
                                            compose_metadata.get("container_names", [])
                                        ),
                                    )
                                    recovered_stage_services = _stage_running_service_names(
                                        compose_service_snapshot=compose_service_snapshot,
                                        stage_services=stage_services,
                                        service_name_by_container=compose_service_name_by_container,
                                    )
                                    retry_missing_services = [
                                        service_name
                                        for service_name in stage_services
                                        if service_name not in set(recovered_stage_services)
                                    ]
                                    stage_result["retry_after_timeout"] = {
                                        "attempted": True,
                                        "initial_running_service_names": list(running_stage_services),
                                        "retry_service_names": list(retry_stage_services),
                                        "retry_mode": "missing_services_after_timeout",
                                        "retry_unit": (
                                            "single_service"
                                            if stage_type == "satellite_batch"
                                            else "single_service"
                                        ),
                                        "recovery_timeout_seconds": round(
                                            recovery_timeout_seconds,
                                            6,
                                        ),
                                        "retry_results": retry_results,
                                        "recovered_service_names": list(recovered_stage_services),
                                        "remaining_missing_service_names": list(retry_missing_services),
                                    }
                                    if not retry_failed and not retry_missing_services:
                                        stage_result["timed_out"] = False
                                        stage_result["recovered_after_timeout"] = True
                                        stage_result["recovery_mode"] = "single_service_retry"
                                        events.append(
                                            {
                                                "timestamp_utc": _timestamp_utc(),
                                                "event_type": "compose_up_stage_retry_completed",
                                                "stage_id": stage_id,
                                                "recovered": True,
                                                "retry_service_count": len(retry_stage_services),
                                                "recovery_timeout_seconds": recovery_timeout_seconds,
                                            }
                                        )
                                        continue
                                phase_timing["compose_up_wall_seconds"] = round(
                                    time.perf_counter() - compose_up_started_at,
                                    6,
                                )
                                runtime_blocker = {
                                    "stage": "docker_compose_up",
                                    "classified_blocker": "docker_compose_up_timeout",
                                    "timeout_seconds": float(args.compose_up_timeout_seconds),
                                    "stage_timeout_seconds": round(stage_timeout_seconds, 6),
                                    "wall_seconds": phase_timing["compose_up_wall_seconds"],
                                    "stdout": compose_up_result.get("stdout"),
                                    "stderr": compose_up_result.get("stderr"),
                                    "compose_up_stage": stage_id,
                                    "compose_service_snapshot": compose_service_snapshot,
                                    "running_stage_service_names": list(running_stage_services),
                                    "retry_service_names": list(retry_stage_services),
                                    "retry_results": retry_results,
                                }
                                raise RuntimeError("docker-compose up timed out")
                            compose_up = completed
                            if compose_up is None:
                                runtime_blocker = {
                                    "stage": "docker_compose_up",
                                    "classified_blocker": "docker_compose_up_unknown_failure",
                                    "compose_up_stage": stage_id,
                                }
                                raise RuntimeError("docker-compose up failed")
                            if compose_up.returncode != 0:
                                classified = _classify_compose_failure(
                                    compose_up.stdout or "",
                                    compose_up.stderr or "",
                                )
                                compose_service_snapshot = _compose_service_state_snapshot(
                                    output_dir=output_dir,
                                    container_names=list(
                                        compose_metadata.get("container_names", [])
                                    ),
                                )
                                runtime_blocker = {
                                    "stage": "docker_compose_up",
                                    "returncode": compose_up.returncode,
                                    "stdout": compose_up.stdout,
                                    "stderr": compose_up.stderr,
                                    "compose_up_stage": stage_id,
                                    "compose_service_snapshot": compose_service_snapshot,
                                    **classified,
                                }
                                compose_up = None
                                raise RuntimeError("docker-compose up failed")
                        phase_timing["compose_up_wall_seconds"] = round(
                            time.perf_counter() - compose_up_started_at,
                            6,
                        )
                        events.append(
                            {
                                "timestamp_utc": _timestamp_utc(),
                                "event_type": "compose_up_completed",
                                "output_dir": str(output_dir),
                                "wall_seconds": phase_timing["compose_up_wall_seconds"],
                            }
                        )
                        _progress_update(
                            phase="container_readiness",
                            event_type="compose_up_completed",
                            phase_detail={
                                "compose_up_wall_seconds": phase_timing["compose_up_wall_seconds"],
                            },
                        )
                        compose_up_stage_results_summary = list(compose_up_stage_results)
                        container_readiness = _wait_for_compose_containers(
                            container_names=list(compose_metadata.get("container_names", [])),
                            timeout_seconds=float(args.container_readiness_timeout_seconds),
                        )
                        phase_timing["container_readiness_wall_seconds"] = round(
                            float(container_readiness.get("wall_seconds", 0.0)),
                            6,
                        )
                        events.append(
                            {
                                "timestamp_utc": _timestamp_utc(),
                                "event_type": "container_readiness_completed",
                                "ready": container_readiness.get("ready"),
                                "wall_seconds": phase_timing["container_readiness_wall_seconds"],
                                "required_container_count": container_readiness.get(
                                    "required_container_count"
                                ),
                            }
                        )
                        _progress_update(
                            phase="container_readiness",
                            event_type="container_readiness_completed",
                            phase_detail={
                                "ready": container_readiness.get("ready"),
                                "wall_seconds": phase_timing["container_readiness_wall_seconds"],
                                "required_container_count": container_readiness.get(
                                    "required_container_count"
                                ),
                            },
                        )
                    else:
                        phase_timing["compose_up_wall_seconds"] = 0.0
                        phase_timing["container_readiness_wall_seconds"] = 0.0
                        events.append(
                            {
                                "timestamp_utc": _timestamp_utc(),
                                "event_type": "container_readiness_reused",
                                "ready": container_readiness.get("ready"),
                                "required_container_count": container_readiness.get(
                                    "required_container_count"
                                ),
                            }
                        )
                        _progress_update(
                            phase="container_readiness",
                            event_type="container_readiness_reused",
                            phase_detail={
                                "ready": container_readiness.get("ready"),
                                "required_container_count": container_readiness.get(
                                    "required_container_count"
                                ),
                            },
                        )
                    if container_readiness.get("ready") is not True:
                        compose_service_snapshot = _compose_service_state_snapshot(
                            output_dir=output_dir,
                            container_names=list(compose_metadata.get("container_names", [])),
                        )
                        runtime_blocker = {
                            "stage": "container_readiness",
                            "classified_blocker": "container_readiness_timeout",
                            "container_readiness": container_readiness,
                            "compose_service_snapshot": compose_service_snapshot,
                        }
                        raise RuntimeError("container readiness failed")
                    if bool(args.stop_after_container_readiness):
                        events.append(
                            {
                                "timestamp_utc": _timestamp_utc(),
                                "event_type": "stopped_after_container_readiness",
                            }
                        )
                        _progress_update(
                            phase="stopped_after_container_readiness",
                            event_type="stopped_after_container_readiness",
                        )
                        runtime_report = {
                            "mode": "compose_startup_only",
                            "initial_runtime_readiness": None,
                            "iterations": [],
                            "timing_summary": {},
                            "final_cleanup": {"all_cleanup_success": True},
                            "temporal_audit": {
                                "no_future_active_link_violation": True,
                                "no_future_forwarding_state_violation": True,
                                "parameter_epoch_without_topology_mutation": None,
                                "runtime_epoch_has_expected_mutation_scope": None,
                                "rollback_completed": True,
                                "queue_exhausted_or_stopped_by_bound": "stopped_after_container_readiness",
                                "horizon_replenishment_status": {
                                    "mode": "not_started",
                                    "status": "not_started",
                                },
                            },
                            "prepared_bundle_runtime": None,
                            "outcome": {
                                "real_apply_success": None,
                                "ping_success": None,
                                "rollback_completed": True,
                                "failure_reason": None,
                            },
                        }
                        compose_service_snapshot = _compose_service_state_snapshot(
                            output_dir=output_dir,
                            container_names=list(compose_metadata.get("container_names", [])),
                        )
                    else:
                        watch_targets = list(
                            execution_snapshots[0].planner_hints.get("internet_validation_targets", [])
                        ) if execution_snapshots else []
                        if args.ping_watch:
                            _wait_for_named_containers(
                                [
                                    watch["ut_id"]
                                    for watch in _parse_ping_watch_selectors(
                                        args.ping_watch,
                                        validation_targets=watch_targets,
                                    )
                                ]
                            )
                            parsed_watches = _parse_ping_watch_selectors(
                                args.ping_watch,
                                validation_targets=watch_targets,
                            )
                            watcher_summary["enabled"] = True
                            for watch in parsed_watches:
                                label = (
                                    f"{watch['ut_id']}_to_"
                                    f"{watch['target'].replace(':', '_').replace('/', '_')}"
                                )
                                watcher_log = run_dir / f"watcher_{label}.log"
                                process = _start_ping_watcher(
                                    ut_id=watch["ut_id"],
                                    target_ip=watch["target_ip"],
                                    label=label,
                                    outfile=watcher_log,
                                )
                                watcher_processes.append((watch, process, watcher_log))
                                events.append(
                                    {
                                        "timestamp_utc": _timestamp_utc(),
                                        "event_type": "ping_watcher_started",
                                        "ut_id": watch["ut_id"],
                                        "target": watch["target"],
                                        "target_ip": watch["target_ip"],
                                        "log_path": str(watcher_log),
                                    }
                                )
                        events.append(
                            {
                                "timestamp_utc": _timestamp_utc(),
                                "event_type": "runtime_started",
                                "selected_runtime_slice_ids": list(selected_runtime_slice_ids),
                                "selected_parameter_slice_ids": list(selected_parameter_slice_ids),
                                "continuous": bool(args.continuous),
                            }
                        )
                        _progress_update(
                            phase="runtime_started",
                            event_type="runtime_started",
                            phase_detail={
                                "continuous": bool(args.continuous),
                                "selected_runtime_epoch_count": len(selected_runtime_slice_ids),
                                "selected_parameter_epoch_count": len(selected_parameter_slice_ids),
                            },
                        )
                        initial_segment = _preview_summary_to_segment(
                            segment_id="initial",
                            preview_summary=preview_summary,
                            temporal_artifacts=temporal_artifacts,
                            topology_path=topology_path,
                            link_state_path=link_state_path,
                            run_start_time_utc=(
                                list(temporal_plan.get("orbit_samples", []))[0].get(
                                    "absolute_time_utc"
                                )
                                if list(temporal_plan.get("orbit_samples", []))
                                else None
                            ),
                            max_epochs=args.max_epochs,
                            max_runtime_epochs=args.max_runtime_epochs,
                            max_parameter_epochs=args.max_parameter_epochs,
                        )
                        runtime_report = _run_live_temporal_runtime(
                            args=args,
                            run_dir=run_dir,
                            topology_path=topology_path,
                            link_state_path=link_state_path,
                            initial_segment=initial_segment,
                            validation_targets=watch_targets,
                            observe_selectors=list(args.observe_flow),
                            stream_progress=bool(args.stream_progress),
                            events=events,
                            progress_update=_progress_update,
                            phase_timing=phase_timing,
                        )
                        events.append(
                            {
                                "timestamp_utc": _timestamp_utc(),
                                "event_type": "runtime_completed",
                                "real_apply_success": runtime_report.get("outcome", {}).get(
                                    "real_apply_success"
                                ),
                                "ping_success": runtime_report.get("outcome", {}).get(
                                    "ping_success"
                                ),
                                "rollback_completed": runtime_report.get("outcome", {}).get(
                                    "rollback_completed"
                                ),
                            }
                        )
                        _progress_update(
                            phase="runtime_completed",
                            event_type="runtime_completed",
                            phase_detail={
                                "real_apply_success": runtime_report.get("outcome", {}).get(
                                    "real_apply_success"
                                ),
                                "ping_success": runtime_report.get("outcome", {}).get(
                                    "ping_success"
                                ),
                                "rollback_completed": runtime_report.get("outcome", {}).get(
                                    "rollback_completed"
                                ),
                            },
                        )
                        phase_timing["initial_runtime_readiness_wall_seconds"] = round(
                            float(
                                runtime_report.get("initial_runtime_readiness", {}).get(
                                    "wall_seconds",
                                    0.0,
                                )
                            ),
                            6,
                        )
                        prewarm = dict(
                            runtime_report.get("prepared_bundle_runtime", {}).get(
                                "pre_epoch_host_prewarm",
                                {},
                            )
                        )
                        prewarm_timing = dict(prewarm.get("manager_timing_breakdown", {}))
                        phase_timing["pre_epoch_host_runtime_prewarm_wall_seconds"] = round(
                            float(prewarm.get("prewarm_wall_seconds", 0.0)),
                            6,
                        )
                        phase_timing["pre_epoch_host_runtime_prepare_wall_seconds"] = round(
                            float(prewarm_timing.get("runtime_prepare_wall_seconds", 0.0)),
                            6,
                        )
                        phase_timing["pre_epoch_host_runtime_reconcile_wall_seconds"] = round(
                            float(prewarm_timing.get("runtime_reconcile_wall_seconds", 0.0)),
                            6,
                        )
                        first_iteration = (
                            list(runtime_report.get("iterations", []))[0]
                            if list(runtime_report.get("iterations", []))
                            else None
                        )
                        if first_iteration is not None:
                            first_timing = dict(first_iteration.get("timing", {}))
                            first_manager = dict(
                                first_iteration.get("host_mutation_execution", {})
                                .get("outcome", {})
                                .get("manager_timing_breakdown", {})
                            )
                            phase_timing["first_runtime_epoch_transition_wall_seconds"] = round(
                                float(first_timing.get("transition_wall_seconds", 0.0)),
                                6,
                            )
                            phase_timing["first_runtime_epoch_network_mutation_wall_seconds"] = round(
                                float(
                                    first_timing.get("network_mutation_plan_wall_seconds", 0.0)
                                ),
                                6,
                            )
                            phase_timing["first_runtime_epoch_node_local_apply_wall_seconds"] = round(
                                float(first_timing.get("node_local_apply_wall_seconds", 0.0)),
                                6,
                            )
                            phase_timing[
                                "first_runtime_epoch_direct_backend_prepare_wall_seconds"
                            ] = round(
                                float(first_manager.get("runtime_prepare_wall_seconds", 0.0)),
                                6,
                            )
                            phase_timing[
                                "first_runtime_epoch_direct_backend_reconcile_wall_seconds"
                            ] = round(
                                float(first_manager.get("runtime_reconcile_wall_seconds", 0.0)),
                                6,
                            )
                except subprocess.CalledProcessError as exc:
                    classified = _classify_compose_failure(exc.stdout or "", exc.stderr or "")
                    runtime_blocker = {
                        "stage": "docker_compose_up",
                        "returncode": exc.returncode,
                        "stdout": exc.stdout,
                        "stderr": exc.stderr,
                        **classified,
                    }
                except RuntimeError as exc:
                    if runtime_blocker is None:
                        runtime_blocker = {
                            "stage": "runtime_bringup_or_execution",
                            "classified_blocker": "runtime_bringup_or_execution_failed",
                            "error": str(exc),
                        }
                except KeyboardInterrupt:
                    if runtime_blocker is None:
                        runtime_blocker = {
                            "stage": "runtime_bringup_or_execution",
                            "classified_blocker": "runtime_interrupted_by_signal",
                            "error": "KeyboardInterrupt",
                        }
                    force_compose_down_after = not bool(args.keep_environment)
                    events.append(
                        {
                            "timestamp_utc": _timestamp_utc(),
                            "event_type": "runtime_interrupted",
                            "classified_blocker": "runtime_interrupted_by_signal",
                        }
                    )
            elif runtime_blocker is None:
                runtime_blocker = {
                    "stage": "preview_gate",
                    "classified_blocker": "preview_invalid_or_realization_unsupported",
                    "preview_gate": preview_gate,
                }
        finally:
            for watch, process, watcher_log in watcher_processes:
                _stop_ping_watcher(process)
                watcher_summary["watchers"][f"{watch['ut_id']}:{watch['target']}"] = {
                    "ut_id": watch["ut_id"],
                    "target": watch["target"],
                    "target_ip": watch["target_ip"],
                    "log_path": str(watcher_log),
                    "summary": _summarize_watcher(watcher_log),
                }
                events.append(
                    {
                        "timestamp_utc": _timestamp_utc(),
                        "event_type": "ping_watcher_stopped",
                        "ut_id": watch["ut_id"],
                        "target": watch["target"],
                        "log_path": str(watcher_log),
                    }
                )
            if not bool(args.keep_environment) or force_compose_down_after:
                compose_down_after = _compose_down(output_dir)
            else:
                compose_down_after = None
            if runtime_lock_active and runtime_lock_path.exists():
                runtime_lock_path.unlink()
                runtime_lock_active = False
                events.append(
                    {
                        "timestamp_utc": _timestamp_utc(),
                        "event_type": "runtime_lock_removed",
                        "lock_path": str(runtime_lock_path),
                    }
                )
            post_run_preflight_started_at = time.perf_counter()
            post_run_preflight_report = run_skyseed_preflight(
                ignore_pids=_current_preflight_ignore_pids()
            )
            phase_timing["post_run_preflight_wall_seconds"] = round(
                time.perf_counter() - post_run_preflight_started_at,
                6,
            )
            post_run_expected_clean = not bool(args.keep_environment)
            post_run_residue = _compose_residue_summary(post_run_preflight_report)
            post_run_preflight_report["expected_clean_after_run"] = post_run_expected_clean
            post_run_preflight_report["compose_residue_summary"] = post_run_residue
            post_run_preflight_report["clean_after_run"] = (
                all(int(value) == 0 for value in post_run_residue.values())
                if post_run_expected_clean
                else None
            )
            post_run_preflight_report["wall_seconds"] = phase_timing[
                "post_run_preflight_wall_seconds"
            ]
            _write_json(run_dir / "post_run_preflight.json", post_run_preflight_report)
            events.append(
                {
                    "timestamp_utc": _timestamp_utc(),
                    "event_type": "post_run_preflight_completed",
                    "ready": post_run_preflight_report.get("assessment", {}).get("ready"),
                    "expected_clean_after_run": post_run_expected_clean,
                    "clean_after_run": post_run_preflight_report.get("clean_after_run"),
                    "compose_residue_summary": post_run_residue,
                }
            )
            _write_json(
                run_dir / "docker_compose.json",
                {
                    "output_dir": str(output_dir),
                    "output_digest": output_digest,
                    "compose_metadata": compose_metadata,
                    "build_reuse_decision": build_reuse_summary,
                    "compose_down_before": (
                        None
                        if compose_down_before is None
                        else {
                            "returncode": compose_down_before.returncode,
                            "stdout": compose_down_before.stdout,
                            "stderr": compose_down_before.stderr,
                        }
                    ),
                    "compose_network_scrub_before": compose_network_scrub_before,
                    "compose_build": (
                        None
                        if compose_build is None
                        else {
                            "returncode": compose_build.returncode,
                            "stdout": compose_build.stdout,
                            "stderr": compose_build.stderr,
                        }
                    ),
                    "compose_up": (
                        None
                        if compose_up is None
                        else {
                            "stdout": compose_up.stdout,
                            "stderr": compose_up.stderr,
                        }
                    ),
                    "compose_up_stage_results": compose_up_stage_results,
                    "container_readiness": container_readiness,
                    "compose_service_snapshot": compose_service_snapshot,
                    "compose_down_after": (
                        None
                        if compose_down_after is None
                        else {
                            "returncode": compose_down_after.returncode,
                            "stdout": compose_down_after.stdout,
                            "stderr": compose_down_after.stderr,
                        }
                    ),
                    "post_run_preflight": post_run_preflight_report,
                },
            )

    if runtime_report is not None:
        _write_json(run_dir / "real_runtime_report.json", runtime_report)
        if runtime_report.get("temporal_audit") is not None:
            _write_json(
                run_dir / "temporal_audit.json",
                dict(runtime_report.get("temporal_audit", {})),
            )
        if runtime_report.get("prepared_bundle_runtime") is not None:
            prepared_bundle_path = run_dir / "prepared_update_bundle.json"
            prepared_bundle = _read_json(prepared_bundle_path)
            prepared_bundle["continuous_runtime_execution"] = dict(
                runtime_report.get("prepared_bundle_runtime", {})
            )
            _write_json(prepared_bundle_path, prepared_bundle)
            _write_json(
                run_dir / "epoch_execution_queue.json",
                list(
                    runtime_report.get("prepared_bundle_runtime", {}).get(
                        "epoch_execution_queue",
                        [],
                    )
                ),
            )
    final_timing_report = _runtime_report_for_final_timing(run_dir, runtime_report)
    if final_timing_report is not None:
        timing_rows = _timing_rows(final_timing_report)
        timing_summary = dict(final_timing_report.get("timing_summary", {}))
    else:
        timing_rows = []
        timing_summary = {}
    timing_analysis = _timing_analysis(
        phase_timing=phase_timing,
        timing_rows=timing_rows,
    )
    continuous_operation_summary = _continuous_operation_summary(
        temporal_plan=temporal_plan,
        all_sample_frames=all_sample_frames,
        selected_epoch_queue=selected_epoch_queue,
        selected_runtime_slice_ids=selected_runtime_slice_ids,
        selected_parameter_slice_ids=selected_parameter_slice_ids,
    )
    if runtime_report is not None and runtime_report.get("prepared_bundle_runtime") is not None:
        continuous_operation_summary.update(
            {
                "rolling_horizon_replenishment": runtime_report.get("temporal_audit", {})
                .get("horizon_replenishment_status", {})
                .get("status", "live_unknown"),
                "prepared_epoch_queue_count": len(
                    list(
                        runtime_report.get("prepared_bundle_runtime", {}).get(
                            "epoch_execution_queue",
                            [],
                        )
                    )
                ),
                "prepared_runtime_epoch_count": int(
                    runtime_report.get("timing_summary", {})
                    .get("runtime_epoch_count", 0)
                ),
                "prepared_parameter_epoch_count": int(
                    runtime_report.get("timing_summary", {})
                    .get("parameter_epoch_count", 0)
                ),
                "real_runtime_execution_model": (
                    "runtime_epochs_execute_live; parameter_epochs_execute_live; "
                    "rolling_horizon_replenishment_live"
                ),
                "replenishment_segment_count": len(
                    list(
                        runtime_report.get("prepared_bundle_runtime", {}).get(
                            "replenishment_events",
                            [],
                        )
                    )
                ),
            }
        )
    _write_json(
        run_dir / "timing.json",
        {
            "finalized": True,
            "phase_timing": phase_timing,
            "timing_analysis": timing_analysis,
            "timing_summary": timing_summary,
            "source_runtime_report": (
                "memory"
                if runtime_report is not None
                else "live_checkpoint"
                if final_timing_report is not None
                else None
            ),
            "rows": timing_rows,
        },
    )
    _write_timing_csv(run_dir / "timing.csv", timing_rows)

    if runtime_report is not None:
        _write_json(
            run_dir / "ping_results.json",
            {
                "initial_runtime_readiness": dict(
                    runtime_report.get("initial_runtime_readiness") or {}
                ),
                "post_transition_connectivity": [
                    {
                        "sequence_index": iteration.get("sequence_index"),
                        "to_slice_id": iteration.get("to_slice_id"),
                        "pre_transition_connectivity": iteration.get(
                            "pre_transition_connectivity"
                        ),
                        "post_transition_connectivity": iteration.get(
                            "post_transition_connectivity"
                        ),
                    }
                    for iteration in list(runtime_report.get("iterations", []))
                ],
                "watchers": dict(watcher_summary.get("watchers", {})),
            },
        )
        _write_json(
            run_dir / "path_observation_results.json",
            {
                "observe_selectors": list(args.observe_flow),
                "iterations": [
                    {
                        "sequence_index": iteration.get("sequence_index"),
                        "to_slice_id": iteration.get("to_slice_id"),
                        "packet_observation": iteration.get("packet_observation"),
                        "post_transition_paths": iteration.get("post_transition_paths"),
                    }
                    for iteration in list(runtime_report.get("iterations", []))
                ],
            },
        )
        for iteration in list(runtime_report.get("iterations", [])):
            events.append(
                {
                    "timestamp_utc": _timestamp_utc(),
                    "event_type": "epoch_result_exported",
                    "epoch_type": iteration.get("epoch_type", "runtime_epoch"),
                    "sequence_index": iteration.get("sequence_index"),
                    "to_slice_id": iteration.get("to_slice_id"),
                    "transition_wall_seconds": iteration.get("timing", {}).get(
                        "transition_wall_seconds"
                    ),
                    "network_mutation_plan_wall_seconds": iteration.get("timing", {}).get(
                        "network_mutation_plan_wall_seconds"
                    ),
                    "node_local_apply_wall_seconds": iteration.get("timing", {}).get(
                        "node_local_apply_wall_seconds"
                    ),
                    "transition_success": iteration.get("outcome", {}).get(
                        "transition_success"
                    ),
                }
            )

    selected_ut_ids = [
        str(entry.get("ut_id"))
        for entry in list(shell_manifest.get("ground_model", {}).get("selected_uts", []))
        if entry.get("ut_id")
    ]
    selected_gateway_ids = [
        str(entry.get("gateway_id"))
        for entry in list(shell_manifest.get("ground_model", {}).get("effective_gateways", []))
        if entry.get("gateway_id")
    ]
    validation_targets = list(
        execution_snapshots[0].planner_hints.get("internet_validation_targets", [])
    ) if execution_snapshots else []
    selected_target_ips = [
        str(target.get("ip_address"))
        for target in validation_targets
        if target.get("ip_address")
    ]
    example_satellite_ids = []
    if list(planner_preview.get("snapshots", [])):
        first_runtime_snapshot = next(
            (
                record
                for record in list(planner_preview.get("snapshots", []))
                if str(record.get("slice_id")) in set(selected_runtime_slice_ids)
            ),
            dict(planner_preview.get("snapshots", [])[0]),
        )
        example_satellite_ids = sorted(
            {
                str(sat_id)
                for sat_id in dict(
                    first_runtime_snapshot.get("active_serving_satellite_ids", {})
                ).values()
                if sat_id
            }
        )
    commands_md = _manual_commands_markdown(
        run_id=run_id,
        run_dir=run_dir,
        output_dir=output_dir,
        topology_path=topology_path,
        selected_ut_ids=selected_ut_ids,
        selected_gateway_ids=selected_gateway_ids,
        selected_target_ips=selected_target_ips,
        example_satellite_ids=example_satellite_ids,
        observe_selectors=list(args.observe_flow),
        keep_environment=bool(args.keep_environment),
    )
    (run_dir / "commands.md").write_text(commands_md, encoding="utf-8")

    generated_artifacts = {
        "run_manifest_json": str(run_dir / "run_manifest.json"),
        "host_prepare_json": str(run_dir / "host_prepare.json"),
        "preflight_json": str(run_dir / "preflight.json"),
        "post_run_preflight_json": str(run_dir / "post_run_preflight.json"),
        "shell_manifest_json": str(run_dir / "shell_manifest.json"),
        "ground_manifest_json": str(run_dir / "ground_manifest.json"),
        "mini_internet_summary_json": str(run_dir / "mini_internet_summary.json"),
        "observation_frame_summary_json": str(run_dir / "observation_frame_summary.json"),
        "return_path_audit_json": str(run_dir / "return_path_audit.json"),
        "epoch_execution_queue_json": str(run_dir / "epoch_execution_queue.json"),
        "temporal_plan_json": str(run_dir / "temporal_plan.json"),
        "resource_budget_json": str(run_dir / "resource_budget.json"),
        "runtime_epoch_summary_json": str(run_dir / "runtime_epoch_summary.json"),
        "parameter_epoch_summary_json": str(run_dir / "parameter_epoch_summary.json"),
        "network_mutation_plan_summary_json": str(
            run_dir / "network_mutation_plan_summary.json"
        ),
        "runtime_update_plan_summary_json": str(
            run_dir / "runtime_update_plan_summary.json"
        ),
        "temporal_audit_json": str(run_dir / "temporal_audit.json"),
        "real_runtime_report_json": str(run_dir / "real_runtime_report.json"),
        "epoch_execution_details_jsonl": str(run_dir / "epoch_execution_details.jsonl"),
        "ping_results_json": str(run_dir / "ping_results.json"),
        "path_observation_results_json": str(run_dir / "path_observation_results.json"),
        "timing_json": str(run_dir / "timing.json"),
        "timing_csv": str(run_dir / "timing.csv"),
        "events_jsonl": str(run_dir / "events.jsonl"),
        "commands_md": str(run_dir / "commands.md"),
        "prepared_update_bundle_json": str(run_dir / "prepared_update_bundle.json"),
        "zone_model_json": str(run_dir / "zone_model.json"),
        "zone_anchor_schedule_json": str(run_dir / "zone_anchor_schedule.json"),
        "gz_fec_plan_json": str(run_dir / "gz_fec_plan.json"),
        "gz_update_diff_summary_json": str(run_dir / "gz_update_diff_summary.json"),
        "zone_anchor_quality_summary_json": str(
            run_dir / "zone_anchor_quality_summary.json"
        ),
        "zone_availability_analysis_json": str(
            run_dir / "zone_availability_analysis.json"
        ),
        "docker_compose_json": str(run_dir / "docker_compose.json"),
        "compose_build_reuse_json": str(run_dir / "compose_build_reuse.json"),
        "compose_audit_json": str(run_dir / "compose_audit.json"),
        "runtime_progress_json": str(run_dir / "runtime_progress.json"),
        "runtime_lock_metadata_json": str(run_dir / "runtime_lock_metadata.json"),
    }
    if startup_strategy is not None:
        startup_strategy = {
            **dict(startup_strategy),
            "reuse_precheck": compose_environment_reuse_summary,
            "compose_stage_count": len(compose_up_stage_results_summary),
            "compose_stage_results": compose_up_stage_results_summary,
        }
    summary = _experiment_summary_payload(
        args=args,
        run_id=run_id,
        run_dir=run_dir,
        preview_summary=preview_summary,
        temporal_payload=temporal_plan,
        selected_runtime_slice_ids=selected_runtime_slice_ids,
        selected_parameter_slice_ids=selected_parameter_slice_ids,
        preview_gate=preview_gate,
        runtime_report=runtime_report,
        runtime_blocker=runtime_blocker,
        watcher_summary=watcher_summary,
        phase_timing=phase_timing,
        timing_analysis=timing_analysis,
        compose_audit=compose_audit,
        startup_strategy=startup_strategy,
        mini_internet_summary=mini_internet_summary,
        continuous_operation_summary=continuous_operation_summary,
        data_provenance=data_provenance,
        artifacts=generated_artifacts,
        preflight_report=preflight_report,
        host_prepare_report=host_prepare_report,
        post_run_preflight_report=post_run_preflight_report,
    )
    _write_json(run_dir / "run_manifest.json", summary)
    events.append(
        {
            "timestamp_utc": _timestamp_utc(),
            "event_type": "run_completed",
            "run_id": run_id,
            "run_mode": args.run_mode,
            "runtime_succeeded": (
                runtime_report is not None
                and runtime_report.get("outcome", {}).get("real_apply_success") is True
                and runtime_report.get("outcome", {}).get("ping_success") is True
            ),
            "post_run_clean": None if summary.get("post_run_clean") is None else bool(summary.get("post_run_clean")),
            "runtime_blocker": None if runtime_blocker is None else runtime_blocker.get("classified_blocker"),
        }
    )
    _write_events_jsonl(run_dir / "events.jsonl", events)
    _progress_update(
        phase="completed",
        event_type="run_completed",
        phase_detail={
            "real_apply_success": summary.get("real_apply_success"),
            "ping_success": summary.get("ping_success"),
            "rollback_completed": summary.get("rollback_completed"),
        },
        write_events=False,
    )
    progress_stop_event.set()
    progress_thread.join(timeout=2.0)

    if args.format == "json":
        print(json.dumps(_json_safe(summary), indent=2, sort_keys=True))
        return 0

    print("Starlink shell experiment complete.")
    print(f"Run id: {run_id}")
    print(f"Run dir: {run_dir}")
    print(
        "Temporal plan: "
        f"{len(list(temporal_plan.get('orbit_samples', [])))} samples -> "
        f"{len(list(temporal_plan.get('runtime_epochs', [])))} RuntimeEpochs, "
        f"{len(list(temporal_plan.get('parameter_epochs', [])))} ParameterEpochs"
    )
    print(
        "Selected runtime epochs: "
        + ", ".join(selected_runtime_slice_ids)
        if selected_runtime_slice_ids
        else "Selected runtime epochs: none"
    )
    print(
        "Preview gate: "
        f"preview_valid={_bool_text(bool(preview_gate['all_preview_valid']))} "
        f"real_supported={_bool_text(bool(preview_gate['all_real_supported']))}"
    )
    if runtime_report is not None:
        outcome = dict(runtime_report.get("outcome", {}))
        print(
            "Runtime outcome: "
            f"real_apply_success={_bool_text(bool(outcome.get('real_apply_success')))} "
            f"ping_success={_bool_text(bool(outcome.get('ping_success')))} "
            f"rollback_completed={_bool_text(bool(outcome.get('rollback_completed')))}"
        )
    elif runtime_blocker is not None:
        print(
            "Runtime blocked: "
            f"{runtime_blocker.get('classified_blocker') or runtime_blocker.get('stage')}"
        )
    else:
        print("Runtime not requested.")
    print(f"Commands: {run_dir / 'commands.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
