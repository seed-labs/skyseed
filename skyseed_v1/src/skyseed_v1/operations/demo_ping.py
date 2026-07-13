"""Human-facing ping probe for an active Skyseed example runtime."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import time
from typing import Any

from skyseed_v1.configuration.models import ExperimentConfig
from skyseed_v1.operations.paths import PROJECT_ROOT, PROJECT_RUNTIME_ROOT


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _active_run(config: ExperimentConfig) -> tuple[Path, dict[str, Any]]:
    lock = PROJECT_RUNTIME_ROOT / "skyseed_real_runtime_active.lock"
    metadata = _load_json(lock)
    run_id = str(metadata.get("run_id") or "")
    if not run_id:
        raise RuntimeError("no active Skyseed runtime; start this example first")
    run_dir = config.run_root / run_id
    if not run_dir.is_dir():
        raise RuntimeError(
            f"active runtime is {run_id}, but it does not belong to {config.example_name}"
        )
    return run_dir, metadata


def latest_ping_target(path: Path, ut_id: str | None = None) -> dict[str, Any] | None:
    """Return the newest authoritative observer snapshot, never a superseded one."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("superseded") is True or item.get("observation_status") != "valid":
            continue
        if ut_id and str(item.get("ut_id")) != ut_id:
            continue
        if item.get("ut_id") and item.get("target_ip"):
            return item
    return None


def _slice_id(run_dir: Path) -> str:
    progress = _load_json(run_dir / "runtime_progress.json")
    context = progress.get("current_epoch_context") or {}
    return str(context.get("slice_id") or "unknown")


def _catalog(path: str) -> list[dict[str, Any]]:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = PROJECT_ROOT / resolved
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"expected a list catalog: {resolved}")
    return [item for item in value if isinstance(item, dict)]


def static_ping_targets(config: ExperimentConfig, ut_id: str | None) -> list[dict[str, Any]]:
    """Resolve immutable policy rules into concrete gateway service addresses."""
    selected_ut = ut_id or str(config.ground.ut_ids[0])
    if selected_ut not in config.ground.ut_ids:
        raise ValueError(f"UT {selected_ut!r} is not selected by this example")
    policies = {
        str(item.get("ut_id")): item
        for item in _catalog(config.ground.ut_gateway_policy_catalog)
    }
    bindings = {
        str(item.get("gateway_id")): item
        for item in _catalog(config.ground.gateway_binding_catalog)
    }
    policy = policies.get(selected_ut)
    if policy is None:
        raise ValueError(f"no gateway policy exists for {selected_ut}")
    targets = []
    for rule in policy.get("rules", []):
        gateway_id = str(rule.get("gateway_id") or "")
        binding = bindings.get(gateway_id)
        if binding is None:
            raise ValueError(f"policy references unbound gateway {gateway_id}")
        target_ip = str(binding.get("gateway_service_ipv4") or "").split("/", 1)[0]
        if not target_ip:
            raise ValueError(f"gateway {gateway_id} has no service address")
        targets.append(
            {
                "ut_id": selected_ut,
                "selected_gateway_id": gateway_id,
                "target_ip": target_ip,
                "destination_prefix": rule.get("prefix"),
                "rule_role": rule.get("rule_role"),
            }
        )
    if not targets:
        raise ValueError(f"gateway policy for {selected_ut} contains no rules")
    return targets


def _container_running(name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _ping_once(target: dict[str, Any], count: int) -> tuple[int, str, str]:
    command = [
        "docker", "exec", str(target["ut_id"]), "ping", "-n", "-c", str(count),
        "-W", "2", str(target["target_ip"]),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result.returncode, result.stdout, result.stderr


def _summary(stdout: str) -> str:
    loss = re.search(r"([0-9.]+)% packet loss", stdout)
    rtt = re.search(r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = ([^\n]+)", stdout)
    parts = []
    if loss:
        parts.append(f"loss={loss.group(1)}%")
    if rtt:
        values = rtt.group(1).split()[0].split("/")
        if len(values) >= 3:
            parts.append(f"rtt_min/avg/max={values[0]}/{values[1]}/{values[2]} ms")
    return " ".join(parts) or "no ping summary returned"


def run_demo_ping(
    config: ExperimentConfig,
    *,
    ut_id: str | None = None,
    count: int = 3,
    wait_seconds: float = 5.0,
    watch: bool = False,
    watch_interval: float = 5.0,
) -> int:
    """Probe immutable UT policy targets while an example runtime is active."""
    try:
        run_dir, _ = _active_run(config)
    except RuntimeError as exc:
        print(f"[demo-ping] unavailable: {exc}")
        return 2
    try:
        targets = static_ping_targets(config, ut_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[demo-ping] invalid example policy: {exc}")
        return 2
    selected_ut = str(targets[0]["ut_id"])
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while not _container_running(selected_ut) and time.monotonic() < deadline:
        print(f"[demo-ping] runtime is active; waiting for UT container {selected_ut} ...", flush=True)
        time.sleep(min(2.0, max(0.1, deadline - time.monotonic())))
    if not _container_running(selected_ut):
        print(
            f"[demo-ping] UT container {selected_ut} is not running yet; "
            "wait for compose startup and retry (or use --wait-seconds 60)."
        )
        return 2

    while True:
        failed = False
        for target in targets:
            before = _slice_id(run_dir)
            observed = latest_ping_target(
                run_dir / "runtime_ping_observation.jsonl", selected_ut
            )
            satellite = (
                observed.get("serving_satellite_id")
                if observed and observed.get("selected_gateway_id") == target["selected_gateway_id"]
                else "unavailable"
            )
            print(
                "[demo-ping] probing "
                f"UT={target['ut_id']} -> GW={target['selected_gateway_id']} "
                f"IP={target['target_ip']} rule={target.get('rule_role')} "
                f"prefix={target.get('destination_prefix')} recent_observed_SAT={satellite} "
                f"current_slice={before}",
                flush=True,
            )
            returncode, stdout, stderr = _ping_once(target, max(1, count))
            after = _slice_id(run_dir)
            print(f"[demo-ping] {'PASS' if returncode == 0 else 'FAIL'} {_summary(stdout)}")
            failed = failed or returncode != 0
            if after != before:
                print(
                    f"[demo-ping] note: runtime advanced {before} -> {after} during the probe; "
                    "the result spans a route transition."
                )
            if returncode != 0 and stderr.strip():
                print(f"[demo-ping] stderr: {stderr.strip()}")
        if not watch:
            return 1 if failed else 0
        time.sleep(max(0.2, watch_interval))
