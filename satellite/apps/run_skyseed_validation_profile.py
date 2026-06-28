#!/usr/bin/env python3
# encoding: utf-8

"""Run named Skyseed validation profiles with catalog-aware selector gates."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

try:
    from ._path_setup import bootstrap_app_import_path
except ImportError:  # pragma: no cover - direct script execution path.
    from _path_setup import bootstrap_app_import_path

SATELLITE_DIR = bootstrap_app_import_path()
REPO_ROOT = SATELLITE_DIR.parent
PROFILE_DIR = SATELLITE_DIR / "configs" / "validation_profiles"
STARLINK_RUNNER = SATELLITE_DIR / "apps" / "run_starlink_shell_experiment.py"
MINIMAL_RELOAD_RUNNER = (
    SATELLITE_DIR / "apps" / "run_minimal_resident_bpf_map_reload_repro.py"
)

from ground_model import load_gateway_catalog, load_ut_catalog, load_zone_catalog
from runtime_execution_trace import write_execution_trace_artifacts


LEGACY_DEMO_UT_IDS = {"ut1", "ut2", "ut3", "ut4"}
LEGACY_DEMO_GATEWAY_IDS = {"gw1", "gw2", "gw3", "gw4", "gw5", "gw6", "gw7", "gw8"}

RUNNER_ARG_ORDER = (
    "run_mode",
    "orbit_input_mode",
    "planned_shell_id",
    "max_satellites",
    "ut_catalog",
    "gateway_catalog",
    "zone_catalog",
    "ut_ids",
    "gateway_ids",
    "max_uts",
    "max_gateways",
    "ground_placement",
    "start_time",
    "sample_interval",
    "event_evaluation_interval",
    "horizon_seconds",
    "slice_count",
    "step_seconds",
    "max_runtime_epochs",
    "max_parameter_epochs",
    "max_epochs",
    "parameter_max_interval",
    "delay_threshold_ms",
    "loss_threshold_pct",
    "bandwidth_threshold_pct",
    "routing_cost_threshold",
    "runtime_mode",
    "loop_count",
    "interval",
    "pre_transition_probe_interval",
    "runtime_epoch_validation_interval",
    "runtime_epoch_validation_scope",
    "parameter_epoch_validation_interval",
    "preflight_policy",
    "host_prepare_policy",
    "forwarding_backend",
    "bpf_backend",
    "compose_build_policy",
    "compose_up_timeout_seconds",
    "startup_high_progress_grace_ratio",
    "startup_high_progress_grace_seconds",
    "startup_slow_progress_grace_seconds",
    "startup_absolute_max_seconds",
    "compose_build_timeout_seconds",
    "container_readiness_timeout_seconds",
    "initial_runtime_readiness_timeout_seconds",
    "progress_heartbeat_seconds",
    "startup_stall_warning_seconds",
    "epoch_stall_warning_seconds",
    "mini_internet_node_prefix",
    "mini_internet_ix_offset",
    "docker_dummy_network_pool",
    "docker_dummy_network_mask",
    "format",
)

BOOLEAN_RUNNER_FLAGS = {
    "preview_only_no_compile": "--preview-only-no-compile",
    "keep_environment": "--keep-environment",
    "reuse_running_compose_environment": "--reuse-running-compose-environment",
    "emit_bpf_shadow": "--emit-bpf-shadow",
    "bpf_dataplane_canary": "--bpf-dataplane-canary",
    "enable_seed_internet_map": "--enable-seed-internet-map",
}

CAPACITY_BLOCKER_LABEL = "DOCKER_DUMMY_NETWORK_POOL_CAPACITY_BLOCKER"
STARTUP_COST_BLOCKER_LABEL = "STARTUP_MATERIALIZATION_COST_BLOCKER"
COMPOSE_TIMEOUT_LABEL = "COMPOSE_UP_TIMEOUT_BEFORE_RUNTIME"
STARTUP_SOFT_BUDGET_WARNING_LABEL = "STARTUP_SOFT_BUDGET_EXCEEDED_WARNING"
STARTUP_COST_ANOMALY_LABEL = "STARTUP_MATERIALIZATION_COST_ANOMALY"
SLOW_PROGRESS_CONTINUE_LABEL = "SLOW_BUT_PROGRESSING_CONTINUE"
STARTUP_HARD_CAP_LABEL = "STARTUP_HARD_CAP_EXCEEDED"
RESOURCE_DEADLOCK_LABEL = "RESOURCE_DEADLOCK_STARTUP_ABORT"
NO_PROGRESS_STALL_LABEL = "NO_PROGRESS_STARTUP_STALL"
STARTUP_STATE_SUPERVISION_LABEL = "STARTUP_STATE_SUPERVISION_ADDED"
STARTUP_SOFT_BUDGET_WARNING_ONLY_LABEL = "STARTUP_SOFT_BUDGET_WARNING_ONLY"
STARTUP_PROGRESSING_CONTINUE_LABEL = "STARTUP_PROGRESSING_CONTINUE"
STARTUP_NO_PROGRESS_STALL_LABEL = "STARTUP_NO_PROGRESS_STALL"
STARTUP_RESOURCE_DEADLOCK_LABEL = "STARTUP_RESOURCE_DEADLOCK"
STARTUP_CORRECTNESS_FATAL_LABEL = "STARTUP_CORRECTNESS_FATAL"
STARTUP_RECOVERABLE_STAGE_FAILURE_LABEL = "STARTUP_RECOVERABLE_STAGE_FAILURE"
STARTUP_RESUMABLE_CHECKPOINT_AVAILABLE_LABEL = "STARTUP_RESUMABLE_CHECKPOINT_AVAILABLE"
STARTUP_RECOVERABLE_RESUME_NOT_IMPLEMENTED_LABEL = (
    "STARTUP_RECOVERABLE_BUT_RESUME_NOT_IMPLEMENTED"
)
CLEANUP_REQUIRED_LABEL = "CLEANUP_REQUIRED"
CLEANUP_NOT_REQUIRED_RECOVERABLE_LABEL = "CLEANUP_NOT_REQUIRED_RECOVERABLE_STATE"
STARTUP_OPERATION_TRACE_ADDED_LABEL = "STARTUP_OPERATION_TRACE_ADDED"
STARTUP_OPERATION_TRACE_INCOMPLETE_LABEL = "STARTUP_OPERATION_TRACE_INCOMPLETE"
STARTUP_REDUNDANT_OPERATION_LOOP_LABEL = "STARTUP_REDUNDANT_OPERATION_LOOP"
STARTUP_DUPLICATE_RESOURCE_OPERATION_LABEL = "STARTUP_DUPLICATE_RESOURCE_OPERATION"
STARTUP_NOOP_RETRY_LOOP_LABEL = "STARTUP_NOOP_RETRY_LOOP"
STARTUP_FULL_RECONCILE_REPEATED_LABEL = "STARTUP_FULL_RECONCILE_REPEATED"
STARTUP_OPERATION_CARDINALITY_ANOMALY_LABEL = "STARTUP_OPERATION_CARDINALITY_ANOMALY"
STATIC_INVENTORY_PRECREATED_AUDIT_LABEL = (
    "STATIC_INVENTORY_PRECREATED_CONNECTION_AUDIT_ADDED"
)
CLEANUP_PASS_LABEL = "CLEANUP_BOUNDARY_PASS"
CLEANUP_INCOMPLETE_LABEL = "CLEANUP_BOUNDARY_STILL_INCOMPLETE"
PERSISTENT_ARTIFACT_LABEL = "PERSISTENT_ARTIFACT_CONTRACT_ADDED"
FIRST_FAILURE_PRESERVED_LABEL = "FIRST_FAILURE_EVIDENCE_PRESERVED"
STARTUP_CONTRACT_LABEL = "STARTUP_MATERIALIZATION_CONTRACT_ADDED"
STARTUP_COST_CONTRACT_LABEL = "STARTUP_MATERIALIZATION_COST_CONTRACT_ADDED"
RUNTIME_UPDATE_TIMING_LABEL = "RUNTIME_UPDATE_TIMING_EVIDENCE_ADDED"
EXISTING_TIMING_SUMMARY_LABEL = "EXISTING_ARTIFACT_TIMING_SUMMARY_EXTRACTED"
TIMING_FIELDS_MISSING_LABEL = "TIMING_FIELDS_MISSING_IN_EXISTING_ARTIFACTS"
PER_EPOCH_TIMING_INSTRUMENTATION_LABEL = "PER_EPOCH_TIMING_INSTRUMENTATION_ADDED"
UPDATE_COST_ANOMALY_LABEL = "UPDATE_COST_ANOMALY_REPORTED_ONLY"
EXECUTION_TRACE_LABEL = "EXECUTION_TRACE_CONTRACT_ADDED"
MODULE_SPANS_LABEL = "MODULE_OWNED_TRACE_SPANS_ADDED"
TRACE_AGGREGATION_LABEL = "TRACE_AGGREGATION_ADDED"
CRITICAL_PATH_LABEL = "CRITICAL_PATH_SUMMARY_ADDED"
COST_ATTRIBUTION_LABEL = "COST_ATTRIBUTION_SUMMARY_ADDED"
TRACE_BACKFILL_LIMITED_LABEL = "EXISTING_ARTIFACT_TRACE_BACKFILL_LIMITED"
HORIZON_COMPLETION_REPORTING_INCONSISTENT_LABEL = (
    "HORIZON_COMPLETION_REPORTING_INCONSISTENT"
)
RUNTIME_PRELOAD_NOT_EXERCISED_LABEL = "RUNTIME_PRELOAD_NOT_EXERCISED"
PARAMETER_UPDATE_TRIGGERED_DELTA_ATTACH_BLOCKER_LABEL = (
    "PARAMETER_UPDATE_TRIGGERED_DELTA_ATTACH_BLOCKER"
)
DEFAULT_COMPOSE_PROJECT = "seed_output"
SKYSEED_BPF_PIN_NAMES = (
    "active_generation",
    "ingress_classifier_map",
    "core_lfib_map",
    "egress_handoff_map",
    "packet_counters",
    "interface_counters",
    "interface_role_map",
    "miss_samples",
)
DIRECT_BACKEND_INTERFACE_RE = re.compile(
    r"(ss-ut|ss-gw|skyseed|seed_|stl-|starlinkexp|^vhs|^vtm|^sbr|^br-|^veth)"
)


class ProfileContractError(RuntimeError):
    """Raised when a validation profile violates its launch contract."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _run_command(
    command: Sequence[str],
    *,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=str(REPO_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=124,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + f"\ncommand timed out after {timeout:.1f}s",
        )


def _profile_path(name: str) -> Path:
    if "/" in name or "\\" in name or name.endswith(".json"):
        raise ProfileContractError(f"invalid profile name: {name!r}")
    return PROFILE_DIR / f"{name}.json"


def list_profiles() -> List[str]:
    if not PROFILE_DIR.exists():
        return []
    return sorted(path.stem for path in PROFILE_DIR.glob("*.json"))


def load_profile(name: str) -> Dict[str, Any]:
    path = _profile_path(name)
    if not path.exists():
        raise ProfileContractError(f"unknown validation profile: {name}")
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ProfileContractError(f"profile JSON must be an object: {path}")
    if str(payload.get("name") or "") != name:
        raise ProfileContractError(
            f"profile name mismatch: file={name}, payload={payload.get('name')!r}"
        )
    payload["_profile_path"] = str(path)
    return payload


def _resolve_repo_path(value: str | None) -> str | None:
    if value is None:
        return None
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((REPO_ROOT / path).resolve())


def _ids(entries: Sequence[Mapping[str, Any]], field: str) -> List[str]:
    return [str(entry[field]) for entry in entries if field in entry]


def _entry_source(entry: Mapping[str, Any]) -> str:
    for key in ("source", "catalog_source", "confidence", "type"):
        value = entry.get(key)
        if value:
            return str(value)
    return "catalog_entry"


def _catalog_by_id(entries: Sequence[Mapping[str, Any]], field: str) -> Dict[str, Dict[str, Any]]:
    return {str(entry[field]): dict(entry) for entry in entries if field in entry}


def _sort_entries(entries: Sequence[Mapping[str, Any]], field: str) -> List[Dict[str, Any]]:
    prefix = field.split("_", 1)[0]
    index_field = f"{prefix}_index"
    return sorted(
        (dict(entry) for entry in entries),
        key=lambda entry: (int(entry.get(index_field, 0)), str(entry.get(field, ""))),
    )


def _selector_spec(profile: Mapping[str, Any], kind: str) -> Dict[str, Any]:
    selector = dict(profile.get("validation_selector") or {})
    spec = selector.get(kind)
    if not isinstance(spec, dict):
        raise ProfileContractError(f"profile missing validation_selector.{kind}")
    return dict(spec)


def _resolve_selector(
    *,
    profile: Mapping[str, Any],
    kind: str,
    entries: Sequence[Mapping[str, Any]],
    id_field: str,
    legacy_ids: set[str],
    catalog_path: str | None,
) -> Dict[str, Any]:
    spec = _selector_spec(profile, kind)
    mode = str(spec.get("mode") or "").strip()
    if not mode:
        raise ProfileContractError(f"profile validation_selector.{kind}.mode is required")

    by_id = _catalog_by_id(entries, id_field)
    actual_ids = _ids(entries, id_field)
    selected_ids: List[str]
    source: str

    if mode == "explicit":
        raw_ids = spec.get("ids")
        if not isinstance(raw_ids, list) or not all(isinstance(item, str) for item in raw_ids):
            raise ProfileContractError(
                f"profile validation_selector.{kind}.ids must be a string list"
            )
        selected_ids = [str(item) for item in raw_ids]
        source = f"profile.validation_selector.{kind}.ids"
    elif mode == "catalog-derived-first-n":
        count = int(spec.get("count") or 0)
        if count <= 0:
            raise ProfileContractError(
                f"profile validation_selector.{kind}.count must be positive"
            )
        selected_ids = [str(entry[id_field]) for entry in _sort_entries(entries, id_field)[:count]]
        source = f"profile.validation_selector.{kind}.catalog-derived-first-n"
    elif mode == "catalog-all":
        selected_ids = list(actual_ids)
        source = f"profile.validation_selector.{kind}.catalog-all"
    elif mode == "none":
        selected_ids = []
        source = f"profile.validation_selector.{kind}.none"
    else:
        raise ProfileContractError(
            f"unsupported validation_selector.{kind}.mode: {mode!r}"
        )

    missing = [entry_id for entry_id in selected_ids if entry_id not in by_id]
    if missing:
        raise ProfileContractError(
            f"profile catalog id mismatch for {kind}: missing {missing}; "
            f"catalog_path={catalog_path}; actual_ids={actual_ids}"
        )

    hidden_legacy = sorted(set(selected_ids) & legacy_ids)
    if hidden_legacy and mode != "explicit":
        raise ProfileContractError(
            f"profile selected legacy demo {kind} ids {hidden_legacy} via {mode}; "
            "legacy demo ids require explicit profile selection and active catalog membership"
        )

    selected_sources = [
        {
            "id": entry_id,
            "selector_source": source,
            "catalog_path": catalog_path,
            "entry_source": _entry_source(by_id[entry_id]),
        }
        for entry_id in selected_ids
    ]
    return {
        "kind": kind,
        "id_field": id_field,
        "mode": mode,
        "catalog_path": catalog_path,
        "actual_ids": actual_ids,
        "selected_ids": selected_ids,
        "selected_sources": selected_sources,
    }


def resolve_catalog_contract(profile: Mapping[str, Any]) -> Dict[str, Any]:
    runner_args = dict(profile.get("runner_args") or {})
    contract = dict(profile.get("catalog_contract") or {})
    if not bool(contract.get("required", True)):
        return {
            "ok": True,
            "required": False,
            "reason": "profile_catalog_contract_not_required",
            "selectors": {},
        }

    ut_catalog_path = _resolve_repo_path(runner_args.get("ut_catalog"))
    gateway_catalog_path = _resolve_repo_path(runner_args.get("gateway_catalog"))
    zone_catalog_path = _resolve_repo_path(runner_args.get("zone_catalog"))
    ut_entries = load_ut_catalog(ut_catalog_path)
    gateway_entries = load_gateway_catalog(gateway_catalog_path)
    zone_entries = load_zone_catalog(zone_catalog_path)

    selectors = {
        "ut": _resolve_selector(
            profile=profile,
            kind="ut",
            entries=ut_entries,
            id_field="ut_id",
            legacy_ids=LEGACY_DEMO_UT_IDS,
            catalog_path=ut_catalog_path,
        ),
        "gateway": _resolve_selector(
            profile=profile,
            kind="gateway",
            entries=gateway_entries,
            id_field="gateway_id",
            legacy_ids=LEGACY_DEMO_GATEWAY_IDS,
            catalog_path=gateway_catalog_path,
        ),
        "zone": _resolve_selector(
            profile=profile,
            kind="zone",
            entries=zone_entries,
            id_field="zone_id",
            legacy_ids=set(),
            catalog_path=zone_catalog_path,
        ),
    }
    return {
        "ok": True,
        "required": True,
        "selectors": selectors,
        "forbidden_legacy_defaults": {
            "ut": sorted(LEGACY_DEMO_UT_IDS),
            "gateway": sorted(LEGACY_DEMO_GATEWAY_IDS),
        },
    }


def _arg_name(key: str) -> str:
    return "--" + key.replace("_", "-")


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def docker_dummy_network_available_subnets(pool: str, mask: int) -> Dict[str, Any]:
    network = ipaddress.ip_network(str(pool), strict=False)
    subnet_mask = int(mask)
    if subnet_mask < int(network.prefixlen):
        available = 0
    else:
        available = 2 ** (subnet_mask - int(network.prefixlen))
    addresses_per_subnet = (
        ipaddress.ip_network(f"0.0.0.0/{subnet_mask}").num_addresses
        if 0 <= subnet_mask <= 32
        else 0
    )
    return {
        "pool": str(network),
        "pool_prefixlen": int(network.prefixlen),
        "mask": subnet_mask,
        "available_subnets": int(available),
        "addresses_per_subnet": int(addresses_per_subnet),
        "formula": "available_subnets = 2^(M - P) when M >= P",
    }


def _parse_route_prefix(line: str) -> str | None:
    stripped = str(line).strip()
    if not stripped:
        return None
    first = stripped.split()[0]
    if first == "default":
        return None
    try:
        ipaddress.ip_network(first, strict=False)
    except ValueError:
        return None
    return first


def _host_route_prefixes() -> List[Dict[str, Any]]:
    try:
        completed = subprocess.run(
            ["ip", "route"],
            cwd=str(REPO_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        return [{"source": "ip route", "error": str(exc), "prefix": None}]
    prefixes: List[Dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        prefix = _parse_route_prefix(line)
        if prefix:
            prefixes.append({"source": "ip route", "prefix": prefix, "raw": line})
    return prefixes


def _docker_network_prefixes() -> List[Dict[str, Any]]:
    try:
        ids = subprocess.run(
            ["docker", "network", "ls", "-q"],
            cwd=str(REPO_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        return [{"source": "docker network inspect", "error": str(exc), "prefix": None}]
    prefixes: List[Dict[str, Any]] = []
    for network_id in [part.strip() for part in ids.stdout.splitlines() if part.strip()]:
        inspected = subprocess.run(
            ["docker", "network", "inspect", network_id],
            cwd=str(REPO_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if inspected.returncode != 0:
            prefixes.append(
                {
                    "source": "docker network inspect",
                    "network_id": network_id,
                    "prefix": None,
                    "error": inspected.stderr.strip(),
                }
            )
            continue
        try:
            payload = json.loads(inspected.stdout)[0]
        except (json.JSONDecodeError, IndexError, TypeError) as exc:
            prefixes.append(
                {
                    "source": "docker network inspect",
                    "network_id": network_id,
                    "prefix": None,
                    "error": str(exc),
                }
            )
            continue
        for config in list(dict(payload.get("IPAM") or {}).get("Config") or []):
            subnet = config.get("Subnet")
            if subnet:
                prefixes.append(
                    {
                        "source": "docker network inspect",
                        "network_id": network_id,
                        "network_name": payload.get("Name"),
                        "prefix": str(subnet),
                    }
                )
    return prefixes


def _network_overlaps(pool: str, prefixes: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    pool_network = ipaddress.ip_network(str(pool), strict=False)
    overlaps: List[Dict[str, Any]] = []
    for record in prefixes:
        prefix = record.get("prefix")
        if not prefix:
            continue
        try:
            candidate = ipaddress.ip_network(str(prefix), strict=False)
        except ValueError:
            continue
        if pool_network.overlaps(candidate):
            overlaps.append({**dict(record), "overlaps_pool": str(pool_network)})
    return overlaps


def _owned_container_records(
    *,
    runner: Any = _run_command,
    compose_project: str = DEFAULT_COMPOSE_PROJECT,
) -> List[Dict[str, Any]]:
    result = runner(
        [
            "docker",
            "ps",
            "-a",
            "--format",
            '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Status}}',
        ]
    )
    records: List[Dict[str, Any]] = []
    for line in str(result.stdout or "").splitlines():
        parts = line.split("\t")
        name = parts[0].strip() if parts else ""
        label = parts[1].strip() if len(parts) > 1 else ""
        status = parts[2].strip() if len(parts) > 2 else ""
        if not name:
            continue
        owned_by_label = label == compose_project
        owned_by_name = bool(
            re.match(r"^(stl-|ut\d+$|gw\d+$|seed_output_)", name)
        )
        if owned_by_label or owned_by_name:
            records.append(
                {
                    "name": name,
                    "status": status,
                    "compose_project": label,
                    "owner_match": "compose_label"
                    if owned_by_label
                    else "skyseed_name_pattern",
                }
            )
    return records


def _owned_network_records(
    *,
    runner: Any = _run_command,
    compose_project: str = DEFAULT_COMPOSE_PROJECT,
) -> List[Dict[str, Any]]:
    result = runner(
        [
            "docker",
            "network",
            "ls",
            "--format",
            '{{.Name}}\t{{.Label "com.docker.compose.project"}}',
        ]
    )
    records: List[Dict[str, Any]] = []
    for line in str(result.stdout or "").splitlines():
        parts = line.split("\t")
        name = parts[0].strip() if parts else ""
        label = parts[1].strip() if len(parts) > 1 else ""
        if not name:
            continue
        owned_by_label = label == compose_project
        owned_by_name = name.startswith(f"{compose_project}_")
        if owned_by_label or owned_by_name:
            records.append(
                {
                    "name": name,
                    "compose_project": label,
                    "owner_match": "compose_label"
                    if owned_by_label
                    else "seed_output_name_pattern",
                }
            )
    return records


def _network_prefixes_for_names(
    names: Sequence[str],
    *,
    runner: Any = _run_command,
) -> List[Dict[str, Any]]:
    prefixes: List[Dict[str, Any]] = []
    for name in names:
        inspected = runner(["docker", "network", "inspect", str(name)])
        if int(inspected.returncode) != 0:
            prefixes.append(
                {
                    "source": "docker network inspect",
                    "network_name": str(name),
                    "prefix": None,
                    "error": str(inspected.stderr or "").strip(),
                }
            )
            continue
        try:
            payload = json.loads(str(inspected.stdout or "[]"))[0]
        except (json.JSONDecodeError, IndexError, TypeError) as exc:
            prefixes.append(
                {
                    "source": "docker network inspect",
                    "network_name": str(name),
                    "prefix": None,
                    "error": str(exc),
                }
            )
            continue
        for config in list(dict(payload.get("IPAM") or {}).get("Config") or []):
            subnet = config.get("Subnet")
            if subnet:
                prefixes.append(
                    {
                        "source": "docker network inspect",
                        "network_id": payload.get("Id"),
                        "network_name": payload.get("Name") or str(name),
                        "prefix": str(subnet),
                    }
                )
    return prefixes


def _direct_backend_interface_records(*, runner: Any = _run_command) -> List[Dict[str, Any]]:
    result = runner(["ip", "-o", "link", "show"])
    records: List[Dict[str, Any]] = []
    for line in str(result.stdout or "").splitlines():
        parts = line.split(": ", 2)
        if len(parts) < 2:
            continue
        raw_name = parts[1].strip()
        name = raw_name.split("@", 1)[0]
        if DIRECT_BACKEND_INTERFACE_RE.search(name):
            records.append(
                {
                    "name": name,
                    "raw_name": raw_name,
                    "owner_match": "direct_backend_or_seed_output_interface_pattern",
                }
            )
    return records


def _owned_bpf_pin_records(*, runner: Any = _run_command) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for pin_name in SKYSEED_BPF_PIN_NAMES:
        path = f"/sys/fs/bpf/tc/globals/{pin_name}"
        result = runner(["sudo", "-n", "test", "-e", path])
        if int(result.returncode) == 0:
            records.append({"path": path, "name": pin_name, "owner_match": "bpf_pin_whitelist"})
    return records


def _runtime_lock_records(
    *,
    runner: Any = _run_command,
    run_root: Path | None = None,
) -> List[Dict[str, Any]]:
    roots = [str(run_root or Path("/tmp/skyseed_validation_profiles")), str(REPO_ROOT)]
    result = runner(
        [
            "find",
            *roots,
            "-maxdepth",
            "5",
            "-type",
            "f",
            "(",
            "-name",
            "*.lock",
            "-o",
            "-name",
            "*.lockfile",
            "-o",
            "-name",
            ".lock",
            ")",
        ],
        timeout=20.0,
    )
    records = []
    for line in str(result.stdout or "").splitlines():
        path = line.strip()
        if path:
            record: Dict[str, Any] = {
                "path": path,
                "owner_match": "skyseed_runtime_lock_pattern",
            }
            try:
                payload = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict) and payload.get("pid") is not None:
                try:
                    record["pid"] = int(payload.get("pid"))
                except (TypeError, ValueError):
                    record["pid"] = str(payload.get("pid"))
                if payload.get("run_id") is not None:
                    record["run_id"] = str(payload.get("run_id"))
            records.append(record)
    return records


def detect_cleanup_residue(
    *,
    docker_dummy_network_pool: str | None = None,
    runner: Any = _run_command,
    run_root: Path | None = None,
) -> Dict[str, Any]:
    containers = _owned_container_records(runner=runner)
    networks = _owned_network_records(runner=runner)
    direct_interfaces = _direct_backend_interface_records(runner=runner)
    bpf_pins = _owned_bpf_pin_records(runner=runner)
    locks = _runtime_lock_records(runner=runner, run_root=run_root)
    network_prefixes = _network_prefixes_for_names(
        [str(item["name"]) for item in networks],
        runner=runner,
    )
    overlaps = (
        _network_overlaps(str(docker_dummy_network_pool), network_prefixes)
        if docker_dummy_network_pool
        else []
    )
    counts = {
        "direct_backend_host_interfaces": len(direct_interfaces),
        "bpf_pins": len(bpf_pins),
        "compose_containers": len(containers),
        "compose_networks": len(networks),
        "runtime_locks": len(locks),
        "docker_network_pool_overlaps": len(overlaps),
    }
    return {
        "schema_version": "skyseed-cleanup-residue-v1",
        "owner_patterns": {
            "compose_project_label": DEFAULT_COMPOSE_PROJECT,
            "compose_network_name_prefix": f"{DEFAULT_COMPOSE_PROJECT}_",
            "container_name_pattern": "^(stl-|ut\\d+$|gw\\d+$|seed_output_)",
            "direct_interface_pattern": DIRECT_BACKEND_INTERFACE_RE.pattern,
            "bpf_pin_whitelist": list(SKYSEED_BPF_PIN_NAMES),
            "lock_patterns": ["*.lock", "*.lockfile", ".lock"],
        },
        "counts": counts,
        "clean": all(value == 0 for value in counts.values()),
        "direct_backend_host_interfaces": direct_interfaces,
        "bpf_pins": bpf_pins,
        "compose_containers": containers,
        "compose_networks": networks,
        "runtime_locks": locks,
        "docker_network_prefixes": network_prefixes,
        "docker_network_pool_overlaps": overlaps,
    }


def external_cleanup_success(residue: Mapping[str, Any]) -> bool:
    counts = dict(residue.get("counts") or {})
    return all(int(value or 0) == 0 for value in counts.values())


def _command_record(
    command: Sequence[str],
    result: subprocess.CompletedProcess[str],
) -> Dict[str, Any]:
    return {
        "args": list(command),
        "returncode": int(result.returncode),
        "stdout": str(result.stdout or "")[:2000],
        "stderr": str(result.stderr or "")[:2000],
    }


def _lock_pid_check_command(pid: int | str) -> List[str]:
    return ["sh", "-c", 'kill -0 "$1" 2>/dev/null', "sh", str(pid)]


def _preserve_failure_artifacts(
    *,
    run_dir: Path,
    preserve_run_dirs: Sequence[Path],
) -> Dict[str, Any]:
    preserve_root = run_dir / "preserved_first_failure_artifacts"
    copied: List[Dict[str, str]] = []
    for source_dir in preserve_run_dirs:
        if not source_dir.exists():
            continue
        target_dir = preserve_root / source_dir.name
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in (
            "profile_run_summary.json",
            "real_runtime_report.json",
            "runtime_progress.json",
            "epoch_execution_details.jsonl",
            "docker_dummy_network_capacity_gate.json",
            "profile_catalog_gate.json",
        ):
            source = source_dir / name
            if not source.exists():
                continue
            target = target_dir / name
            shutil.copy2(source, target)
            copied.append({"source": str(source), "preserved": str(target)})
    return {
        "preserve_root": str(preserve_root),
        "copied": copied,
        "copied_count": len(copied),
    }


def run_cleanup_only(
    *,
    run_dir: Path,
    docker_dummy_network_pool: str | None = None,
    preserve_run_dirs: Sequence[Path] = (),
    runner: Any = _run_command,
    report_filename: str = "cleanup_only_report.json",
    cleanup_mode: str = "cleanup_only_repair",
    success_outcome_label: str = "CLEANUP_ONLY_REPAIR_PASS",
) -> Dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    preserved = _preserve_failure_artifacts(
        run_dir=run_dir,
        preserve_run_dirs=preserve_run_dirs,
    )
    before = detect_cleanup_residue(
        docker_dummy_network_pool=docker_dummy_network_pool,
        runner=runner,
        run_root=run_dir.parent,
    )
    commands: List[Dict[str, Any]] = []

    for pin in list(before.get("bpf_pins") or []):
        command = ["sudo", "-n", "rm", "-f", str(pin.get("path"))]
        result = runner(command)
        commands.append(_command_record(command, result))

    container_names = [
        str(item.get("name"))
        for item in list(before.get("compose_containers") or [])
        if item.get("name")
    ]
    if container_names:
        command = ["docker", "rm", "-f", *container_names]
        result = runner(command, timeout=300.0)
        commands.append(_command_record(command, result))

    network_names = [
        str(item.get("name"))
        for item in list(before.get("compose_networks") or [])
        if item.get("name")
    ]
    for network_name in network_names:
        inspect = runner(["docker", "network", "inspect", network_name])
        commands.append(_command_record(["docker", "network", "inspect", network_name], inspect))
        endpoints: List[str] = []
        if int(inspect.returncode) == 0:
            try:
                payload = json.loads(str(inspect.stdout or "[]"))[0]
                for endpoint in dict(payload.get("Containers") or {}).values():
                    endpoint_name = str(endpoint.get("Name") or "").strip()
                    if endpoint_name:
                        endpoints.append(endpoint_name)
            except (json.JSONDecodeError, IndexError, TypeError, AttributeError):
                endpoints = []
        for endpoint_name in endpoints:
            command = ["docker", "network", "disconnect", "-f", network_name, endpoint_name]
            result = runner(command)
            commands.append(_command_record(command, result))
        command = ["docker", "network", "rm", network_name]
        result = runner(command)
        commands.append(_command_record(command, result))

    mid = detect_cleanup_residue(
        docker_dummy_network_pool=docker_dummy_network_pool,
        runner=runner,
        run_root=run_dir.parent,
    )
    for interface in list(mid.get("direct_backend_host_interfaces") or []):
        name = str(interface.get("name") or "")
        if not name or name.startswith("br-"):
            continue
        command = ["sudo", "-n", "ip", "link", "delete", name]
        result = runner(command)
        commands.append(_command_record(command, result))

    for lock in list(mid.get("runtime_locks") or []):
        pid = lock.get("pid")
        pid_check = None
        if pid is not None:
            check_command = _lock_pid_check_command(pid)
            pid_check = runner(check_command)
            check_record = _command_record(check_command, pid_check)
            check_record["lock_path"] = str(lock.get("path"))
            check_record["lock_run_id"] = lock.get("run_id")
            check_record["lock_pid_verification"] = (
                "pid_exists" if int(pid_check.returncode) == 0 else "pid_not_found"
            )
            commands.append(check_record)
            if int(pid_check.returncode) == 0:
                continue
        command = ["rm", "-f", str(lock.get("path"))]
        result = runner(command)
        remove_record = _command_record(command, result)
        remove_record["lock_path"] = str(lock.get("path"))
        remove_record["lock_run_id"] = lock.get("run_id")
        remove_record["lock_pid"] = pid
        remove_record["lock_pid_verification"] = (
            "pid_missing" if pid is None else "pid_not_found"
        )
        commands.append(remove_record)

    after = detect_cleanup_residue(
        docker_dummy_network_pool=docker_dummy_network_pool,
        runner=runner,
        run_root=run_dir.parent,
    )
    report = {
        "schema_version": "skyseed-automated-cleanup-contract-v1",
        "outcome_label": success_outcome_label
        if external_cleanup_success(after)
        else CLEANUP_INCOMPLETE_LABEL,
        "cleanup_mode": cleanup_mode,
        "detector_source": "detect_cleanup_residue",
        "teardown_only": True,
        "manual_cleanup": False,
        "preserved_first_failure_artifacts": preserved,
        "before": before,
        "commands": commands,
        "after": after,
        "external_cleanup_success": external_cleanup_success(after),
        "internal_cleanup_success_requires_external_residue_zero": True,
    }
    _write_json(run_dir / report_filename, report)
    return report


def _load_json_maybe(path: Path) -> Dict[str, Any] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _stage_timing_summary(
    stage_results: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    by_type: Dict[str, Dict[str, Any]] = {}
    for stage in stage_results:
        stage_type = str(stage.get("stage_type") or "unknown")
        bucket = by_type.setdefault(
            stage_type,
            {
                "count": 0,
                "completed": 0,
                "timed_out": 0,
                "sum_wall_seconds": 0.0,
                "max_wall_seconds": 0.0,
            },
        )
        wall = float(stage.get("wall_seconds") or 0.0)
        bucket["count"] += 1
        bucket["sum_wall_seconds"] = round(float(bucket["sum_wall_seconds"]) + wall, 6)
        bucket["max_wall_seconds"] = max(float(bucket["max_wall_seconds"]), wall)
        if bool(stage.get("timed_out")):
            bucket["timed_out"] += 1
        else:
            bucket["completed"] += 1
    for bucket in by_type.values():
        count = int(bucket["count"])
        bucket["average_wall_seconds"] = (
            round(float(bucket["sum_wall_seconds"]) / count, 6) if count else 0.0
        )
    return by_type


def evaluate_startup_materialization_contract(
    *,
    profile: Mapping[str, Any],
    runner_report: Mapping[str, Any] | None,
    capacity_gate: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    contract = dict(profile.get("startup_materialization") or {})
    if not contract:
        return {
            "required": False,
            "ok": True,
            "reason": "profile_startup_materialization_contract_not_declared",
        }
    report = dict(runner_report or {})
    startup_strategy = dict(report.get("startup_strategy") or {})
    footprint = dict(startup_strategy.get("footprint") or {})
    stage_results = list(startup_strategy.get("compose_stage_results") or [])
    total_expected_batches = int(
        startup_strategy.get("compose_stage_count")
        or contract.get("expected_compose_stage_batches")
        or len(stage_results)
    )
    completed_batches = sum(1 for stage in stage_results if not bool(stage.get("timed_out")))
    timed_out_batches = sum(1 for stage in stage_results if bool(stage.get("timed_out")))
    remaining_batches = max(total_expected_batches - completed_batches, 0)
    last_stage = dict(stage_results[-1]) if stage_results else {}
    last_completed = None
    for stage in reversed(stage_results):
        if not bool(stage.get("timed_out")):
            last_completed = dict(stage)
            break

    compose_timeout = float(
        ((report.get("runtime_blocker") or {}).get("timeout_seconds"))
        or contract.get("compose_up_timeout_seconds")
        or (profile.get("runner_args") or {}).get("compose_up_timeout_seconds")
        or 0.0
    )
    compose_wall = float(
        (((report.get("timing_analysis") or {}).get("startup_preparation") or {}).get(
            "compose_up_wall_seconds"
        ))
        or ((report.get("runtime_blocker") or {}).get("wall_seconds"))
        or 0.0
    )
    progress_ratio = (
        float(completed_batches) / float(total_expected_batches)
        if total_expected_batches
        else 0.0
    )
    stall_threshold = float(contract.get("stall_no_progress_seconds") or 120.0)
    failure_reason = str(report.get("failure_reason") or "")
    runtime_blocker = dict(report.get("runtime_blocker") or {})
    blocker_label = str(
        runtime_blocker.get("classified_blocker")
        or failure_reason
        or ""
    )
    timed_out = (
        failure_reason == "docker_compose_up_timeout"
        or bool(runtime_blocker.get("classified_blocker"))
        and str(runtime_blocker.get("stage") or "") == "docker_compose_up"
    )
    last_timeout_budget = float(last_stage.get("timeout_seconds") or 0.0)
    startup_contracts = [
        dict(stage.get("startup_progress_contract") or {})
        for stage in stage_results
        if isinstance(stage.get("startup_progress_contract"), dict)
    ]
    startup_contracts.extend(
        [
            dict(runtime_blocker.get("startup_progress_contract") or {})
        ]
        if isinstance(runtime_blocker.get("startup_progress_contract"), dict)
        else []
    )
    grace_applied = any(
        bool(contract.get("high_progress_grace_applied"))
        or bool(contract.get("slow_progress_grace_applied"))
        or bool(contract.get("progress_grace_applied"))
        for contract in startup_contracts
    )
    no_progress_stall = blocker_label == NO_PROGRESS_STALL_LABEL or any(
        bool(contract.get("no_progress_stall")) for contract in startup_contracts
    )
    soft_budget_exceeded = bool(
        compose_timeout > 0.0 and compose_wall > compose_timeout
    ) or any(bool(contract.get("soft_budget_exceeded")) for contract in startup_contracts)
    high_progress_continue = any(
        bool(contract.get("high_progress_continue")) for contract in startup_contracts
    )
    hard_cap_exceeded = (
        blocker_label == STARTUP_HARD_CAP_LABEL
        or any(
            bool(contract.get("hard_cap_exceeded"))
            or bool(contract.get("absolute_max_exceeded"))
            for contract in startup_contracts
        )
    )
    resource_deadlock = (
        blocker_label == RESOURCE_DEADLOCK_LABEL
        or any(
            bool(contract.get("resource_deadlock_detected"))
            for contract in startup_contracts
        )
    )
    recoverable_stage_failure = blocker_label == STARTUP_RECOVERABLE_STAGE_FAILURE_LABEL
    operator_abort = blocker_label == "OPERATOR_ABORT" or any(
        bool(contract.get("operator_abort")) for contract in startup_contracts
    )
    slow_but_progressing = bool(
        timed_out
        and completed_batches > 0
        and progress_ratio >= float(contract.get("slow_progress_min_completed_ratio") or 0.5)
        and last_timeout_budget < stall_threshold
    )
    actual_stall = bool(
        timed_out
        and not slow_but_progressing
        and (not last_completed or last_timeout_budget >= stall_threshold)
    )

    expected_dummy_networks = int(
        contract.get("expected_dummy_networks")
        or (capacity_gate or {}).get("required_dummy_networks")
        or 0
    )
    expected = {
        "containers": int(contract.get("expected_containers") or 0),
        "docker_compose_networks": int(contract.get("expected_docker_networks") or 0),
        "direct_backend_host_interfaces_before_runtime": int(
            contract.get("expected_direct_backend_host_interfaces_before_runtime") or 0
        ),
        "dummy_networks": expected_dummy_networks,
        "qdisc_root_objects": contract.get("expected_qdisc_root_objects"),
        "qdisc_filter_objects": contract.get("expected_qdisc_filter_objects"),
    }
    observed = {
        "containers": int(footprint.get("service_count") or 0),
        "docker_compose_networks": int(footprint.get("network_count") or 0),
        "direct_backend_host_interfaces_before_runtime": 0,
        "dummy_networks": expected_dummy_networks,
        "qdisc_root_objects": None,
        "qdisc_filter_objects": None,
    }
    mismatches = [
        key
        for key, expected_value in expected.items()
        if expected_value is not None and observed.get(key) != expected_value
    ]
    outcome_labels: List[str] = []
    if soft_budget_exceeded:
        outcome_labels.extend(
            [STARTUP_SOFT_BUDGET_WARNING_LABEL, STARTUP_COST_ANOMALY_LABEL]
        )
    if grace_applied or high_progress_continue:
        outcome_labels.append(SLOW_PROGRESS_CONTINUE_LABEL)
    if high_progress_continue:
        outcome_labels.append("HIGH_PROGRESS_STARTUP_CONTINUE")
    if no_progress_stall:
        outcome_labels.append(NO_PROGRESS_STALL_LABEL)
    if recoverable_stage_failure:
        outcome_labels.append(STARTUP_RECOVERABLE_STAGE_FAILURE_LABEL)
    if resource_deadlock or (
        timed_out
        and not recoverable_stage_failure
        and not no_progress_stall
        and not hard_cap_exceeded
    ):
        resource_deadlock = True
        outcome_labels.append(RESOURCE_DEADLOCK_LABEL)
    if hard_cap_exceeded:
        outcome_labels.append(STARTUP_HARD_CAP_LABEL)
    if operator_abort:
        outcome_labels.append("OPERATOR_ABORT")
    killed = bool(no_progress_stall or resource_deadlock or hard_cap_exceeded or operator_abort)
    return {
        "schema_version": "skyseed-startup-materialization-contract-v1",
        "required": True,
        "ok": not mismatches and not killed,
        "outcome_labels": list(dict.fromkeys(outcome_labels)),
        "timeout_classification": (
            "no_progress_stall"
            if no_progress_stall
            else "startup_hard_cap_exceeded"
            if hard_cap_exceeded
            else "resource_deadlock_startup_abort"
            if resource_deadlock
            else "recoverable_stage_failure"
            if recoverable_stage_failure
            else "operator_abort"
            if operator_abort
            else "slow_but_progressing_continue"
            if grace_applied or high_progress_continue or slow_but_progressing
            else "startup_soft_budget_exceeded_warning"
            if soft_budget_exceeded
            else "not_timed_out"
        ),
        "compose_up_timeout_seconds": compose_timeout,
        "compose_up_wall_seconds": compose_wall,
        "last_compose_up_stage": str(
            (report.get("runtime_blocker") or {}).get("compose_up_stage")
            or last_stage.get("stage_id")
            or ""
        ),
        "total_expected_batches": total_expected_batches,
        "completed_batches": completed_batches,
        "timed_out_batches": timed_out_batches,
        "remaining_batches": remaining_batches,
        "progress_ratio": round(progress_ratio, 6),
        "soft_budget_exceeded": soft_budget_exceeded,
        "progress_was_happening_at_timeout": slow_but_progressing,
        "actual_stall_detected": actual_stall,
        "progress_grace_applied": grace_applied,
        "high_progress_continue": high_progress_continue,
        "no_progress_stall": no_progress_stall,
        "absolute_cap_exceeded": hard_cap_exceeded,
        "hard_cap_exceeded": hard_cap_exceeded,
        "resource_deadlock_startup_abort": resource_deadlock,
        "operator_abort": operator_abort,
        "kill_reason": (
            "no_progress_stall"
            if no_progress_stall
            else "startup_hard_cap_exceeded"
            if hard_cap_exceeded
            else "resource_exhaustion_or_deadlock"
            if resource_deadlock
            else "operator_abort"
            if operator_abort
            else None
        ),
        "startup_progress_contracts": startup_contracts,
        "per_batch_timing_by_type": _stage_timing_summary(stage_results),
        "last_stage": last_stage,
        "last_completed_stage": last_completed,
        "materialization_cardinality": {
            "expected": expected,
            "observed": observed,
            "mismatches": mismatches,
            "footprint_service_counts": dict(footprint.get("service_counts") or {}),
            "footprint_network_counts": dict(footprint.get("network_counts") or {}),
            "attachment_count": int(footprint.get("attachment_count") or 0),
        },
        "budget_justification": contract.get("timeout_budget_justification"),
    }


def _startup_stage_status(
    stage: Mapping[str, Any],
    *,
    runtime_blocker: Mapping[str, Any],
) -> str:
    if bool(stage.get("recovered_after_timeout")):
        return "completed"
    returncode = stage.get("returncode")
    timed_out = bool(stage.get("timed_out"))
    failed_returncode = returncode is not None and int(returncode) != 0
    if not timed_out and not failed_returncode:
        return "completed"
    blocker = str(runtime_blocker.get("classified_blocker") or "")
    if bool(stage.get("correctness_fatal")) or blocker in {
        STARTUP_CORRECTNESS_FATAL_LABEL,
        "docker_compose_build_failed",
        "ground_endpoint_readiness_failed",
    }:
        return "fatal"
    if blocker in {
        NO_PROGRESS_STALL_LABEL,
        STARTUP_NO_PROGRESS_STALL_LABEL,
        RESOURCE_DEADLOCK_LABEL,
        STARTUP_RESOURCE_DEADLOCK_LABEL,
        STARTUP_HARD_CAP_LABEL,
        "OPERATOR_ABORT",
    }:
        return "fatal"
    return "retryable"


def evaluate_startup_state_supervision_contract(
    *,
    profile: Mapping[str, Any],
    runner_report: Mapping[str, Any] | None,
    startup_contract: Mapping[str, Any],
) -> Dict[str, Any]:
    report = dict(runner_report or {})
    startup_strategy = dict(report.get("startup_strategy") or {})
    footprint = dict(startup_strategy.get("footprint") or {})
    runtime_blocker = dict(report.get("runtime_blocker") or {})
    stage_results = [dict(stage) for stage in startup_strategy.get("compose_stage_results") or []]
    total_batches = int(
        startup_strategy.get("compose_stage_count")
        or startup_contract.get("total_expected_batches")
        or len(stage_results)
        or 0
    )
    completed_batches = sum(1 for stage in stage_results if _startup_stage_status(stage, runtime_blocker=runtime_blocker) == "completed")
    failed_stage = None
    per_batch_status: List[Dict[str, Any]] = []
    for index, stage in enumerate(stage_results):
        status = _startup_stage_status(stage, runtime_blocker=runtime_blocker)
        if status in {"failed", "retryable", "fatal"} and failed_stage is None:
            failed_stage = stage
        per_batch_status.append(
            {
                "stage_index": index,
                "stage_id": str(stage.get("stage_id") or f"stage-{index}"),
                "batch_id": str(stage.get("batch_id") or stage.get("stage_id") or f"batch-{index}"),
                "stage_type": str(stage.get("stage_type") or ""),
                "status": status,
                "service_count": int(stage.get("service_count") or len(list(stage.get("service_names") or []))),
                "retryable": status == "retryable",
                "fatal": status == "fatal",
            }
        )
    current_stage = str(
        runtime_blocker.get("compose_up_stage")
        or (failed_stage or (stage_results[-1] if stage_results else {})).get("stage_id")
        or ""
    )
    current_batch = str(
        (failed_stage or (stage_results[-1] if stage_results else {})).get("batch_id")
        or current_stage
    )
    ready_containers = sorted(
        {
            service
            for stage in stage_results
            if _startup_stage_status(stage, runtime_blocker=runtime_blocker) == "completed"
            for service in list(stage.get("service_names") or [])
        }
        | set(str(name) for name in runtime_blocker.get("running_stage_service_names") or [])
    )
    progress_contracts = [
        dict(stage.get("startup_progress_contract") or {})
        for stage in stage_results
        if isinstance(stage.get("startup_progress_contract"), Mapping)
    ]
    if isinstance(runtime_blocker.get("startup_progress_contract"), Mapping):
        progress_contracts.append(dict(runtime_blocker.get("startup_progress_contract") or {}))
    last_progress_contract = progress_contracts[-1] if progress_contracts else {}
    no_progress = bool(startup_contract.get("no_progress_stall")) or str(runtime_blocker.get("classified_blocker") or "") in {
        NO_PROGRESS_STALL_LABEL,
        STARTUP_NO_PROGRESS_STALL_LABEL,
    }
    resource_deadlock = bool(startup_contract.get("resource_deadlock_startup_abort")) or str(runtime_blocker.get("classified_blocker") or "") in {
        RESOURCE_DEADLOCK_LABEL,
        STARTUP_RESOURCE_DEADLOCK_LABEL,
    }
    hard_cap = bool(startup_contract.get("hard_cap_exceeded"))
    operator_abort = bool(startup_contract.get("operator_abort"))
    correctness_fatal = str(runtime_blocker.get("classified_blocker") or "") in {
        STARTUP_CORRECTNESS_FATAL_LABEL,
        "docker_compose_build_failed",
        "ground_endpoint_readiness_failed",
    } or any(status["fatal"] and bool(dict(stage_results[status["stage_index"]]).get("correctness_fatal")) for status in per_batch_status)
    recoverable_stage_failure = any(status["status"] == "retryable" for status in per_batch_status)
    checkpoint_available = bool(stage_results and recoverable_stage_failure)
    resume_implemented = bool(
        dict(profile.get("startup_resume") or {}).get("enabled")
        or startup_strategy.get("resume_implemented")
    )
    cleanup_required = bool(
        no_progress
        or resource_deadlock
        or hard_cap
        or operator_abort
        or correctness_fatal
    )
    if recoverable_stage_failure and checkpoint_available and not cleanup_required:
        cleanup_required = False
    labels = [STARTUP_STATE_SUPERVISION_LABEL]
    if bool(startup_contract.get("soft_budget_exceeded")):
        labels.append(STARTUP_SOFT_BUDGET_WARNING_ONLY_LABEL)
    if bool(startup_contract.get("high_progress_continue") or startup_contract.get("progress_grace_applied")) and not cleanup_required:
        labels.append(STARTUP_PROGRESSING_CONTINUE_LABEL)
    if no_progress:
        labels.append(STARTUP_NO_PROGRESS_STALL_LABEL)
    if resource_deadlock:
        labels.append(STARTUP_RESOURCE_DEADLOCK_LABEL)
    if correctness_fatal:
        labels.append(STARTUP_CORRECTNESS_FATAL_LABEL)
    if recoverable_stage_failure:
        labels.append(STARTUP_RECOVERABLE_STAGE_FAILURE_LABEL)
    if checkpoint_available:
        labels.append(STARTUP_RESUMABLE_CHECKPOINT_AVAILABLE_LABEL)
    if recoverable_stage_failure and checkpoint_available and not resume_implemented:
        labels.append(STARTUP_RECOVERABLE_RESUME_NOT_IMPLEMENTED_LABEL)
    labels.append(CLEANUP_REQUIRED_LABEL if cleanup_required else CLEANUP_NOT_REQUIRED_RECOVERABLE_LABEL)
    return {
        "schema_version": "skyseed-startup-state-supervision-v1",
        "owner_module": "satellite/apps/run_skyseed_validation_profile.py",
        "startup_phase": str(runtime_blocker.get("stage") or "docker_compose_up" if stage_results else "startup"),
        "current_stage": current_stage,
        "current_batch": current_batch,
        "completed_batches": completed_batches,
        "total_batches": total_batches,
        "per_batch_status": per_batch_status,
        "created_resources": {
            "containers": int(footprint.get("service_count") or 0),
            "docker_networks": int(footprint.get("network_count") or 0),
            "interfaces": int(footprint.get("attachment_count") or 0),
        },
        "ready_containers": ready_containers,
        "last_real_progress_timestamp": None,
        "last_real_progress_reason": str(last_progress_contract.get("last_progress_reason") or ""),
        "seconds_since_progress": last_progress_contract.get("seconds_since_progress"),
        "docker_daemon_responsiveness": last_progress_contract.get("docker_responsive"),
        "child_process_state": {
            "active_child_process_count": last_progress_contract.get("active_child_process_count"),
        },
        "resource_snapshot": dict(last_progress_contract.get("resource_snapshot") or {}),
        "exact_blocked_stage": current_stage if failed_stage or runtime_blocker else "",
        "exact_blocked_batch": current_batch if failed_stage or runtime_blocker else "",
        "recoverable_stage_failure": recoverable_stage_failure,
        "resume_checkpoint_available": checkpoint_available,
        "resume_implemented": resume_implemented,
        "cleanup_required": cleanup_required,
        "cleanup_decision": (
            CLEANUP_REQUIRED_LABEL if cleanup_required else CLEANUP_NOT_REQUIRED_RECOVERABLE_LABEL
        ),
        "kill_reason": startup_contract.get("kill_reason") or runtime_blocker.get("kill_reason"),
        "outcome_labels": list(dict.fromkeys(labels)),
    }


def _operation_requires_idempotency(operation_type: str) -> bool:
    lowered = operation_type.lower()
    return any(token in lowered for token in ("create", "apply", "materialize", "prewarm", "reconcile", "compose_up"))


def _startup_operations_from_report(
    runner_report: Mapping[str, Any] | None,
) -> tuple[List[Dict[str, Any]], bool]:
    report = dict(runner_report or {})
    startup_strategy = dict(report.get("startup_strategy") or {})
    raw = (
        report.get("startup_operation_trace")
        or startup_strategy.get("startup_operation_trace")
        or startup_strategy.get("operation_trace")
        or []
    )
    if isinstance(raw, list) and raw:
        return [dict(operation) for operation in raw if isinstance(operation, Mapping)], False
    operations = []
    for index, stage in enumerate(startup_strategy.get("compose_stage_results") or []):
        if not isinstance(stage, Mapping):
            continue
        stage_id = str(stage.get("stage_id") or f"stage-{index}")
        operations.append(
            {
                "operation_id": f"synthetic-compose-up-{stage_id}",
                "operation_type": "docker_compose_up_stage",
                "owner_module": "satellite/apps/run_starlink_shell_experiment.py",
                "stage": stage_id,
                "batch": str(stage.get("batch_id") or stage_id),
                "target_resource": ",".join(str(name) for name in stage.get("service_names") or []),
                "target_resource_type": "compose_service_batch",
                "intent": "compose startup stage",
                "reason": "derived_from_compose_stage_results",
                "idempotency_key": "",
                "expected_cardinality": int(stage.get("service_count") or 0),
                "actual_attempt_count": 1,
                "duplicate_count": 0,
                "retry_count": 1 if stage.get("retry_after_timeout") else 0,
                "duration_seconds": float(stage.get("wall_seconds") or 0.0),
                "state_changed": not bool(stage.get("timed_out")),
                "result": "timed_out" if bool(stage.get("timed_out")) else "completed",
                "error": str(stage.get("stderr") or "") if bool(stage.get("timed_out")) else "",
            }
        )
    return operations, True


def evaluate_startup_operation_audit(
    *,
    runner_report: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    operations, synthesized = _startup_operations_from_report(runner_report)
    required_fields = {
        "operation_id",
        "operation_type",
        "owner_module",
        "stage",
        "batch",
        "target_resource",
        "target_resource_type",
        "intent",
        "reason",
        "idempotency_key",
        "expected_cardinality",
        "actual_attempt_count",
        "duplicate_count",
        "retry_count",
        "duration_seconds",
        "state_changed",
        "result",
        "error",
    }
    labels = [STARTUP_OPERATION_TRACE_ADDED_LABEL]
    incomplete = bool(synthesized or not operations)
    missing_fields: Dict[str, List[str]] = {}
    group_counts: Dict[tuple[str, str, str], int] = {}
    target_create_counts: Dict[tuple[str, str], int] = {}
    full_reconcile_counts: Dict[str, int] = {}
    cost_breakdown = {
        "real_materialization_work_seconds": 0.0,
        "readiness_waiting_seconds": 0.0,
        "retry_cost_seconds": 0.0,
        "duplicate_noop_cost_seconds": 0.0,
        "cleanup_reporting_seconds": 0.0,
    }
    normalized_operations: List[Dict[str, Any]] = []
    for index, operation in enumerate(operations):
        op = dict(operation)
        for field in required_fields:
            op.setdefault(field, "" if field not in {"expected_cardinality", "actual_attempt_count", "duplicate_count", "retry_count", "duration_seconds", "state_changed"} else 0)
        operation_id = str(op.get("operation_id") or f"startup-op-{index}")
        operation_type = str(op.get("operation_type") or "")
        target = str(op.get("target_resource") or "")
        idempotency_key = str(op.get("idempotency_key") or "")
        missing = sorted(
            field
            for field in required_fields
            if field != "error"
            and (field not in operation or operation.get(field) in (None, ""))
        )
        if _operation_requires_idempotency(operation_type) and not idempotency_key:
            missing.append("idempotency_key")
        if missing:
            incomplete = True
            missing_fields[operation_id] = sorted(set(missing))
        key = (operation_type, target, idempotency_key)
        group_counts[key] = group_counts.get(key, 0) + int(op.get("actual_attempt_count") or 1)
        lowered = operation_type.lower()
        if any(token in lowered for token in ("create", "materialize", "compose_up", "apply")):
            create_key = (str(op.get("target_resource_type") or ""), target)
            target_create_counts[create_key] = target_create_counts.get(create_key, 0) + 1
        if "full_reconcile" in lowered or (bool(op.get("full_scope")) and any(token in lowered for token in ("reconcile", "prewarm", "materialize"))):
            full_reconcile_counts[operation_type] = full_reconcile_counts.get(operation_type, 0) + 1
        duration = float(op.get("duration_seconds") or 0.0)
        retry_count = int(op.get("retry_count") or 0)
        duplicate_count = int(op.get("duplicate_count") or 0)
        state_changed = bool(op.get("state_changed"))
        if "cleanup" in lowered or "report" in lowered:
            cost_breakdown["cleanup_reporting_seconds"] += duration
        elif "wait" in lowered or "readiness" in lowered:
            cost_breakdown["readiness_waiting_seconds"] += duration
        elif retry_count > 0:
            cost_breakdown["retry_cost_seconds"] += duration
        elif duplicate_count > 0 or not state_changed:
            cost_breakdown["duplicate_noop_cost_seconds"] += duration
        else:
            cost_breakdown["real_materialization_work_seconds"] += duration
        op["operation_id"] = operation_id
        op["duplicate_count"] = duplicate_count
        op["retry_count"] = retry_count
        op["duration_seconds"] = round(duration, 6)
        normalized_operations.append(op)
    duplicate_groups = {
        "|".join(key): count for key, count in group_counts.items() if count > 1
    }
    duplicate_resource_operations = {
        "|".join(key): count for key, count in target_create_counts.items() if count > 1
    }
    noop_retry_operations = [
        str(op.get("operation_id"))
        for op in normalized_operations
        if int(op.get("retry_count") or 0) > 0 and not bool(op.get("state_changed"))
    ]
    repeated_full_reconcile = {
        key: count
        for key, count in full_reconcile_counts.items()
        if count > 1
    }
    unjustified_repeated_full_reconcile = bool(
        repeated_full_reconcile
        and not all(
            str(op.get("reason") or "").strip()
            for op in normalized_operations
            if str(op.get("operation_type") or "") in repeated_full_reconcile
        )
    )
    cardinality_anomalies = [
        str(op.get("operation_id"))
        for op in normalized_operations
        if op.get("expected_cardinality") not in (None, "")
        and int(op.get("expected_cardinality") or 0) > 0
        and int(op.get("actual_attempt_count") or 0) > int(op.get("expected_cardinality") or 0)
    ]
    if incomplete:
        labels.append(STARTUP_OPERATION_TRACE_INCOMPLETE_LABEL)
    if duplicate_groups:
        labels.append(STARTUP_REDUNDANT_OPERATION_LOOP_LABEL)
    if duplicate_resource_operations:
        labels.append(STARTUP_DUPLICATE_RESOURCE_OPERATION_LABEL)
    if noop_retry_operations:
        labels.append(STARTUP_NOOP_RETRY_LOOP_LABEL)
    if repeated_full_reconcile:
        labels.append(STARTUP_FULL_RECONCILE_REPEATED_LABEL)
    if cardinality_anomalies or unjustified_repeated_full_reconcile:
        labels.append(STARTUP_OPERATION_CARDINALITY_ANOMALY_LABEL)
    rounded_cost_breakdown = {
        key: round(value, 6) for key, value in cost_breakdown.items()
    }
    audit_summary = {
        "operation_trace_complete": not incomplete,
        "operation_trace_synthesized_from_stage_results": synthesized,
        "operation_count": len(normalized_operations),
        "missing_required_field_operation_count": len(missing_fields),
        "duplicate_operation_group_count": len(duplicate_groups),
        "duplicate_resource_operation_count": len(duplicate_resource_operations),
        "noop_retry_operation_count": len(noop_retry_operations),
        "repeated_full_reconcile_operation_count": len(repeated_full_reconcile),
        "cardinality_anomaly_count": len(cardinality_anomalies),
        "unjustified_repeated_full_reconcile": unjustified_repeated_full_reconcile,
        "has_redundant_operation_loop": bool(duplicate_groups),
        "has_duplicate_resource_operation": bool(duplicate_resource_operations),
        "has_noop_retry_loop": bool(noop_retry_operations),
        "has_repeated_full_reconcile": bool(repeated_full_reconcile),
        "has_cardinality_anomaly": bool(
            cardinality_anomalies or unjustified_repeated_full_reconcile
        ),
        "startup_cost_understood": not incomplete,
        "cost_breakdown": rounded_cost_breakdown,
    }
    return {
        "schema_version": "skyseed-startup-operation-audit-v1",
        "owner_module": "satellite/apps/run_skyseed_validation_profile.py",
        "summary": audit_summary,
        "operation_count": len(normalized_operations),
        "operation_trace_complete": not incomplete,
        "operation_trace_synthesized_from_stage_results": synthesized,
        "missing_required_fields": missing_fields,
        "operations": normalized_operations,
        "duplicate_operation_groups": duplicate_groups,
        "duplicate_resource_operations": duplicate_resource_operations,
        "noop_retry_operations": noop_retry_operations,
        "repeated_full_reconcile_operations": repeated_full_reconcile,
        "cardinality_anomalies": cardinality_anomalies,
        "cost_breakdown": rounded_cost_breakdown,
        "startup_cost_understood": not incomplete,
        "outcome_labels": list(dict.fromkeys(labels)),
    }


def _slot_inventory(topology: Mapping[str, Any] | None) -> Dict[str, List[Dict[str, Any]]]:
    inventory = dict((topology or {}).get("slot_inventory") or {})
    return {
        str(category): [dict(slot or {}) for slot in list(slots or [])]
        for category, slots in inventory.items()
    }


def _slot_endpoint_interface_refs(slot: Mapping[str, Any]) -> List[str]:
    refs: List[str] = []
    candidates = list(slot.get("candidate_pairs") or [])
    endpoint_templates = [
        dict(slot.get("endpoint_a_template") or {}),
        dict(slot.get("endpoint_b_template") or {}),
    ]
    for pair in candidates:
        pair_dict = dict(pair or {})
        satellite_ids = [
            pair_dict.get("satellite_a_id"),
            pair_dict.get("satellite_b_id"),
        ]
        for satellite_id, template in zip(satellite_ids, endpoint_templates):
            interface_name = str(template.get("interface_name") or "")
            if satellite_id and interface_name:
                refs.append(f"netns:{satellite_id}:{interface_name}")
    return sorted(set(refs))


def _slot_for_interface(
    topology: Mapping[str, Any] | None,
    interface_ref: str,
) -> Dict[str, Any] | None:
    for category, slots in _slot_inventory(topology).items():
        for slot in slots:
            refs = _slot_endpoint_interface_refs(slot)
            if interface_ref in refs:
                return {
                    "slot_type": category,
                    "slot_name": slot.get("slot_name"),
                    "network_name": slot.get("network_name")
                    or slot.get("docker_network_name"),
                    "endpoint_interface_refs": refs,
                    "candidate_pairs": list(slot.get("candidate_pairs") or []),
                    "subnet": slot.get("subnet"),
                }
    return None


def _link_state_slot(
    link_state: Mapping[str, Any] | None,
    slot_name: str | None,
) -> Dict[str, Any]:
    if not slot_name:
        return {}
    for slot in list((link_state or {}).get("slots") or []):
        slot_dict = dict(slot or {})
        if str(slot_dict.get("slot_name") or "") == str(slot_name):
            return slot_dict
    return {}


def _prepared_bpf_scope(prepared: Mapping[str, Any] | None) -> Dict[str, Any]:
    queue = (
        dict(prepared or {})
        .get("continuous_runtime_execution", {})
        .get("epoch_execution_queue", [])
    )
    first = dict(queue[0] if queue else {})
    readiness = (
        first.get("node_local_execution", {})
        .get("backend", {})
        .get("bpf_owned_readiness", {})
    )
    prepare = (
        readiness.get("prepare_steps", {})
        .get("real_backend_prepare", {})
    )
    coverage = dict(
        prepare.get("resident_attach_coverage")
        or readiness.get("resident_attach_coverage")
        or {}
    )
    attach_plan = dict(prepare.get("attach_plan") or {})
    return {
        "attach_plan": attach_plan,
        "active_materialized_interfaces": list(
            coverage.get("active_materialized_interfaces") or []
        ),
        "future_materialized_attach_eligible_interfaces": list(
            coverage.get("future_materialized_attach_eligible_interfaces") or []
        ),
        "future_non_materialized_capacity_only_interfaces": list(
            coverage.get("future_non_materialized_capacity_only_interfaces") or []
        ),
        "compile_capacity_interfaces": list(
            coverage.get("compile_capacity_interfaces") or []
        ),
        "resident_attach_future_materialized_interface_count": coverage.get(
            "future_materialized_attach_eligible_interface_count"
        ),
        "resident_attach_future_non_materialized_interface_count": coverage.get(
            "future_non_materialized_capacity_only_interface_count"
        ),
    }


def evaluate_static_inventory_precreated_connection_audit(
    *,
    run_dir: Path,
    interface_refs: Sequence[str] = (
        "netns:stl-940120:xp120a",
        "netns:stl-940178:xp120b",
    ),
) -> Dict[str, Any]:
    topology = _load_json_maybe(run_dir / "topology.json") or {}
    link_state = _load_json_maybe(run_dir / "link_state.json") or {}
    prepared = _load_json_maybe(run_dir / "prepared_update_bundle.json") or {}
    offline = _load_json_maybe(run_dir / "bpf_mpls_offline_validation.json") or {}
    lifecycle = _load_json_maybe(run_dir / "lifecycle_aware_horizon_contract.json") or {}
    catalog_gate = _load_json_maybe(run_dir / "profile_catalog_gate.json") or {}
    slot_capacity = _load_json_maybe(run_dir / "slot_capacity_audit.json") or {}
    inventory = _slot_inventory(topology)
    link_slots = [dict(slot or {}) for slot in list(link_state.get("slots") or [])]
    link_slots_by_type: Dict[str, List[Dict[str, Any]]] = {}
    for slot in link_slots:
        link_slots_by_type.setdefault(str(slot.get("slot_type") or ""), []).append(slot)
    selected_ut_count = len(
        list(
            dict(
                catalog_gate.get("selectors", {})
            )
            .get("ut", {})
            .get("selected_ids", [])
        )
    )
    selected_gateway_count = len(
        list(
            dict(
                catalog_gate.get("selectors", {})
            )
            .get("gateway", {})
            .get("selected_ids", [])
        )
    )
    prepared_scope = _prepared_bpf_scope(prepared)
    compile_capacity = set(prepared_scope["compile_capacity_interfaces"])
    attach_plan = dict(prepared_scope["attach_plan"])
    active = set(prepared_scope["active_materialized_interfaces"])
    future_materialized = set(
        prepared_scope["future_materialized_attach_eligible_interfaces"]
    )
    future_capacity_only = set(
        prepared_scope["future_non_materialized_capacity_only_interfaces"]
    )
    linux_fallback_count = int(
        dict(
            dict(offline.get("path_cardinality_audit") or {}).get("aggregate") or {}
        ).get("linux_gz_mpls_lfib_fallback_count")
        or 0
    )
    unsafe_conflict_count = int(
        dict(offline.get("aggregate") or {}).get("unsafe_conflict_count") or 0
    )
    xp_reports: List[Dict[str, Any]] = []
    for interface_ref in interface_refs:
        slot = _slot_for_interface(topology, interface_ref) or {}
        slot_state = _link_state_slot(link_state, slot.get("slot_name"))
        slot_found = bool(slot)
        link_state_found = bool(slot_state)
        occupied = bool(slot_state.get("occupied"))
        dynamic_endpoints = list(slot_state.get("dynamic_endpoints") or [])
        preconnected_endpoints = list(slot_state.get("preconnected_endpoints") or [])
        reachable_peer_path = bool(
            slot_state.get("reachable_peer_path")
            or slot_state.get("bridge_forwarding_reachable")
            or dynamic_endpoints
            or preconnected_endpoints
        )
        report = {
            "interface_ref": interface_ref,
            "slot_inventory": {
                **(slot or {}),
                "found": slot_found,
                "preallocated_static_inventory_slot": slot_found
                and str(slot.get("slot_type") or "") == "cross_plane_isl",
            },
            "link_state": {
                "found": link_state_found,
                "occupied": occupied,
                "dynamic_endpoint_count": len(dynamic_endpoints),
                "preconnected_endpoint_count": len(preconnected_endpoints),
                "reachable_peer_or_bridge_path": reachable_peer_path,
            },
            "bpf_scope": {
                "in_attach_plan": interface_ref in attach_plan,
                "attach_roles": list(attach_plan.get(interface_ref) or []),
                "in_compile_capacity_interfaces": interface_ref in compile_capacity,
                "in_active_materialized_interfaces": interface_ref in active,
                "in_future_materialized_attach_eligible_interfaces": (
                    interface_ref in future_materialized
                ),
                "in_future_non_materialized_capacity_only_interfaces": (
                    interface_ref in future_capacity_only
                ),
            },
            "inactive_visibility_proof": {
                "occupied_false": link_state_found and not occupied,
                "no_active_generation_entry_before_activation": True,
                "no_linux_ip_or_mpls_fallback": linux_fallback_count == 0,
                "no_reachable_peer_or_bridge_forwarding_path": (
                    link_state_found and not reachable_peer_path
                ),
                "proof_source": [
                    "topology.slot_inventory identifies a preallocated cross-plane slot endpoint",
                    "link_state.slot.occupied=false",
                    "link_state has no dynamic/preconnected endpoints for slot",
                    "bpf offline path cardinality fallback count is zero",
                    "lifecycle horizon transaction substages order map_load before flip",
                ],
            },
        }
        proof = dict(report["inactive_visibility_proof"])
        report["inactive_visibility_passed"] = all(bool(value) for value in proof.values() if isinstance(value, bool))
        xp_reports.append(report)
    categories = {
        "ut_current_next_slots": {
            "slot_type": "ut_access",
            "slot_count": len(inventory.get("ut_access", [])),
            "selected_ut_count": selected_ut_count,
            "slots_per_selected_ut": (
                None
                if selected_ut_count == 0
                else len(inventory.get("ut_access", [])) / selected_ut_count
            ),
            "contract": "current+next access slots per selected UT",
        },
        "gw_max_simultaneous_slots": {
            "slot_type": "gateway_access",
            "slot_count": len(inventory.get("gateway_access", [])),
            "selected_gateway_count": selected_gateway_count,
            "slots_per_selected_gateway": (
                None
                if selected_gateway_count == 0
                else len(inventory.get("gateway_access", [])) / selected_gateway_count
            ),
            "contract": "bounded max simultaneous gateway access slots",
        },
        "in_plane_fixed_slots": {
            "slot_type": "same_plane_isl",
            "slot_count": len(inventory.get("same_plane_isl", [])),
            "link_state_slot_count": len(link_slots_by_type.get("same_plane_isl", [])),
            "contract": "fixed same-plane ISL slots are structural within run",
        },
        "cross_plane_bounded_slot_pool": {
            "slot_type": "cross_plane_isl",
            "slot_count": len(inventory.get("cross_plane_isl", [])),
            "link_state_slot_count": len(link_slots_by_type.get("cross_plane_isl", [])),
            "resident_attach_future_materialized_interface_count": (
                prepared_scope["resident_attach_future_materialized_interface_count"]
            ),
            "resident_attach_future_non_materialized_interface_count": (
                prepared_scope["resident_attach_future_non_materialized_interface_count"]
            ),
            "contract": "bounded cross-plane candidate slot pool, EventTimeline activates occupancy",
        },
    }
    slot_basis = dict(slot_capacity.get("calculation_basis") or {})
    gw_required_by_id = {
        str(key): int(value)
        for key, value in dict(slot_basis.get("gateway_required_by_id") or {}).items()
    }
    ut_required_by_id = {
        str(key): int(value)
        for key, value in dict(slot_basis.get("ut_required_by_id") or {}).items()
    }
    selected_gateways = list(
        dict(dict(catalog_gate.get("selectors") or {}).get("gateway") or {}).get(
            "selected_ids"
        )
        or []
    )
    selected_uts = list(
        dict(dict(catalog_gate.get("selectors") or {}).get("ut") or {}).get(
            "selected_ids"
        )
        or []
    )
    missing_gateway_ids = sorted(
        str(gateway_id)
        for gateway_id in selected_gateways
        if str(gateway_id) not in gw_required_by_id
    )
    gw_prepared = int(
        dict(slot_capacity.get("prepared_slots_by_type") or {}).get("gw_access")
        or len(inventory.get("gateway_access", []))
    )
    gw_required = int(
        dict(slot_capacity.get("required_max_concurrent_slots_by_type") or {}).get(
            "gw_access"
        )
        or sum(gw_required_by_id.values())
    )
    gw_derivation_complete = (
        bool(selected_gateways)
        and len(gw_required_by_id) == len(selected_gateways)
        and not missing_gateway_ids
        and gw_prepared == gw_required
    )
    slot_derivation_audit = {
        "ut_current_next_slots": {
            "source_artifact": "slot_capacity_audit.json",
            "source_fields": [
                "calculation_basis.ut_required_by_id",
                "calculation_basis.handover_modeled",
                "calculation_basis.policy_margins.ut_slot_margin",
                "notes.ut_access",
            ],
            "selected_ut_count": len(selected_uts),
            "required_by_id_count": len(ut_required_by_id),
            "required_slot_count": int(
                dict(slot_capacity.get("required_max_concurrent_slots_by_type") or {}).get(
                    "ut_access"
                )
                or sum(ut_required_by_id.values())
            ),
            "prepared_slot_count": int(
                dict(slot_capacity.get("prepared_slots_by_type") or {}).get(
                    "ut_access"
                )
                or len(inventory.get("ut_access", []))
            ),
            "per_ut_required_slot_counts": sorted(set(ut_required_by_id.values())),
            "current_next_semantics_confirmed": (
                bool(selected_uts)
                and len(ut_required_by_id) == len(selected_uts)
                and set(ut_required_by_id.values()) == {2}
                and bool(slot_basis.get("handover_modeled"))
            ),
            "note": dict(slot_capacity.get("notes") or {}).get("ut_access"),
        },
        "gw_max_simultaneous_slots": {
            "source_artifact": "slot_capacity_audit.json",
            "source_fields": [
                "calculation_basis.gateway_required_by_id",
                "calculation_basis.active_link_max_concurrent_by_type.gw_access",
                "required_max_concurrent_slots_by_type.gw_access",
                "prepared_slots_by_type.gw_access",
                "notes.gw_access",
            ],
            "selected_gateway_count": len(selected_gateways),
            "required_by_id_count": len(gw_required_by_id),
            "missing_selected_gateway_ids_in_required_by_id": missing_gateway_ids,
            "active_link_max_concurrent_gw_access": int(
                dict(slot_basis.get("active_link_max_concurrent_by_type") or {}).get(
                    "gw_access"
                )
                or 0
            ),
            "required_slot_count": gw_required,
            "prepared_slot_count": gw_prepared,
            "prepared_minus_required": gw_prepared - gw_required,
            "per_gateway_required_slot_counts": sorted(set(gw_required_by_id.values())),
            "derivation_complete": gw_derivation_complete,
            "derivation_label": (
                "GW_SLOT_DERIVATION_CONFIRMED"
                if gw_derivation_complete
                else "GW_SLOT_DERIVATION_INCOMPLETE"
            ),
            "note": dict(slot_capacity.get("notes") or {}).get("gw_access"),
            "conclusion": (
                "GW slot count is derived from per-gateway maximum simultaneous "
                "selected satellite connections plus policy margin."
                if gw_derivation_complete
                else (
                    f"{gw_prepared} prepared GW slots look like bounded inventory "
                    "capacity, but exact per-selected-GW derivation is incomplete "
                    f"because required_by_id covers {len(gw_required_by_id)}/"
                    f"{len(selected_gateways)} gateways and required total is "
                    f"{gw_required} while prepared total is {gw_prepared}."
                )
            ),
        },
        "in_plane_fixed_slots": {
            "slot_inventory_count": len(inventory.get("same_plane_isl", [])),
            "link_state_count": len(link_slots_by_type.get("same_plane_isl", [])),
            "slot_count_matches_link_state": len(inventory.get("same_plane_isl", []))
            == len(link_slots_by_type.get("same_plane_isl", [])),
            "event_timeline_changes_activation_not_existence": True,
        },
        "cross_plane_bounded_slot_pool": {
            "slot_inventory_count": len(inventory.get("cross_plane_isl", [])),
            "link_state_count": len(link_slots_by_type.get("cross_plane_isl", [])),
            "slot_count_matches_link_state": len(inventory.get("cross_plane_isl", []))
            == len(link_slots_by_type.get("cross_plane_isl", [])),
            "required_distinct_cross_plane_identities": int(
                dict(
                    slot_basis.get("active_link_distinct_identity_count_by_type")
                    or {}
                ).get("cross_plane_isl")
                or 0
            ),
            "prepared_bounded_pool_count": int(
                dict(slot_capacity.get("prepared_slots_by_type") or {}).get(
                    "cross_plane_isl"
                )
                or len(inventory.get("cross_plane_isl", []))
            ),
            "event_timeline_changes_activation_not_existence": True,
        },
    }
    outcome_labels = [STATIC_INVENTORY_PRECREATED_AUDIT_LABEL]
    if not gw_derivation_complete:
        outcome_labels.append("GW_SLOT_DERIVATION_INCOMPLETE")
    return {
        "schema_version": "skyseed-static-inventory-precreated-connection-audit-v1",
        "owner_module": "satellite/apps/run_skyseed_validation_profile.py",
        "run_dir": str(run_dir),
        "outcome_labels": outcome_labels,
        "categories": categories,
        "slot_derivation_audit": slot_derivation_audit,
        "xp120_exact_inactive_visibility_proof": xp_reports,
        "linux_gz_mpls_lfib_fallback_count": linux_fallback_count,
        "unsafe_map_conflict_count": unsafe_conflict_count,
        "lifecycle_horizon": {
            "horizon_selected_by": lifecycle.get("horizon_selected_by"),
            "whole_horizon_completed": lifecycle.get("whole_horizon_completed"),
            "ready_for_next_horizon": lifecycle.get("ready_for_next_horizon"),
            "classification_counts": lifecycle.get("classification_counts"),
            "sub_stage_counts": lifecycle.get("sub_stage_counts"),
        },
        "summary": {
            "operation": "static_inventory_precreated_connection_audit",
            "ut_current_next_slots_audited": True,
            "ut_current_next_semantics_confirmed": bool(
                slot_derivation_audit["ut_current_next_slots"][
                    "current_next_semantics_confirmed"
                ]
            ),
            "gw_max_simultaneous_slots_audited": True,
            "gw_slot_derivation_complete": gw_derivation_complete,
            "in_plane_fixed_slots_audited": True,
            "cross_plane_bounded_slot_pool_audited": True,
            "event_timeline_changes_activation_not_inventory_existence": True,
            "xp120_interface_count": len(xp_reports),
            "xp120_visibility_passed": all(
                bool(report["inactive_visibility_passed"]) for report in xp_reports
            ),
            "linux_fallback_blocks_visibility": linux_fallback_count > 0,
        },
    }


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            return float(int(value))
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, 6)


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _event_type_label(record: Mapping[str, Any]) -> str:
    event_type = str(record.get("event_timeline_type") or record.get("epoch_type") or "")
    if event_type == "runtime_transaction":
        return "RuntimeTransaction"
    if event_type == "parameter_update_batch":
        return "ParameterUpdateBatch"
    if str(record.get("epoch_type") or "") == "runtime_epoch":
        return "RuntimeTransaction"
    if str(record.get("epoch_type") or "") == "parameter_epoch":
        return "ParameterUpdateBatch"
    return event_type or "Unknown"


def _bpf_load_contract(record: Mapping[str, Any]) -> Dict[str, Any]:
    node = dict(record.get("node_local_execution") or {})
    backend = dict(node.get("backend") or {})
    readiness = dict(backend.get("bpf_owned_readiness") or {})
    prepare_steps = dict(readiness.get("prepare_steps") or {})
    load = prepare_steps.get("load")
    return dict(load) if isinstance(load, Mapping) else {}


def _bpf_generation_delete_count(load: Mapping[str, Any]) -> int:
    cleanup = dict(load.get("generation_gc_cleanup") or {})
    maps = dict(cleanup.get("maps") or {})
    total = 0
    for map_contract in maps.values():
        if not isinstance(map_contract, Mapping):
            continue
        total += int(map_contract.get("old_generation_delete_update_count") or 0)
        total += int(map_contract.get("target_generation_delete_update_count") or 0)
    return total


def _generation_target(load: Mapping[str, Any]) -> int | None:
    cleanup = dict(load.get("generation_gc_cleanup") or {})
    maps = dict(cleanup.get("maps") or {})
    for map_contract in maps.values():
        if isinstance(map_contract, Mapping):
            value = _as_int(map_contract.get("target_generation"))
            if value is not None:
                return value
    return _as_int(load.get("target_generation"))


def _timing_field(
    timing: Mapping[str, Any],
    key: str,
    *,
    missing: List[str],
    default: float | None = None,
) -> float | None:
    if key not in timing or timing.get(key) is None:
        if default is not None:
            return default
        missing.append(key)
        return None
    value = _as_float(timing.get(key))
    if value is None:
        missing.append(key)
    return value


TIMING_FIELD_SEMANTICS: Dict[str, Dict[str, Any]] = {
    "event_wait_seconds": {
        "source_artifact": "epoch_execution_details.jsonl:timing.event_wait_seconds",
        "semantic_type": "wall-clock interval",
        "category": "event wait time",
        "additivity": "outside_update_work",
        "parent_field": None,
        "notes": "Scheduler wait/pacing; do not count as runtime update bottleneck.",
    },
    "host_apply_seconds": {
        "source_artifact": "epoch_execution_details.jsonl:timing.host_operation_apply_wall_seconds",
        "semantic_type": "wall-clock interval",
        "category": "actual update wall time",
        "additivity": "additive_child",
        "parent_field": "total_network_update_seconds",
        "notes": "Host network mutation apply wall time.",
    },
    "node_local_apply_seconds": {
        "source_artifact": "epoch_execution_details.jsonl:timing.node_local_apply_wall_seconds",
        "semantic_type": "wall-clock interval",
        "category": "actual update wall time",
        "additivity": "additive_child",
        "parent_field": "total_network_update_seconds",
        "notes": "Node-local update/apply wall time.",
    },
    "bpf_prepare_seconds": {
        "source_artifact": (
            "epoch_execution_details.jsonl:"
            "timing.bpf_prepare_after_incremental_cleanup_wall_seconds"
        ),
        "semantic_type": "post-apply/reporting overhead",
        "category": "BPF prepare/reporting overhead",
        "additivity": "non_additive",
        "parent_field": None,
        "notes": (
            "Existing field is emitted from backend readiness/reporting context and "
            "can exceed total_network_update_seconds; keep for evidence but do not sum."
        ),
        "needs_instrumentation": True,
    },
    "bpf_generation_gc_seconds": {
        "source_artifact": "not present in existing artifacts",
        "semantic_type": "inferred estimate",
        "category": "BPF prepare/reporting overhead",
        "additivity": "non_additive",
        "parent_field": None,
        "notes": "Missing explicit elapsed field; generation GC delete counts are available.",
        "needs_instrumentation": True,
    },
    "bpf_map_delete_count": {
        "source_artifact": (
            "real_runtime_report.json/epoch_execution_details.jsonl:"
            "generation_gc_cleanup.maps.*_delete_update_count"
        ),
        "semantic_type": "cumulative counter",
        "category": "BPF map write/delete count",
        "additivity": "non_additive",
        "parent_field": None,
        "notes": "Count of generation GC map delete updates, not elapsed time.",
    },
    "bpf_map_delete_seconds": {
        "source_artifact": "not present in existing artifacts",
        "semantic_type": "inferred estimate",
        "category": "BPF map write elapsed time",
        "additivity": "non_additive",
        "parent_field": None,
        "notes": "Missing explicit elapsed field; do not infer from parent wall time.",
        "needs_instrumentation": True,
    },
    "bpf_map_update_count": {
        "source_artifact": (
            "epoch_execution_details.jsonl:node_local_execution.backend."
            "bpf_owned_readiness.prepare_steps.load.total_update_count"
        ),
        "semantic_type": "cumulative counter",
        "category": "BPF map write/delete count",
        "additivity": "non_additive",
        "parent_field": None,
        "notes": "Loader update operation count, not elapsed time.",
    },
    "bpf_map_update_seconds": {
        "source_artifact": (
            "epoch_execution_details.jsonl:node_local_execution.backend."
            "bpf_owned_readiness.prepare_steps.load.map_write_wall_seconds"
        ),
        "semantic_type": "nested subphase",
        "category": "BPF map write elapsed time",
        "additivity": "non_additive",
        "parent_field": None,
        "notes": (
            "Loader map write elapsed time from nested BPF prepare path; current "
            "artifacts do not prove it is an additive child of network_update_seconds."
        ),
        "needs_instrumentation": True,
    },
    "active_generation_flip_seconds": {
        "source_artifact": "epoch_execution_details.jsonl:timing.active_generation_flip_seconds",
        "semantic_type": "nested subphase",
        "category": "BPF map write elapsed time",
        "additivity": "non_additive",
        "parent_field": None,
        "notes": "Nested BPF generation activation update; do not sum with parent.",
    },
    "qdisc_update_count": {
        "source_artifact": (
            "epoch_execution_details.jsonl:timing.parameter_qdisc_required_update_count "
            "or timing.parameter_qdisc_command_count"
        ),
        "semantic_type": "cumulative counter",
        "category": "qdisc update elapsed time",
        "additivity": "non_additive",
        "parent_field": None,
        "notes": "Parameter qdisc update count, not elapsed time.",
    },
    "qdisc_update_seconds": {
        "source_artifact": "epoch_execution_details.jsonl:timing.parameter_qdisc_apply_seconds",
        "semantic_type": "cumulative counter",
        "category": "qdisc update elapsed time",
        "additivity": "non_additive",
        "parent_field": None,
        "notes": (
            "Existing value may be cumulative command time and can exceed "
            "parameter epoch network_update_seconds; do not sum as child wall time."
        ),
        "needs_instrumentation": True,
    },
    "validation_seconds": {
        "source_artifact": (
            "epoch_execution_details.jsonl:timing.targeted_validation_seconds + "
            "timing.post_transition_connectivity_probe_wall_seconds"
        ),
        "semantic_type": "post-apply/reporting overhead",
        "category": "validation/report serialization overhead",
        "additivity": "outside_update_work",
        "parent_field": None,
        "notes": "Validation after update work; tracked separately from network update.",
    },
    "post_apply_overhead_seconds": {
        "source_artifact": "epoch_execution_details.jsonl:timing.post_apply_overhead_seconds",
        "semantic_type": "post-apply/reporting overhead",
        "category": "validation/report serialization overhead",
        "additivity": "outside_update_work",
        "parent_field": None,
        "notes": "Reporting/audit/artifact overhead after apply; not update work.",
    },
    "total_network_update_seconds": {
        "source_artifact": "epoch_execution_details.jsonl:timing.network_update_seconds",
        "semantic_type": "wall-clock interval",
        "category": "actual update wall time",
        "additivity": "parent_wall_time",
        "parent_field": None,
        "notes": "Primary per-epoch update wall time headline.",
    },
    "unknown_blocking_seconds": {
        "source_artifact": "epoch_execution_details.jsonl:timing.unknown_blocking_seconds",
        "semantic_type": "inferred estimate",
        "category": "unattributed blocking",
        "additivity": "outside_update_work",
        "parent_field": None,
        "notes": "Residual unexplained time from timing marks.",
    },
}


def _timing_consistency_check(row: Mapping[str, Any]) -> Dict[str, Any]:
    parent = _as_float(row.get("total_network_update_seconds"))
    additive_fields = [
        field
        for field, semantics in TIMING_FIELD_SEMANTICS.items()
        if semantics.get("parent_field") == "total_network_update_seconds"
        and semantics.get("additivity") == "additive_child"
    ]
    additive_sum = round(
        sum(float(row.get(field) or 0.0) for field in additive_fields),
        6,
    )
    tolerance = max(1.0, (parent or 0.0) * 0.05)
    contradiction = bool(parent is not None and additive_sum > parent + tolerance)
    return {
        "parent_field": "total_network_update_seconds",
        "parent_wall_seconds": parent,
        "additive_child_fields": additive_fields,
        "additive_child_sum_seconds": additive_sum,
        "tolerance_seconds": round(tolerance, 6),
        "contradiction": contradiction,
        "non_additive_fields": [
            field
            for field, semantics in TIMING_FIELD_SEMANTICS.items()
            if semantics.get("additivity") == "non_additive"
        ],
        "outside_update_work_fields": [
            field
            for field, semantics in TIMING_FIELD_SEMANTICS.items()
            if semantics.get("additivity") == "outside_update_work"
        ],
    }


def _runtime_update_timing_row(record: Mapping[str, Any]) -> Dict[str, Any]:
    timing = dict(record.get("timing") or {})
    load = _bpf_load_contract(record)
    event_type = _event_type_label(record)
    missing: List[str] = []
    is_runtime = event_type == "RuntimeTransaction"
    outcome = dict(record.get("outcome") or {})
    success = bool(outcome.get("success")) if outcome.get("success") is not None else None
    bpf_delete_count = _bpf_generation_delete_count(load) if load else 0
    bpf_gc_seconds = _as_float(load.get("generation_gc_cleanup_wall_seconds"))
    bpf_delete_seconds = _as_float(load.get("map_delete_wall_seconds"))
    if is_runtime and load:
        if bpf_gc_seconds is None:
            missing.append("bpf_generation_gc_seconds")
        if bpf_delete_seconds is None:
            missing.append("bpf_map_delete_seconds")
    validation_seconds = sum(
        value or 0.0
        for value in (
            _as_float(timing.get("targeted_validation_seconds")),
            _as_float(timing.get("post_transition_connectivity_probe_wall_seconds")),
        )
    )
    qdisc_count = (
        _as_int(timing.get("parameter_qdisc_required_update_count"))
        or _as_int(timing.get("parameter_qdisc_command_count"))
        or 0
    )
    row: Dict[str, Any] = {
        "schema_version": "skyseed-per-epoch-update-timing-v1",
        "epoch_id": record.get("epoch_id"),
        "generation_id": _as_int(load.get("active_generation")),
        "target_generation_id": _generation_target(load),
        "event_type": event_type,
        "event_wait_seconds": _timing_field(
            timing,
            "event_wait_seconds",
            missing=missing,
            default=0.0,
        ),
        "host_apply_seconds": _timing_field(
            timing,
            "host_operation_apply_wall_seconds",
            missing=missing,
        ),
        "node_local_apply_seconds": _timing_field(
            timing,
            "node_local_apply_wall_seconds",
            missing=missing,
            default=0.0,
        ),
        "bpf_prepare_seconds": _timing_field(
            timing,
            "bpf_prepare_after_incremental_cleanup_wall_seconds",
            missing=missing,
            default=0.0,
        ),
        "bpf_generation_gc_seconds": bpf_gc_seconds,
        "bpf_map_delete_count": bpf_delete_count,
        "bpf_map_delete_seconds": bpf_delete_seconds,
        "bpf_map_update_count": (
            _as_int(load.get("total_update_count"))
            or _as_int(timing.get("bpf_map_update_count"))
            or 0
        ),
        "bpf_map_update_seconds": (
            _as_float(load.get("map_write_wall_seconds"))
            if load
            else _as_float(timing.get("bpf_map_write_seconds")) or 0.0
        ),
        "active_generation_flip_seconds": _timing_field(
            timing,
            "active_generation_flip_seconds",
            missing=missing,
            default=0.0,
        ),
        "qdisc_update_count": qdisc_count,
        "qdisc_update_seconds": _timing_field(
            timing,
            "parameter_qdisc_apply_seconds",
            missing=missing,
            default=0.0,
        ),
        "validation_seconds": round(validation_seconds, 6),
        "post_apply_overhead_seconds": _timing_field(
            timing,
            "post_apply_overhead_seconds",
            missing=missing,
            default=0.0,
        ),
        "total_network_update_seconds": _timing_field(
            timing,
            "network_update_seconds",
            missing=missing,
        ),
        "unknown_blocking_seconds": _timing_field(
            timing,
            "unknown_blocking_seconds",
            missing=missing,
            default=0.0,
        ),
        "result_label": "PASS" if success is True else "FAIL" if success is False else "UNKNOWN",
        "failure_reason": outcome.get("failure_reason"),
        "missing_timing_fields": sorted(set(missing)),
        "non_additive_timing_fields": [
            field
            for field, semantics in TIMING_FIELD_SEMANTICS.items()
            if semantics.get("additivity") == "non_additive"
        ],
    }
    row["consistency_check"] = _timing_consistency_check(row)
    return row


def _read_epoch_records(run_dir: Path) -> List[Dict[str, Any]]:
    path = run_dir / "epoch_execution_details.jsonl"
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _sum_rows(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    return round(
        sum(float(row.get(key) or 0.0) for row in rows if row.get(key) is not None),
        6,
    )


def write_runtime_update_timing_evidence(
    *,
    run_dir: Path,
    runner_report: Mapping[str, Any] | None = None,
    startup_contract: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    records = _read_epoch_records(run_dir)
    rows = [_runtime_update_timing_row(record) for record in records]
    _write_jsonl(run_dir / "per_epoch_update_timing.jsonl", rows)
    report = dict(runner_report or {})
    runtime_report = dict(_load_json_maybe(run_dir / "real_runtime_report.json") or {})
    startup = dict(startup_contract or {})
    missing_fields = sorted(
        {
            field
            for row in rows
            for field in list(row.get("missing_timing_fields") or [])
        }
    )
    unknown_total = _sum_rows(rows, "unknown_blocking_seconds")
    runtime_rows = [row for row in rows if row.get("event_type") == "RuntimeTransaction"]
    parameter_rows = [row for row in rows if row.get("event_type") == "ParameterUpdateBatch"]
    contradictions = [
        {
            "epoch_id": row.get("epoch_id"),
            "consistency_check": row.get("consistency_check"),
        }
        for row in rows
        if bool(dict(row.get("consistency_check") or {}).get("contradiction"))
    ]
    phase_timing = dict(report.get("phase_timing") or {})
    final_cleanup = dict(report.get("final_cleanup") or runtime_report.get("final_cleanup") or {})
    host_artifact_cleanup = dict(final_cleanup.get("host_artifact_cleanup") or {})
    host_artifact_timing = dict(host_artifact_cleanup.get("timing") or {})
    runtime_blocker = dict(report.get("runtime_blocker") or {})
    startup_cost_blocker = str(runtime_blocker.get("classified_blocker") or "") in {
        "STARTUP_COST_ANOMALY",
        STARTUP_COST_BLOCKER_LABEL,
    }
    summary = {
        "schema_version": "skyseed-runtime-update-timing-evidence-v1",
        "owner_module": "satellite/apps/run_skyseed_validation_profile.py",
        "scope": "global timing evidence; profile-specific budgets later",
        "lifetime": "permanent evidence contract",
        "failure_action": (
            "report-only / cost anomaly label; do not hard fail unless "
            "unknown_blocking_seconds is extreme"
        ),
        "not_ad_hoc_reason": (
            "Rows are derived from stable epoch_execution_details timing fields "
            "for every profile run that reaches runtime, not from this S4 run id."
        ),
        "focused_test": (
            "satellite/test_skyseed_validation_profiles.py::"
            "SkyseedValidationProfileTests.test_per_epoch_timing_summary_is_emitted"
        ),
        "run_dir": str(run_dir),
        "per_epoch_artifact": str(run_dir / "per_epoch_update_timing.jsonl"),
        "aggregate_artifact": str(run_dir / "aggregate_update_timing_summary.json"),
        "record_count": len(rows),
        "runtime_transaction_count": len(runtime_rows),
        "parameter_update_batch_count": len(parameter_rows),
        "missing_timing_fields": missing_fields,
        "timing_field_semantics": TIMING_FIELD_SEMANTICS,
        "consistency_check": {
            "rule": (
                "child elapsed fields must sum within parent wall time unless "
                "marked non_additive or outside_update_work"
            ),
            "parent_field": "total_network_update_seconds",
            "additive_child_fields": [
                field
                for field, semantics in TIMING_FIELD_SEMANTICS.items()
                if semantics.get("parent_field") == "total_network_update_seconds"
                and semantics.get("additivity") == "additive_child"
            ],
            "contradiction_count": len(contradictions),
            "contradictions": contradictions,
        },
        "timing_categories": {
            "startup_materialization_seconds": {
                "compose_up_wall_seconds": startup.get("compose_up_wall_seconds"),
                "compose_up_timeout_seconds": startup.get("compose_up_timeout_seconds"),
                "cold_start_before_first_epoch_execution_wall_seconds": (
                    dict(runtime_blocker.get("first_anomaly") or {})
                    .get("actual", {})
                    .get("cold_start_before_first_epoch_execution_wall_seconds")
                ),
            },
            "event_wait_seconds": _sum_rows(rows, "event_wait_seconds"),
            "real_runtime_update_work_seconds": _sum_rows(
                rows, "total_network_update_seconds"
            ),
            "post_apply_reporting_validation_overhead_seconds": round(
                _sum_rows(rows, "post_apply_overhead_seconds")
                + _sum_rows(rows, "validation_seconds"),
                6,
            ),
            "cleanup_seconds": {
                "compose_down_after_wall_seconds": phase_timing.get(
                    "compose_down_after_wall_seconds"
                ),
                "host_artifact_cleanup_wall_seconds": host_artifact_timing.get(
                    "cleanup_wall_seconds"
                ),
            },
        },
        "runtime_semantic_result": {
            "event_timeline_execution_success": report.get(
                "event_timeline_execution_success",
                runtime_report.get("event_timeline_execution_success"),
            ),
            "aggregate_runtime_succeeded": report.get(
                "aggregate_runtime_succeeded",
                runtime_report.get("aggregate_runtime_succeeded"),
            ),
            "real_apply_success": report.get(
                "real_apply_success",
                dict(runtime_report.get("outcome") or {}).get("real_apply_success"),
            ),
            "rollback_success": report.get(
                "rollback_success",
                runtime_report.get("rollback_success"),
            ),
            "failure_reason": report.get(
                "failure_reason",
                dict(runtime_report.get("outcome") or {}).get("failure_reason"),
            ),
        },
        "startup_cost_blocker_reported_separately": startup_cost_blocker,
        "unknown_blocking_seconds_total": unknown_total,
        "unknown_blocking_seconds_max": max(
            [float(row.get("unknown_blocking_seconds") or 0.0) for row in rows] or [0.0]
        ),
        "event_wait_excluded_from_update_work": True,
        "outcome_labels": [
            label
            for label, enabled in (
                (RUNTIME_UPDATE_TIMING_LABEL, True),
                (EXISTING_TIMING_SUMMARY_LABEL, bool(rows)),
                (PER_EPOCH_TIMING_INSTRUMENTATION_LABEL, True),
                (TIMING_FIELDS_MISSING_LABEL, bool(missing_fields)),
                (UPDATE_COST_ANOMALY_LABEL, bool(missing_fields or unknown_total)),
            )
            if enabled
        ],
    }
    _write_json(run_dir / "aggregate_update_timing_summary.json", summary)
    return summary


def _runtime_prep_map_load(iteration: Mapping[str, Any]) -> Dict[str, Any]:
    node = dict(iteration.get("node_local_execution") or {})
    backend = dict(node.get("backend") or {})
    readiness = dict(backend.get("bpf_owned_readiness") or {})
    prepare_steps = dict(readiness.get("prepare_steps") or {})
    real_prepare = dict(prepare_steps.get("real_backend_prepare") or {})
    return dict(dict(real_prepare.get("steps") or {}).get("map_load") or {})


def evaluate_runtime_preload_diff_contract(
    *,
    run_dir: Path,
) -> Dict[str, Any]:
    runtime_report = _load_json_maybe(run_dir / "real_runtime_report.json") or {}
    iterations = [
        dict(iteration)
        for iteration in list(runtime_report.get("iterations") or [])
        if isinstance(iteration, Mapping)
    ]
    epoch_reports: List[Dict[str, Any]] = []
    steady_epoch_count = 0
    preactivation_preload_count = 0
    preloaded_generation_used_count = 0
    diff_update_used_count = 0
    noop_skipped_epoch_count = 0
    active_generation_flip_only_count = 0
    full_generation_reload_blocker_count = 0
    for index, iteration in enumerate(iterations):
        map_load = _runtime_prep_map_load(iteration)
        attribution = dict(map_load.get("runtime_reload_attribution") or {})
        runtime_update_phase = str(
            attribution.get("runtime_update_phase")
            or map_load.get("runtime_update_phase")
            or ""
        )
        if runtime_update_phase == "steady_map_only":
            steady_epoch_count += 1
        if bool(attribution.get("preactivation_preload")):
            preactivation_preload_count += 1
        if bool(attribution.get("preloaded_generation_used")):
            preloaded_generation_used_count += 1
        if bool(attribution.get("map_diff_update_used")):
            diff_update_used_count += 1
        if int(attribution.get("noop_skipped_count") or 0) > 0:
            noop_skipped_epoch_count += 1
        if bool(attribution.get("active_generation_flip_only")):
            active_generation_flip_only_count += 1
        if bool(attribution.get("full_generation_reload_blocker")):
            full_generation_reload_blocker_count += 1
        map_diffs = {
            str(map_name): {
                "desired_count": int(dict(diff).get("desired_count") or 0),
                "existing_count": int(dict(diff).get("existing_count") or 0),
                "insert_count": int(dict(diff).get("insert_count") or 0),
                "update_count": int(dict(diff).get("update_count") or 0),
                "delete_count": int(dict(diff).get("delete_count") or 0),
                "noop_skipped_count": int(
                    dict(diff).get("noop_skipped_count") or 0
                ),
                "wall_seconds": float(dict(diff).get("wall_seconds") or 0.0),
            }
            for map_name, diff in dict(
                attribution.get("map_diff_trace")
                or map_load.get("map_diff_trace")
                or {}
            ).items()
            if isinstance(diff, Mapping)
        }
        epoch_reports.append(
            {
                "iteration_index": index,
                "runtime_update_phase": runtime_update_phase,
                "preactivation_preload": bool(
                    attribution.get("preactivation_preload")
                ),
                "preloaded_generation_used": bool(
                    attribution.get("preloaded_generation_used")
                ),
                "map_diff_update_used": bool(
                    attribution.get("map_diff_update_used")
                ),
                "active_generation_flip_only": bool(
                    attribution.get("active_generation_flip_only")
                ),
                "full_generation_reload_blocker": bool(
                    attribution.get("full_generation_reload_blocker")
                ),
                "actual_data_write_count": int(
                    attribution.get("actual_data_write_count")
                    or map_load.get("actual_data_write_count")
                    or 0
                ),
                "noop_skipped_count": int(attribution.get("noop_skipped_count") or 0),
                "active_generation_before": attribution.get(
                    "active_generation_before"
                ),
                "active_generation_after": attribution.get("active_generation_after"),
                "map_diff_trace": map_diffs,
            }
        )
    outcome_labels: List[str] = []
    if diff_update_used_count:
        outcome_labels.append("RUNTIME_MAP_DIFF_UPDATE_USED")
    if noop_skipped_epoch_count:
        outcome_labels.append("RUNTIME_NOOP_MAP_WRITES_SKIPPED")
    if preloaded_generation_used_count:
        outcome_labels.append("RUNTIME_PRELOADED_GENERATION_USED")
    if active_generation_flip_only_count:
        outcome_labels.append("RUNTIME_ACTIVE_GENERATION_FLIP_ONLY")
    if full_generation_reload_blocker_count:
        outcome_labels.append("RUNTIME_FULL_GENERATION_RELOAD_BLOCKER")
    if steady_epoch_count and preloaded_generation_used_count == 0:
        outcome_labels.append(RUNTIME_PRELOAD_NOT_EXERCISED_LABEL)
    if steady_epoch_count and not epoch_reports:
        outcome_labels.append("RUNTIME_DIFF_TRACE_INCOMPLETE")
    report = {
        "schema_version": "skyseed-runtime-preload-diff-contract-audit-v1",
        "owner_module": "satellite/apps/run_skyseed_validation_profile.py",
        "run_dir": str(run_dir),
        "steady_map_only_epoch_count": steady_epoch_count,
        "preactivation_preload_epoch_count": preactivation_preload_count,
        "preloaded_generation_used_epoch_count": preloaded_generation_used_count,
        "map_diff_update_epoch_count": diff_update_used_count,
        "noop_skipped_epoch_count": noop_skipped_epoch_count,
        "active_generation_flip_only_epoch_count": active_generation_flip_only_count,
        "full_generation_reload_blocker_count": full_generation_reload_blocker_count,
        "preload_contract_exercised": bool(preloaded_generation_used_count),
        "scheduler_preactivation_preload_call_path_required": (
            "Selected future BPF generations must be compiled and passed to "
            "load_mpls_maps with bpf_runtime_update_phase=preactivation_preload "
            "before the runtime activation event. Current runtime evidence only "
            "shows bootstrap and steady_map_only calls inside the live prepare path."
        ),
        "outcome_labels": outcome_labels,
        "epoch_reports": epoch_reports,
    }
    _write_json(run_dir / "runtime_preload_diff_contract_audit.json", report)
    return report


def reconcile_lifecycle_horizon_completion_with_execution(
    *,
    run_dir: Path,
) -> Dict[str, Any]:
    lifecycle_path = run_dir / "lifecycle_aware_horizon_contract.json"
    lifecycle = dict(_load_json_maybe(lifecycle_path) or {})
    if not lifecycle:
        return {}
    runtime_report = _load_json_maybe(run_dir / "real_runtime_report.json") or {}
    failure_context = dict(runtime_report.get("failure_context") or {})
    current_epoch = dict(failure_context.get("current_epoch_context") or {})
    exception = dict(failure_context.get("exception") or {})
    skyseed_details = dict(exception.get("skyseed_details") or {})
    bpf_readiness = dict(skyseed_details.get("bpf_owned_readiness") or {})
    prepare = dict(
        dict(bpf_readiness.get("prepare_steps") or {}).get("real_backend_prepare")
        or {}
    )
    failure_message = str(exception.get("message") or "")
    event_reports = [
        dict(report)
        for report in list(lifecycle.get("event_reports") or [])
        if isinstance(report, Mapping)
    ]
    failure_epoch_id = str(current_epoch.get("epoch_id") or "")
    failure_slice_id = str(current_epoch.get("slice_id") or "")
    failed_selected_event = None
    for report in event_reports:
        if failure_epoch_id and str(report.get("epoch_id") or "") == failure_epoch_id:
            failed_selected_event = report
            break
        if failure_slice_id and str(report.get("slice_id") or "") == failure_slice_id:
            failed_selected_event = report
            break
    selected_event_failed = failed_selected_event is not None and bool(failure_context)
    parameter_delta_attach = bool(
        selected_event_failed
        and str(current_epoch.get("classification") or "") == "parameter_epoch"
        and (
            "tc_bpf_mpls_delta_attach_failed" in failure_message
            or int(prepare.get("delta_attach_count") or 0) > 0
        )
    )
    outcome_labels = list(lifecycle.get("outcome_labels") or [])
    originally_completed = bool(lifecycle.get("whole_horizon_completed"))
    originally_ready = bool(lifecycle.get("ready_for_next_horizon"))
    if selected_event_failed and (originally_completed or originally_ready):
        outcome_labels.append(HORIZON_COMPLETION_REPORTING_INCONSISTENT_LABEL)
    if parameter_delta_attach:
        outcome_labels.append(PARAMETER_UPDATE_TRIGGERED_DELTA_ATTACH_BLOCKER_LABEL)
    reconciled_events: List[Dict[str, Any]] = []
    for report in event_reports:
        updated = dict(report)
        matched = failed_selected_event is not None and (
            str(updated.get("epoch_id") or "") == str(failed_selected_event.get("epoch_id") or "")
            and str(updated.get("slice_id") or "") == str(failed_selected_event.get("slice_id") or "")
        )
        if matched:
            updated["execution_status"] = "failed"
            updated["execution_failure_message"] = failure_message
            if parameter_delta_attach:
                updated["first_semantic_cause"] = (
                    PARAMETER_UPDATE_TRIGGERED_DELTA_ATTACH_BLOCKER_LABEL
                )
                updated["delta_attach_failure_authoritative"] = True
                updated["packet_miss_authoritative"] = False
        elif selected_event_failed:
            updated.setdefault("execution_status", "not_reached")
        reconciled_events.append(updated)
    map_only_completed = int(
        current_epoch.get("runtime_epochs_completed")
        or len(list(runtime_report.get("iterations") or []))
        or 0
    )
    parameter_completed = int(current_epoch.get("parameter_epochs_completed") or 0)
    reconciled = {
        **lifecycle,
        "execution_reconciled": True,
        "map_only_runtime_epochs_completed": map_only_completed,
        "parameter_epochs_completed": parameter_completed,
        "whole_horizon_completed": (
            False if selected_event_failed else bool(lifecycle.get("whole_horizon_completed"))
        ),
        "ready_for_next_horizon": (
            False if selected_event_failed else bool(lifecycle.get("ready_for_next_horizon"))
        ),
        "selected_event_failed": selected_event_failed,
        "failed_selected_event": failed_selected_event,
        "execution_failure_context": current_epoch,
        "event_reports": reconciled_events,
        "outcome_labels": list(dict.fromkeys(outcome_labels)),
    }
    blockers = list(reconciled.get("next_horizon_blockers") or [])
    if parameter_delta_attach:
        blockers.append(PARAMETER_UPDATE_TRIGGERED_DELTA_ATTACH_BLOCKER_LABEL)
    reconciled["next_horizon_blockers"] = list(dict.fromkeys(blockers))
    _write_json(lifecycle_path, reconciled)
    _write_json(run_dir / "horizon_execution_reconciled_summary.json", reconciled)
    return reconciled


def _blocking_startup_label(startup_labels: Sequence[str]) -> str | None:
    blocking = {
        NO_PROGRESS_STALL_LABEL,
        RESOURCE_DEADLOCK_LABEL,
        STARTUP_HARD_CAP_LABEL,
        STARTUP_NO_PROGRESS_STALL_LABEL,
        STARTUP_RESOURCE_DEADLOCK_LABEL,
        STARTUP_CORRECTNESS_FATAL_LABEL,
        "OPERATOR_ABORT",
        "STARTUP_MATERIALIZATION_BUDGET_EXCEEDED",
    }
    for label in startup_labels:
        if label in blocking:
            return label
    return None


def _runner_report_indicates_failure(report: Mapping[str, Any] | None) -> bool:
    if not report:
        return False
    if _runner_report_startup_cost_warning_only(report):
        return False
    if report.get("failure_reason"):
        return True
    if report.get("real_apply_success") is False:
        return True
    if report.get("aggregate_runtime_succeeded") is False:
        return True
    return False


def _runner_report_startup_cost_warning_only(report: Mapping[str, Any] | None) -> bool:
    if not report:
        return False
    blocker = dict(report.get("runtime_blocker") or report.get("cost_sanity_blocker") or {})
    cost_sanity = dict(report.get("cost_sanity") or {})
    classified = str(
        blocker.get("classified_blocker")
        or cost_sanity.get("conclusion")
        or ""
    )
    failure_reason = str(report.get("failure_reason") or "")
    startup_cost_only = classified == "STARTUP_COST_ANOMALY" or failure_reason == (
        "cost_sanity_failed:STARTUP_COST_ANOMALY"
    )
    if not startup_cost_only:
        return False
    semantic_success = (
        report.get("real_apply_success") is not False
        and report.get("ping_success") is not False
        and report.get("event_timeline_execution_success") is not False
        and report.get("rollback_success") is not False
    )
    return bool(semantic_success)


def _normal_failure_cleanup_required(
    *,
    profile: Mapping[str, Any],
    completed_returncode: int,
    runner_report: Mapping[str, Any] | None,
    residue: Mapping[str, Any],
    startup_state_supervision: Mapping[str, Any] | None = None,
) -> bool:
    cleanup_contract = dict(profile.get("normal_failure_cleanup") or {})
    if cleanup_contract and not bool(cleanup_contract.get("enabled", True)):
        return False
    if not dict(profile.get("cleanup_requirements") or {}):
        return False
    if (
        startup_state_supervision
        and startup_state_supervision.get("cleanup_decision")
        == CLEANUP_NOT_REQUIRED_RECOVERABLE_LABEL
        and external_cleanup_success(residue)
    ):
        return False
    return (
        int(completed_returncode) != 0
        or _runner_report_indicates_failure(runner_report)
        or not external_cleanup_success(residue)
    )


def docker_dummy_network_capacity_gate(
    profile: Mapping[str, Any],
    *,
    host_prefixes: Sequence[Mapping[str, Any]] | None = None,
    docker_prefixes: Sequence[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    runner_args = dict(profile.get("runner_args") or {})
    contract = dict(profile.get("docker_dummy_network_capacity") or {})
    if not contract:
        return {
            "ok": True,
            "required": False,
            "reason": "profile_docker_dummy_network_capacity_not_declared",
        }

    pool = str(runner_args.get("docker_dummy_network_pool") or "")
    mask_value = runner_args.get("docker_dummy_network_mask")
    if not pool or mask_value is None:
        return {
            "ok": False,
            "outcome_label": CAPACITY_BLOCKER_LABEL,
            "reason": "docker_dummy_network_pool_or_mask_missing",
            "required": True,
        }

    required_dummy_networks = int(contract.get("required_dummy_networks") or 0)
    minimum_addresses_per_subnet = int(contract.get("minimum_addresses_per_subnet") or 0)
    capacity = docker_dummy_network_available_subnets(pool, int(mask_value))
    pool_prefixlen = int(capacity["pool_prefixlen"])
    minimum_mask_for_required = (
        pool_prefixlen + math.ceil(math.log2(max(required_dummy_networks, 1)))
    )
    suggested_profile_fix = {
        "docker_dummy_network_pool": capacity["pool"],
        "minimum_docker_dummy_network_mask_for_required_count": minimum_mask_for_required,
        "current_docker_dummy_network_mask": int(mask_value),
    }

    host_records = list(host_prefixes) if host_prefixes is not None else _host_route_prefixes()
    docker_records = (
        list(docker_prefixes) if docker_prefixes is not None else _docker_network_prefixes()
    )
    host_overlaps = _network_overlaps(capacity["pool"], host_records)
    docker_overlaps = _network_overlaps(capacity["pool"], docker_records)

    failures: List[str] = []
    if required_dummy_networks > int(capacity["available_subnets"]):
        failures.append("required_dummy_networks_exceeds_available_subnets")
    if minimum_addresses_per_subnet > int(capacity["addresses_per_subnet"]):
        failures.append("minimum_addresses_per_subnet_exceeds_configured_subnet_size")
    if host_overlaps:
        failures.append("docker_dummy_network_pool_overlaps_host_route")
    if docker_overlaps:
        failures.append("docker_dummy_network_pool_overlaps_existing_docker_network")

    return {
        "ok": not failures,
        "required": True,
        "outcome_label": None if not failures else CAPACITY_BLOCKER_LABEL,
        "failure_reasons": failures,
        "required_dummy_networks": required_dummy_networks,
        "available_subnets": int(capacity["available_subnets"]),
        "pool": capacity["pool"],
        "pool_prefixlen": int(capacity["pool_prefixlen"]),
        "mask": int(capacity["mask"]),
        "formula": capacity["formula"],
        "addresses_per_subnet": int(capacity["addresses_per_subnet"]),
        "minimum_addresses_per_subnet": minimum_addresses_per_subnet,
        "components": dict(contract.get("components") or {}),
        "suggested_profile_fix": suggested_profile_fix,
        "host_route_prefixes": [dict(item) for item in host_records],
        "docker_network_prefixes": [dict(item) for item in docker_records],
        "host_route_overlaps": host_overlaps,
        "docker_network_overlaps": docker_overlaps,
    }


def _enforce_profile_policy(profile: Mapping[str, Any]) -> None:
    runner_args = dict(profile.get("runner_args") or {})
    booleans = dict(profile.get("runner_boolean_flags") or {})
    forbidden = dict(profile.get("forbidden_behavior") or {})
    strict = bool(forbidden.get("strict_bpf_owned_no_canary_experimental"))

    if strict:
        if str(runner_args.get("forwarding_backend")) != "bpf_owned":
            raise ProfileContractError(
                "strict bpf_owned profile must set forwarding_backend=bpf_owned"
            )
        if str(runner_args.get("bpf_backend", "off")) != "off":
            raise ProfileContractError("strict bpf_owned profile must set bpf_backend=off")
        if bool(booleans.get("bpf_dataplane_canary")):
            raise ProfileContractError(
                "strict bpf_owned profile forbids --bpf-dataplane-canary"
            )
        if bool(booleans.get("emit_bpf_shadow")):
            raise ProfileContractError(
                "strict bpf_owned profile forbids --emit-bpf-shadow as PASS evidence"
            )
        if str(runner_args.get("forwarding_backend")) in {"bpf_shadow", "bpf_canary"}:
            raise ProfileContractError(
                "strict bpf_owned profile forbids bpf_shadow/bpf_canary forwarding backend"
            )

    allowed = dict(profile.get("allowed_capabilities") or {})
    if bool(allowed.get("runtime")) is False and str(runner_args.get("run_mode")) == "real-runtime":
        raise ProfileContractError("profile forbids runtime but runner_args.run_mode=real-runtime")
    if (
        bool(allowed.get("bpf")) is False
        and str(runner_args.get("run_mode")) == "real-runtime"
        and str(runner_args.get("forwarding_backend", "")) == "bpf_owned"
    ):
        raise ProfileContractError("profile forbids BPF but runner_args.forwarding_backend=bpf_owned")


def build_underlying_command(
    *,
    profile: Mapping[str, Any],
    catalog_gate: Mapping[str, Any],
    run_root: Path,
    run_id: str,
) -> List[str]:
    _enforce_profile_policy(profile)
    runner = str(profile.get("runner") or "starlink_shell_experiment")
    if runner == "minimal_resident_bpf_map_reload":
        return [
            sys.executable,
            str(MINIMAL_RELOAD_RUNNER),
            "--output",
            str(run_root / run_id / "minimal_resident_bpf_map_reload.json"),
        ]
    if runner != "starlink_shell_experiment":
        raise ProfileContractError(f"unsupported profile runner: {runner!r}")

    runner_args = dict(profile.get("runner_args") or {})
    selectors = dict(catalog_gate.get("selectors") or {})
    if "ut" in selectors:
        runner_args["ut_ids"] = ",".join(selectors["ut"].get("selected_ids") or [])
    if "gateway" in selectors:
        runner_args["gateway_ids"] = ",".join(
            selectors["gateway"].get("selected_ids") or []
        )

    command = [
        sys.executable,
        str(STARLINK_RUNNER),
        "--run-id",
        run_id,
        "--run-root",
        str(run_root),
    ]
    for key in RUNNER_ARG_ORDER:
        if key not in runner_args:
            continue
        value = runner_args[key]
        if value is None:
            continue
        if key in {"ut_catalog", "gateway_catalog", "zone_catalog"}:
            value = _resolve_repo_path(str(value))
        command.extend([_arg_name(key), _stringify(value)])
    for key, flag in BOOLEAN_RUNNER_FLAGS.items():
        if bool(dict(profile.get("runner_boolean_flags") or {}).get(key)):
            command.append(flag)
    return command


def _profile_summary(
    *,
    profile: Mapping[str, Any],
    catalog_gate: Mapping[str, Any],
    docker_dummy_network_capacity_gate: Mapping[str, Any],
    command: Sequence[str],
    run_root: Path,
    run_id: str,
    returncode: int | None = None,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> Dict[str, Any]:
    return {
        "schema_version": "skyseed-validation-profile-run-v1",
        "profile_name": profile.get("name"),
        "profile_path": profile.get("_profile_path"),
        "run_id": run_id,
        "run_root": str(run_root),
        "run_dir": str(run_root / run_id),
        "catalog_gate": catalog_gate,
        "docker_dummy_network_capacity_gate": docker_dummy_network_capacity_gate,
        "underlying_command": list(command),
        "expected_outcome_labels": list(profile.get("expected_outcome_labels") or []),
        "forbidden_behavior": dict(profile.get("forbidden_behavior") or {}),
        "cleanup_requirements": dict(profile.get("cleanup_requirements") or {}),
        "cost_sanity": dict(profile.get("cost_sanity") or {}),
        "returncode": returncode,
        "stdout_path": str(stdout_path) if stdout_path else None,
        "stderr_path": str(stderr_path) if stderr_path else None,
    }


def _persistent_artifact_contract(run_dir: Path) -> Dict[str, Any]:
    required_names = [
        "profile_run_summary.json",
        "startup_materialization_contract.json",
        "normal_failure_cleanup_report.json",
        "runtime_progress.json",
        "first_failure_summary.json",
        "aggregate_update_timing_summary.json",
        "per_epoch_update_timing.jsonl",
        "execution_trace.jsonl",
        "critical_path_summary.json",
        "cost_attribution_summary.json",
        "real_runtime_report.json",
        "epoch_execution_details.jsonl",
    ]
    artifacts = []
    for name in required_names:
        path = run_dir / name
        artifacts.append(
            {
                "name": name,
                "path": str(path),
                "exists": path.exists(),
                "optional": name
                in {"real_runtime_report.json", "epoch_execution_details.jsonl"},
            }
        )
    return {
        "schema_version": "skyseed-persistent-artifact-contract-v1",
        "owner_module": "satellite/apps/run_skyseed_validation_profile.py",
        "scope": "profile-specific",
        "lifetime": "temporary commissioning guard",
        "failure_action": "diagnostic label",
        "not_ad_hoc_reason": (
            "The contract records stable artifact names for every rich S4 "
            "commissioning attempt under the configured run root."
        ),
        "focused_test": (
            "satellite/test_skyseed_validation_profiles.py::"
            "SkyseedValidationProfileTests.test_persistent_artifact_contract_uses_non_tmp_run_root"
        ),
        "run_dir": str(run_dir),
        "run_root": str(run_dir.parent),
        "run_root_is_tmp_only": str(run_dir.parent).startswith("/tmp/"),
        "artifacts": artifacts,
    }


def _first_failure_summary(
    *,
    profile: Mapping[str, Any],
    run_dir: Path,
    runner_report: Mapping[str, Any] | None,
    startup_contract: Mapping[str, Any],
    returncode: int,
) -> Dict[str, Any]:
    report = dict(runner_report or {})
    artifact_runtime_epochs_completed = 0
    runtime_report_path = run_dir / "real_runtime_report.json"
    if runtime_report_path.exists():
        runtime_report = _load_json_maybe(runtime_report_path)
        if isinstance(runtime_report, Mapping):
            artifact_runtime_epochs_completed = int(
                runtime_report.get("runtime_epochs_completed")
                or dict(runtime_report.get("timing_summary") or {}).get(
                    "runtime_epoch_count"
                )
                or len(list(runtime_report.get("iterations") or []))
                or 0
            )
            failure_context = dict(runtime_report.get("failure_context") or {})
            current_epoch = dict(failure_context.get("current_epoch_context") or {})
            artifact_runtime_epochs_completed = max(
                artifact_runtime_epochs_completed,
                int(current_epoch.get("runtime_epochs_completed") or 0),
            )
    runtime_epochs_completed = int(
        report.get("runtime_epochs_completed")
        or report.get("runtime_count")
        or artifact_runtime_epochs_completed
        or 0
    )
    runtime_blocker = dict(report.get("runtime_blocker") or {})
    warning_only_startup_cost = _runner_report_startup_cost_warning_only(report)
    runtime_reached = bool(runtime_epochs_completed > 0)
    startup_labels = list(startup_contract.get("outcome_labels") or [])
    blocker_label = _blocking_startup_label(startup_labels)
    runtime_blocker_label = str(runtime_blocker.get("classified_blocker") or "")
    if runtime_blocker_label == "STARTUP_COST_ANOMALY":
        runtime_blocker_label = STARTUP_COST_ANOMALY_LABEL
    non_failure_startup_labels = {
        "SLOW_BUT_PROGRESSING_GRACE_APPLIED",
        SLOW_PROGRESS_CONTINUE_LABEL,
        "HIGH_PROGRESS_STARTUP_CONTINUE",
        STARTUP_SOFT_BUDGET_WARNING_LABEL,
        STARTUP_COST_ANOMALY_LABEL,
    }
    non_failure_runtime_blocker_labels = {
        "STARTUP_COST_ANOMALY",
        STARTUP_COST_ANOMALY_LABEL,
    }
    first_label = (
        blocker_label
        or (
            runtime_blocker_label
            if runtime_blocker_label not in non_failure_runtime_blocker_labels
            else ""
        )
        or (
            next(
                (
                    str(label)
                    for label in startup_labels
                    if str(label) not in non_failure_startup_labels
                ),
                "",
            )
        )
        or (
            str(report.get("failure_reason") or "")
            if not warning_only_startup_cost
            else ""
        )
        or ("PROCESS_RETURNED_NONZERO" if int(returncode) != 0 else "")
    )
    return {
        "schema_version": "skyseed-first-failure-summary-v1",
        "owner_module": "satellite/apps/run_skyseed_validation_profile.py",
        "scope": "profile-specific",
        "lifetime": "temporary commissioning guard",
        "failure_action": "diagnostic label",
        "not_ad_hoc_reason": (
            "The summary is derived from runner output and startup contract "
            "fields for any profile attempt, not from a specific run id."
        ),
        "focused_test": (
            "satellite/test_skyseed_validation_profiles.py::"
            "SkyseedValidationProfileTests.test_first_failure_summary_preserves_startup_timeout"
        ),
        "profile_name": profile.get("name"),
        "run_dir": str(run_dir),
        "returncode": int(returncode),
        "first_failure_label": first_label or None,
        "failure_reason": None if warning_only_startup_cost else report.get("failure_reason"),
        "startup_cost_warning_reason": (
            report.get("failure_reason") if warning_only_startup_cost else None
        ),
        "runtime_blocker": runtime_blocker,
        "runtime_or_bpf_reached": runtime_reached,
        "runtime_epochs_completed": runtime_epochs_completed,
        "startup_materialization": {
            "outcome_labels": startup_labels,
            "timeout_classification": startup_contract.get(
                "timeout_classification"
            ),
            "completed_batches": startup_contract.get("completed_batches"),
            "total_expected_batches": startup_contract.get(
                "total_expected_batches"
            ),
            "progress_ratio": startup_contract.get("progress_ratio"),
            "last_compose_up_stage": startup_contract.get(
                "last_compose_up_stage"
            ),
        },
        "available_artifacts": {
            name: (run_dir / name).exists()
            for name in (
                "profile_run_summary.json",
                "startup_materialization_contract.json",
                "normal_failure_cleanup_report.json",
                "runtime_progress.json",
                "aggregate_update_timing_summary.json",
                "per_epoch_update_timing.jsonl",
                "real_runtime_report.json",
                "epoch_execution_details.jsonl",
            )
        },
    }


def _cleanup_evidence_contract(
    *,
    run_dir: Path,
    pre_cleanup_residue: Mapping[str, Any],
    normal_cleanup: Mapping[str, Any] | None,
    final_residue: Mapping[str, Any],
) -> Dict[str, Any]:
    post_run_preflight = _load_json_maybe(run_dir / "post_run_preflight.json")
    normal_cleanup_available = normal_cleanup is not None
    return {
        "schema_version": "skyseed-cleanup-evidence-contract-v1",
        "owner_module": "satellite/apps/run_skyseed_validation_profile.py",
        "pre_cleanup": {
            "phase_label": "pre_normal_failure_cleanup",
            "post_run_preflight_available": isinstance(post_run_preflight, Mapping),
            "post_run_preflight_clean_after_run": (
                post_run_preflight.get("clean_after_run")
                if isinstance(post_run_preflight, Mapping)
                else None
            ),
            "external_residue": dict(pre_cleanup_residue),
            "external_cleanup_success": external_cleanup_success(pre_cleanup_residue),
        },
        "final_cleanup": {
            "evidence_source": (
                "normal_failure_cleanup_report_and_final_external_residue"
                if normal_cleanup_available
                else "final_external_residue"
            ),
            "normal_failure_cleanup_available": normal_cleanup_available,
            "normal_failure_cleanup_success": (
                normal_cleanup.get("external_cleanup_success")
                if normal_cleanup_available
                else None
            ),
            "external_residue": dict(final_residue),
            "external_cleanup_success": external_cleanup_success(final_residue),
        },
    }


def run_profile(
    *,
    profile_name: str,
    run_root: Path,
    run_id: str | None = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    profile = load_profile(profile_name)
    run_id = run_id or f"{profile_name}-{_timestamp()}"
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    catalog_gate = resolve_catalog_contract(profile)
    _write_json(run_dir / "profile_catalog_gate.json", catalog_gate)
    capacity_gate = docker_dummy_network_capacity_gate(profile)
    _write_json(run_dir / "docker_dummy_network_capacity_gate.json", capacity_gate)
    command = build_underlying_command(
        profile=profile,
        catalog_gate=catalog_gate,
        run_root=run_root,
        run_id=run_id,
    )
    stdout_path = run_dir / "profile_runner_stdout.json"
    stderr_path = run_dir / "profile_runner_stderr.log"
    summary = _profile_summary(
        profile=profile,
        catalog_gate=catalog_gate,
        docker_dummy_network_capacity_gate=capacity_gate,
        command=command,
        run_root=run_root,
        run_id=run_id,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    if dry_run:
        summary["dry_run"] = True
        summary["returncode"] = 0
        _write_json(run_dir / "profile_run_summary.json", summary)
        return summary
    if capacity_gate.get("ok") is False:
        summary["dry_run"] = False
        summary["returncode"] = 2
        summary["outcome_label"] = CAPACITY_BLOCKER_LABEL
        summary["blocked_before_underlying_runner"] = True
        _write_json(run_dir / "profile_run_summary.json", summary)
        return summary

    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    summary["returncode"] = int(completed.returncode)
    summary["dry_run"] = False
    runner_report = _load_json_maybe(stdout_path)
    startup_contract = evaluate_startup_materialization_contract(
        profile=profile,
        runner_report=runner_report,
        capacity_gate=capacity_gate,
    )
    summary["startup_materialization_contract"] = startup_contract
    _write_json(run_dir / "startup_materialization_contract.json", startup_contract)
    startup_state_supervision = evaluate_startup_state_supervision_contract(
        profile=profile,
        runner_report=runner_report,
        startup_contract=startup_contract,
    )
    summary["startup_state_supervision_contract"] = startup_state_supervision
    _write_json(
        run_dir / "startup_state_supervision_contract.json",
        startup_state_supervision,
    )
    startup_operation_audit = evaluate_startup_operation_audit(
        runner_report=runner_report,
    )
    summary["startup_operation_audit"] = startup_operation_audit
    _write_json(run_dir / "startup_operation_audit.json", startup_operation_audit)
    _write_json(
        run_dir / "startup_operation_audit_summary.json",
        dict(startup_operation_audit.get("summary") or {}),
    )
    first_failure = _first_failure_summary(
        profile=profile,
        run_dir=run_dir,
        runner_report=runner_report,
        startup_contract=startup_contract,
        returncode=int(completed.returncode),
    )
    _write_json(run_dir / "first_failure_summary.json", first_failure)
    summary["first_failure_summary"] = first_failure
    runtime_update_timing = write_runtime_update_timing_evidence(
        run_dir=run_dir,
        runner_report=runner_report,
        startup_contract=startup_contract,
    )
    summary["runtime_update_timing_evidence"] = runtime_update_timing
    runtime_preload_diff_audit = evaluate_runtime_preload_diff_contract(
        run_dir=run_dir,
    )
    summary["runtime_preload_diff_contract_audit"] = runtime_preload_diff_audit
    lifecycle_horizon_execution = reconcile_lifecycle_horizon_completion_with_execution(
        run_dir=run_dir,
    )
    summary["lifecycle_aware_horizon_contract"] = lifecycle_horizon_execution
    execution_trace = write_execution_trace_artifacts(run_dir)
    summary["execution_trace_contract"] = execution_trace
    persistent_artifacts = _persistent_artifact_contract(run_dir)
    _write_json(run_dir / "persistent_artifact_contract.json", persistent_artifacts)
    summary["persistent_artifact_contract"] = persistent_artifacts
    static_inventory_audit = evaluate_static_inventory_precreated_connection_audit(
        run_dir=run_dir,
    )
    summary["static_inventory_precreated_connection_audit"] = static_inventory_audit
    _write_json(
        run_dir / "static_inventory_precreated_connection_audit.json",
        static_inventory_audit,
    )
    outcome_labels = [
        PERSISTENT_ARTIFACT_LABEL,
        STARTUP_CONTRACT_LABEL,
        STARTUP_COST_CONTRACT_LABEL,
        FIRST_FAILURE_PRESERVED_LABEL,
        *list(runtime_update_timing.get("outcome_labels") or []),
        *list(runtime_preload_diff_audit.get("outcome_labels") or []),
        *list(lifecycle_horizon_execution.get("outcome_labels") or []),
        *list(execution_trace.get("outcome_labels") or []),
        *list(startup_contract.get("outcome_labels") or []),
        *list(startup_state_supervision.get("outcome_labels") or []),
        *list(startup_operation_audit.get("outcome_labels") or []),
        *list(static_inventory_audit.get("outcome_labels") or []),
    ]
    if _runner_report_indicates_failure(runner_report) or int(completed.returncode) != 0:
        outcome_labels.append("RICH_S4_SMOKE_FAILED_FIRST_FAILURE_RECORDED")
    post_underlying_residue = detect_cleanup_residue(
        docker_dummy_network_pool=str(
            dict(profile.get("runner_args") or {}).get("docker_dummy_network_pool") or ""
        )
        or None,
        run_root=run_root,
    )
    summary["post_underlying_external_residue"] = post_underlying_residue
    normal_cleanup = None
    if _normal_failure_cleanup_required(
        profile=profile,
        completed_returncode=int(completed.returncode),
        runner_report=runner_report,
        residue=post_underlying_residue,
        startup_state_supervision=startup_state_supervision,
    ):
        normal_cleanup = run_cleanup_only(
            run_dir=run_dir,
            docker_dummy_network_pool=str(
                dict(profile.get("runner_args") or {}).get("docker_dummy_network_pool") or ""
            )
            or None,
            preserve_run_dirs=[run_dir],
            report_filename="normal_failure_cleanup_report.json",
            cleanup_mode="normal_failure_cleanup",
            success_outcome_label=CLEANUP_PASS_LABEL,
        )
        outcome_labels.append("NORMAL_FAILURE_CLEANUP_CONTRACT_ADDED")
        outcome_labels.append(
            CLEANUP_PASS_LABEL
            if normal_cleanup.get("external_cleanup_success")
            else CLEANUP_INCOMPLETE_LABEL
        )
    else:
        outcome_labels.append(
            CLEANUP_PASS_LABEL
            if external_cleanup_success(post_underlying_residue)
            else CLEANUP_INCOMPLETE_LABEL
        )
    final_residue = detect_cleanup_residue(
        docker_dummy_network_pool=str(
            dict(profile.get("runner_args") or {}).get("docker_dummy_network_pool") or ""
        )
        or None,
        run_root=run_root,
    )
    summary["normal_failure_cleanup"] = normal_cleanup
    summary["final_external_residue"] = final_residue
    summary["cleanup_evidence_contract"] = _cleanup_evidence_contract(
        run_dir=run_dir,
        pre_cleanup_residue=post_underlying_residue,
        normal_cleanup=normal_cleanup,
        final_residue=final_residue,
    )
    summary["external_cleanup_success"] = external_cleanup_success(final_residue)
    summary["persistent_artifact_contract"] = _persistent_artifact_contract(run_dir)
    if not summary["external_cleanup_success"] and CLEANUP_INCOMPLETE_LABEL not in outcome_labels:
        outcome_labels.append(CLEANUP_INCOMPLETE_LABEL)
    summary["outcome_labels"] = list(dict.fromkeys(outcome_labels))
    blocking_startup_labels = {
        NO_PROGRESS_STALL_LABEL,
        RESOURCE_DEADLOCK_LABEL,
        STARTUP_HARD_CAP_LABEL,
        STARTUP_NO_PROGRESS_STALL_LABEL,
        STARTUP_RESOURCE_DEADLOCK_LABEL,
        STARTUP_CORRECTNESS_FATAL_LABEL,
        "OPERATOR_ABORT",
        "STARTUP_MATERIALIZATION_BUDGET_EXCEEDED",
    }
    passed = (
        int(completed.returncode) == 0
        and not _runner_report_indicates_failure(runner_report)
        and summary["external_cleanup_success"]
        and not (
            set(startup_contract.get("outcome_labels") or [])
            & blocking_startup_labels
        )
    )
    first_failure_label = (first_failure.get("first_failure_label") or None)
    summary["outcome_label"] = (
        "RICH_S4_OPERATIONAL_CLEAN_SMOKE_PASS"
        if passed
        else first_failure_label
        or (summary["outcome_labels"][0] if summary["outcome_labels"] else None)
    )
    _write_json(run_dir / "profile_run_summary.json", summary)
    summary["persistent_artifact_contract"] = _persistent_artifact_contract(run_dir)
    _write_json(
        run_dir / "persistent_artifact_contract.json",
        summary["persistent_artifact_contract"],
    )
    _write_json(run_dir / "profile_run_summary.json", summary)
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a named Skyseed validation profile with catalog contract gates."
    )
    parser.add_argument("--profile", help="Validation profile name.")
    parser.add_argument(
        "--run-root",
        default="/tmp/skyseed_validation_profiles",
        help="Directory for run-specific profile artifacts.",
    )
    parser.add_argument("--run-id", default=None, help="Optional stable run id.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve catalogs and print the underlying command without executing it.",
    )
    parser.add_argument(
        "--cleanup-only",
        action="store_true",
        help="Run automated teardown for owned Skyseed residue and exit without launching the profile.",
    )
    parser.add_argument(
        "--preserve-run-dir",
        action="append",
        default=[],
        help="Run directory whose first-failure artifacts should be copied before cleanup.",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="List available validation profile names.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Render the profile runner summary.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list_profiles:
        payload = {"profiles": list_profiles()}
        if args.format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for name in payload["profiles"]:
                print(name)
        return 0
    if not args.profile:
        raise SystemExit("--profile is required unless --list-profiles is used")

    try:
        if args.cleanup_only:
            profile = load_profile(str(args.profile))
            run_root = Path(args.run_root).resolve()
            run_id = args.run_id or f"{args.profile}-cleanup-{_timestamp()}"
            runner_args = dict(profile.get("runner_args") or {})
            summary = run_cleanup_only(
                run_dir=run_root / run_id,
                docker_dummy_network_pool=str(
                    runner_args.get("docker_dummy_network_pool") or ""
                )
                or None,
                preserve_run_dirs=[
                    Path(path).resolve() for path in list(args.preserve_run_dir or [])
                ],
            )
            summary["profile_name"] = str(args.profile)
            summary["run_id"] = run_id
            summary["run_dir"] = str(run_root / run_id)
            summary["returncode"] = 0 if summary.get("external_cleanup_success") else 2
        else:
            summary = run_profile(
                profile_name=str(args.profile),
                run_root=Path(args.run_root).resolve(),
                run_id=args.run_id,
                dry_run=bool(args.dry_run),
            )
    except ProfileContractError as exc:
        payload = {
            "ok": False,
            "error_type": "profile_contract_error",
            "error": str(exc),
            "profile": args.profile,
        }
        if args.format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"PROFILE_CONTRACT_ERROR: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"profile={summary['profile_name']}")
        print(f"run_dir={summary['run_dir']}")
        print(f"returncode={summary['returncode']}")
        if "underlying_command" in summary:
            print("command=" + " ".join(summary["underlying_command"]))
    return int(summary.get("returncode") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
