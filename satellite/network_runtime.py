#!/usr/bin/env python3
# encoding: utf-8

"""Host-runtime execution for Iteration 2 NetworkMutationPlan transitions."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import shlex
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence

from contracts import NetworkMutationPlan
from controller import SatelliteController
from network_mutation import dynamic_slot_state_from_link_state


BRIDGE_NETFILTER_SYSCTLS = (
    "net.bridge.bridge-nf-call-iptables",
    "net.bridge.bridge-nf-call-ip6tables",
    "net.bridge.bridge-nf-call-arptables",
)

DEFAULT_HOST_COMMAND_TIMEOUT_SECONDS = 120.0
DOCKER_INSPECT_TIMEOUT_SECONDS = 30.0
RECONCILE_PROGRESS_MIN_SLOT_DELTA = 8
RECONCILE_PROGRESS_MIN_INTERVAL_SECONDS = 15.0


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


def _short_name(prefix: str, seed: str) -> str:
    return f"{prefix}{_fingerprint({'seed': seed})[:11]}"[:15]


def _stable_local_mac(seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    octets = [0x02, digest[0], digest[1], digest[2], digest[3], digest[4]]
    return ":".join(f"{octet:02x}" for octet in octets)


def dynamic_slot_binding_resource_key(
    slot_type: str,
    binding: Mapping[str, Any],
) -> str:
    if slot_type in {"ut_access", "gw_access"}:
        return f"satellite:{binding.get('satellite_id')}"
    normalized = {
        "satellite_a_direction": binding.get("satellite_a_direction"),
        "satellite_a_id": binding.get("satellite_a_id"),
        "satellite_b_direction": binding.get("satellite_b_direction"),
        "satellite_b_id": binding.get("satellite_b_id"),
    }
    return _fingerprint(normalized)


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _command_timeout_seconds(command: Sequence[str]) -> float:
    normalized = [str(part) for part in command]
    if normalized[:2] == ["docker", "inspect"]:
        return DOCKER_INSPECT_TIMEOUT_SECONDS
    return DEFAULT_HOST_COMMAND_TIMEOUT_SECONDS


def _clone_slot_state(state: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {
        slot_name: {
            "slot_name": str(slot_entry.get("slot_name", slot_name)),
            "slot_type": str(slot_entry.get("slot_type", "unknown_dynamic_slot")),
            "occupied": bool(slot_entry.get("occupied", False)),
            "binding": dict(slot_entry.get("binding", {}) or {}),
            "endpoints": [dict(endpoint) for endpoint in slot_entry.get("endpoints", [])],
            "runtime_parameters": dict(slot_entry.get("runtime_parameters", {}) or {}),
        }
        for slot_name, slot_entry in state.items()
    }


def _slot_summary(slot_state: Dict[str, Any] | None) -> Dict[str, Any]:
    return {
        "occupied": bool((slot_state or {}).get("occupied", False)),
        "binding": dict((slot_state or {}).get("binding") or {}),
        "runtime_parameters": dict((slot_state or {}).get("runtime_parameters") or {}),
    }


def _expected_summary(operation: Dict[str, Any], *, source: bool) -> Dict[str, Any]:
    return {
        "occupied": bool(operation.get("source_occupied" if source else "target_occupied", False)),
        "binding": dict(operation.get("source_binding" if source else "target_binding", {}) or {}),
        "runtime_parameters": dict(
            operation.get(
                "source_runtime_parameters" if source else "target_runtime_parameters",
                {},
            )
            or {}
        ),
    }


def _inverse_operation(operation: Dict[str, Any]) -> Dict[str, Any]:
    inverse_name = str(operation.get("operation"))
    if inverse_name == "activate_slot_attachment":
        inverse_name = "deactivate_slot_attachment"
    elif inverse_name == "deactivate_slot_attachment":
        inverse_name = "activate_slot_attachment"
    return {
        **dict(operation),
        "operation": inverse_name,
        "source_binding": dict(operation.get("target_binding", {}) or {}),
        "target_binding": dict(operation.get("source_binding", {}) or {}),
        "source_occupied": bool(operation.get("target_occupied", False)),
        "target_occupied": bool(operation.get("source_occupied", False)),
        "source_endpoints": [dict(endpoint) for endpoint in operation.get("target_endpoints", [])],
        "target_endpoints": [dict(endpoint) for endpoint in operation.get("source_endpoints", [])],
        "source_runtime_parameters": dict(
            operation.get("target_runtime_parameters", {}) or {}
        ),
        "target_runtime_parameters": dict(
            operation.get("source_runtime_parameters", {}) or {}
        ),
    }


def _state_entry_from_operation(operation: Dict[str, Any], *, source: bool) -> Dict[str, Any]:
    return {
        "slot_name": str(operation.get("slot_name")),
        "slot_type": str(operation.get("slot_type")),
        "occupied": bool(operation.get("source_occupied" if source else "target_occupied", False)),
        "binding": dict(
            operation.get("source_binding" if source else "target_binding", {}) or {}
        ),
        "endpoints": [
            dict(endpoint)
            for endpoint in operation.get(
                "source_endpoints" if source else "target_endpoints",
                [],
            )
        ],
        "runtime_parameters": dict(
            operation.get(
                "source_runtime_parameters" if source else "target_runtime_parameters",
                {},
            )
            or {}
        ),
    }


def _slot_outcome_from_operation(slot_name: str, operation: Dict[str, Any] | None) -> Dict[str, Any]:
    if operation is None:
        return {
            "slot_name": slot_name,
            "category": "skipped",
            "change_class": "unchanged_endpoint",
            "reused": True,
        }
    operation_name = str(operation.get("operation"))
    if operation_name == "activate_slot_attachment":
        category = "created"
        change_class = "activate_only"
    elif operation_name == "deactivate_slot_attachment":
        category = "deleted"
        change_class = "deactivate_only"
    elif operation_name == "update_slot_parameters":
        category = "changed"
        change_class = "parameters_only"
    else:
        category = "changed"
        change_class = "endpoint_rebind"
    return {
        "slot_name": slot_name,
        "category": category,
        "change_class": change_class,
        "operation": operation_name,
        "reused": bool(operation.get("delta_hints", {}).get("reuses_slot_runtime", True)),
        "endpoint_unchanged": bool(
            operation.get("delta_hints", {}).get("endpoint_unchanged", False)
        ),
    }


def _operation_owner_keys(operation: Dict[str, Any]) -> set[str]:
    owner_keys: set[str] = set()
    owner_keys.add(f"slot:{operation.get('slot_name')}")
    for endpoint in [
        *list(operation.get("source_endpoints", []) or []),
        *list(operation.get("target_endpoints", []) or []),
    ]:
        node_id = str(endpoint.get("node_id", "")).strip()
        interface_name = str(endpoint.get("interface_name", "")).strip()
        if node_id and interface_name:
            owner_keys.add(f"iface:{node_id}:{interface_name}")
        logical_direction = str(endpoint.get("logical_direction", "")).strip()
        if node_id and logical_direction:
            owner_keys.add(f"dir:{node_id}:{logical_direction}")
    for binding in (
        dict(operation.get("source_binding", {}) or {}),
        dict(operation.get("target_binding", {}) or {}),
    ):
        if binding.get("satellite_id"):
            owner_keys.add(f"slot-binding:{operation.get('slot_name')}:{binding['satellite_id']}")
        if binding.get("ut_id"):
            owner_keys.add(f"ut-slot:{binding['ut_id']}:{operation.get('slot_name')}")
        if binding.get("gateway_id"):
            owner_keys.add(f"gw-slot:{binding['gateway_id']}:{operation.get('slot_name')}")
        if binding.get("satellite_a_id") and binding.get("satellite_a_direction"):
            owner_keys.add(
                f"dir:{binding['satellite_a_id']}:{binding['satellite_a_direction']}"
            )
        if binding.get("satellite_b_id") and binding.get("satellite_b_direction"):
            owner_keys.add(
                f"dir:{binding['satellite_b_id']}:{binding['satellite_b_direction']}"
            )
    if not owner_keys:
        owner_keys.add(f"slot:{operation.get('slot_name')}")
    return owner_keys


def _parallel_group_summary(
    *,
    group_index: int,
    operation_indexes: Sequence[int],
    operations: Sequence[Dict[str, Any]],
    wall_seconds: float,
) -> Dict[str, Any]:
    selected_operations = [operations[index] for index in operation_indexes]
    operation_counts: Dict[str, int] = {}
    slot_type_counts: Dict[str, int] = {}
    for operation in selected_operations:
        operation_name = str(operation.get("operation"))
        slot_type_name = str(operation.get("slot_type", "unknown"))
        operation_counts[operation_name] = operation_counts.get(operation_name, 0) + 1
        slot_type_counts[slot_type_name] = slot_type_counts.get(slot_type_name, 0) + 1
    return {
        "group_index": group_index,
        "operation_indexes": list(operation_indexes),
        "operation_count": len(selected_operations),
        "parallelized": len(selected_operations) > 1,
        "operation_counts": operation_counts,
        "slot_type_counts": slot_type_counts,
        "wall_seconds": round(float(wall_seconds), 6),
    }


def _summarize_operation_results(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    operation_counts: Dict[str, int] = {}
    per_operation_wall_seconds: Dict[str, float] = {}
    delta_category_counts: Dict[str, int] = {}
    slot_type_counts: Dict[str, int] = {}
    per_slot_type_wall_seconds: Dict[str, float] = {}
    total_wall_seconds = 0.0
    for result in results:
        operation_name = str(result.get("operation"))
        wall_seconds = float(result.get("wall_seconds", 0.0))
        category_name = str(result.get("delta_category", "changed"))
        slot_type_name = str(result.get("slot_type", "unknown"))
        operation_counts[operation_name] = operation_counts.get(operation_name, 0) + 1
        per_operation_wall_seconds[operation_name] = (
            per_operation_wall_seconds.get(operation_name, 0.0) + wall_seconds
        )
        delta_category_counts[category_name] = delta_category_counts.get(category_name, 0) + 1
        slot_type_counts[slot_type_name] = slot_type_counts.get(slot_type_name, 0) + 1
        per_slot_type_wall_seconds[slot_type_name] = (
            per_slot_type_wall_seconds.get(slot_type_name, 0.0) + wall_seconds
        )
        total_wall_seconds += wall_seconds
    return {
        "operation_count": len(results),
        "operation_counts": operation_counts,
        "delta_category_counts": delta_category_counts,
        "slot_type_counts": slot_type_counts,
        "per_operation_wall_seconds": {
            name: round(seconds, 3)
            for name, seconds in sorted(per_operation_wall_seconds.items())
        },
        "per_slot_type_wall_seconds": {
            name: round(seconds, 3)
            for name, seconds in sorted(per_slot_type_wall_seconds.items())
        },
        "total_wall_seconds": round(total_wall_seconds, 3),
    }


@dataclass(frozen=True)
class HostRuntimeEnvelope:
    envelope_id: str
    mutation_id: str
    topology_path: str
    link_state_path: str
    target_scope: Dict[str, Any]
    managed_slot_names: List[str]
    observed_state: Dict[str, Dict[str, Any]]
    target_state: Dict[str, Dict[str, Any]]
    operations: List[Dict[str, Any]]
    parallelism: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NetworkMutationApplyResult:
    execution_id: str
    boundary: Dict[str, Any]
    target_scope: Dict[str, Any]
    preflight: Dict[str, Any]
    envelope: Dict[str, Any]
    apply_results: List[Dict[str, Any]]
    apply_verification: Dict[str, Any]
    outcome: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NetworkMutationRollbackResult:
    execution_id: str
    boundary: Dict[str, Any]
    rollback_results: List[Dict[str, Any]]
    rollback_verification: Dict[str, Any]
    outcome: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class NetworkMutationExecutionError(RuntimeError):
    """Raised when a real network-mutation transition fails validation or apply."""


class ControllerBackedSlotLinkNamespaceQdiscInstaller:
    """Transitional installer that still lands on the controller public boundary."""

    installer_name = "controller_backed_slot_link_namespace_qdisc_installer"
    installer_kind = "transitional_host_runtime_installer"
    installer_version = "0.2.0"
    execution_transport = "transitional_controller_public_boundary"
    supports_bounded_parallelism = False
    supports_batched_link_state_persist = False

    def __init__(self) -> None:
        self._last_setup_timing: Dict[str, float] = {
            "runtime_prepare_wall_seconds": 0.0,
            "runtime_reconcile_wall_seconds": 0.0,
        }
        self._last_setup_summary: Dict[str, Any] | None = None
        self._last_cleanup_summary: Dict[str, Any] | None = None
        self._pending_prewarm_reconcile_signature: Dict[str, Any] | None = None
        self._last_cleanup_summary: Dict[str, Any] | None = None

    def controller(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        reconcile_slot_names: Sequence[str] | None = None,
        prepared_resource_scope_by_slot: Mapping[str, Sequence[str] | set[str]] | None = None,
        record_prewarm_reconcile: bool = False,
        consume_matching_prewarm: bool = False,
        reconcile_mode: str = "apply",
        setup_progress_callback: Callable[[Dict[str, Any]], None] | None = None,
    ) -> SatelliteController:
        args = argparse.Namespace(
            topology_config=str(topology_path),
            link_state=str(link_state_path),
            fast_runtime_apply=True,
        )
        return SatelliteController(args)

    def consume_setup_timing(self) -> Dict[str, float]:
        return dict(self._last_setup_timing)

    def last_setup_summary(self) -> Dict[str, Any] | None:
        return (
            None
            if self._last_setup_summary is None
            else dict(self._last_setup_summary)
        )

    def public_runtime_summary(self) -> Dict[str, Any]:
        return {
            "installer_runtime_prepared": None,
            "prepared_bridge_count": None,
            "prepared_candidate_resource_count": None,
            "prepared_candidate_endpoint_count": None,
        }

    def state_by_name(self, *, link_state_path: Path) -> Dict[str, Dict[str, Any]]:
        return dynamic_slot_state_from_link_state(_load_json(link_state_path))

    def _persist_slot_runtime_parameters(
        self,
        *,
        link_state_path: Path,
        slot_name: str,
        runtime_parameters: Dict[str, Any],
    ) -> None:
        link_state = _load_json(link_state_path)
        for slot_entry in link_state.get("slots", []):
            if str(slot_entry.get("slot_name")) != slot_name:
                continue
            slot_entry["runtime_parameters"] = dict(runtime_parameters)
            _write_json(link_state_path, link_state)
            return
        raise NetworkMutationExecutionError(
            f"unable to persist host runtime parameters for unknown slot {slot_name}"
        )

    def bootstrap_runtime_if_needed(
        self,
        *,
        controller: SatelliteController,
        link_state_path: Path,
        failure_reason: str,
        reconcile_slot_names: Sequence[str] | None = None,
    ) -> Dict[str, Any] | None:
        bootstrap_markers = (
            "rerun --init-slot-runtime",
            "slot bridge missing",
            "slot runtime is not initialized",
            "preconnected endpoint validation failed",
        )
        if not any(marker in failure_reason for marker in bootstrap_markers):
            return None
        captured_stdout = io.StringIO()
        with contextlib.redirect_stdout(captured_stdout):
            initialized_state = controller.initialize_slot_runtime()
        refreshed_state = self.state_by_name(link_state_path=link_state_path)
        return {
            "trigger": failure_reason,
            "action": "initialize_slot_runtime",
            "slot_count": len(initialized_state.get("slots", [])),
            "controller_stdout": captured_stdout.getvalue().strip(),
            "post_bootstrap_runtime_initialized": bool(
                _load_json(link_state_path).get("runtime_initialized", False)
            ),
            "post_bootstrap_slot_count": len(refreshed_state),
        }

    def apply_operation(
        self,
        *,
        controller: SatelliteController,
        link_state_path: Path,
        operation: Dict[str, Any],
        state_by_name: Dict[str, Dict[str, Any]] | None = None,
        persist_link_state: bool = True,
    ) -> Dict[str, Any]:
        slot_name = str(operation.get("slot_name"))
        slot_type = str(operation.get("slot_type"))
        operation_name = str(operation.get("operation"))
        source_binding = dict(operation.get("source_binding", {}) or {})
        target_binding = dict(operation.get("target_binding", {}) or {})
        target_runtime_parameters = dict(
            operation.get("target_runtime_parameters", {}) or {}
        )
        captured_stdout = io.StringIO()
        started_at = time.perf_counter()
        qdisc_apply_wall_seconds = 0.0

        with contextlib.redirect_stdout(captured_stdout):
            if operation_name == "update_slot_parameters":
                pass
            elif slot_type == "ut_access":
                ut_id = str((target_binding or source_binding).get("ut_id"))
                if operation_name in {"deactivate_slot_attachment", "rebind_slot_attachment"}:
                    controller.detach_ut(ut_id, requested_slot_name=slot_name)
                if operation_name in {"activate_slot_attachment", "rebind_slot_attachment"}:
                    controller.attach_ut(
                        f"{ut_id}:{target_binding['satellite_id']}",
                        requested_slot_name=slot_name,
                    )
            elif slot_type == "gw_access":
                gateway_id = str((target_binding or source_binding).get("gateway_id"))
                if operation_name in {"deactivate_slot_attachment", "rebind_slot_attachment"}:
                    controller.detach_gateway(gateway_id, requested_slot_name=slot_name)
                if operation_name in {"activate_slot_attachment", "rebind_slot_attachment"}:
                    controller.attach_gateway(
                        f"{gateway_id}:{target_binding['satellite_id']}",
                        requested_slot_name=slot_name,
                    )
            elif slot_type == "cross_plane_isl":
                if operation_name in {"deactivate_slot_attachment", "rebind_slot_attachment"}:
                    controller.release_slot(slot_name)
                if operation_name in {"activate_slot_attachment", "rebind_slot_attachment"}:
                    controller.connect_cross_plane(
                        (
                            f"{target_binding['satellite_a_id']}:"
                            f"{target_binding['satellite_b_id']}:"
                            f"{target_binding['satellite_a_direction']}:"
                            f"{target_binding['satellite_b_direction']}"
                        ),
                        requested_slot_name=slot_name,
                    )
            else:
                raise NetworkMutationExecutionError(
                    f"unsupported real network mutation slot_type: {slot_type}"
                )

        qdisc_apply_wall_seconds = time.perf_counter() - started_at
        metadata_started_at = time.perf_counter()
        if persist_link_state:
            self._persist_slot_runtime_parameters(
                link_state_path=link_state_path,
                slot_name=slot_name,
                runtime_parameters=target_runtime_parameters,
            )
            metadata_update_wall_seconds = time.perf_counter() - metadata_started_at
        controller.link_state = _load_json(link_state_path)
        if state_by_name is not None:
            state_by_name[slot_name] = _state_entry_from_operation(operation, source=False)
        slot_outcome = _slot_outcome_from_operation(slot_name, operation)
        return {
            "slot_name": slot_name,
            "slot_type": slot_type,
            "operation": operation_name,
            "delta_category": slot_outcome["category"],
            "change_class": slot_outcome["change_class"],
            "reused_slot_runtime": slot_outcome["reused"],
            "resulting_state": _slot_summary(_state_entry_from_operation(operation, source=False)),
            "wall_seconds": round(time.perf_counter() - started_at, 3),
            "qdisc_apply_wall_seconds": round(qdisc_apply_wall_seconds, 6),
            "metadata_update_wall_seconds": round(metadata_update_wall_seconds, 6),
            "controller_stdout": captured_stdout.getvalue().strip(),
        }


@dataclass(frozen=True)
class DirectHostRuntimeContext:
    topology_path: str
    link_state_path: str
    topology: Dict[str, Any]


class DirectNetnsVethSlotLinkNamespaceQdiscInstaller:
    """Direct host-runtime installer backed by Linux bridges, veth pairs, and netns."""

    installer_name = "direct_netns_veth_slot_link_namespace_qdisc_installer"
    installer_kind = "direct_host_runtime_installer"
    installer_version = "0.3.0"
    execution_transport = "direct_linux_netns_veth_qdisc"
    supports_bounded_parallelism = True
    supports_batched_link_state_persist = True

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._topology_cache: Dict[str, Dict[str, Any]] = {}
        self._slot_inventory_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._container_pid_cache: Dict[str, int] = {}
        self._prepared_resource_keys_by_slot: Dict[str, set[str]] = {}
        self._runtime_prepared = False
        self._prepared_topology_path: str | None = None
        self._prepared_link_state_path: str | None = None
        self._prepared_resource_counts: Dict[str, int] = {
            "bridge_count": 0,
            "preconnected_endpoint_count": 0,
            "candidate_resource_count": 0,
            "candidate_endpoint_count": 0,
        }
        self._last_setup_timing: Dict[str, float] = {
            "runtime_prepare_wall_seconds": 0.0,
            "runtime_reconcile_wall_seconds": 0.0,
        }
        self._last_setup_summary: Dict[str, Any] | None = None
        self._last_cleanup_summary: Dict[str, Any] | None = None

    def _host_interface_name_set(self) -> set[str]:
        result = self._run_command(["ip", "-o", "link", "show"])
        names: set[str] = set()
        for line in result.stdout.splitlines():
            parts = line.split(": ", 2)
            if len(parts) < 2:
                continue
            names.add(parts[1].split("@", 1)[0].strip())
        return names

    def _docker_network_name_set(self) -> set[str]:
        result = self._run_command(
            ["docker", "network", "ls", "--format", "{{.Name}}"],
        )
        return {
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        }

    def _container_interface_name_set(self, *, node_id: str) -> set[str]:
        try:
            result = self._nsenter(
                node_id=node_id,
                command=["ip", "-o", "link", "show"],
                allow_failure=True,
            )
        except (RuntimeError, subprocess.CalledProcessError, ValueError):
            return set()
        if result.returncode != 0:
            return set()
        names: set[str] = set()
        for line in result.stdout.splitlines():
            parts = line.split(": ", 2)
            if len(parts) < 2:
                continue
            names.add(parts[1].split("@", 1)[0].strip())
        return names

    def consume_setup_timing(self) -> Dict[str, float]:
        return dict(self._last_setup_timing)

    def last_setup_summary(self) -> Dict[str, Any] | None:
        return (
            None
            if self._last_setup_summary is None
            else dict(self._last_setup_summary)
        )

    def public_runtime_summary(self) -> Dict[str, Any]:
        return {
            "installer_runtime_prepared": self._runtime_prepared,
            "prepared_topology_path": self._prepared_topology_path,
            "prepared_link_state_path": self._prepared_link_state_path,
            "prepared_bridge_count": self._prepared_resource_counts["bridge_count"],
            "prepared_candidate_resource_count": self._prepared_resource_counts[
                "candidate_resource_count"
            ],
            "prepared_candidate_endpoint_count": self._prepared_resource_counts[
                "candidate_endpoint_count"
            ],
            "prepared_preconnected_endpoint_count": self._prepared_resource_counts[
                "preconnected_endpoint_count"
            ],
            "prepared_slot_scope_count": len(self._prepared_resource_keys_by_slot),
            "container_pid_cache_count": len(self._container_pid_cache),
            "pending_prewarm_reconcile": self._pending_prewarm_reconcile_signature is not None,
            "last_cleanup_summary": (
                None
                if self._last_cleanup_summary is None
                else dict(self._last_cleanup_summary)
            ),
        }

    def _normalized_slot_scope(
        self,
        slot_names: Sequence[str] | None,
    ) -> tuple[str, ...] | None:
        if slot_names is None:
            return None
        return tuple(sorted({str(slot_name) for slot_name in slot_names if str(slot_name)}))

    def _prewarm_reconcile_signature(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        slot_names: Sequence[str] | None,
    ) -> Dict[str, Any]:
        return {
            "topology_path": str(topology_path.resolve()),
            "link_state_path": str(link_state_path.resolve()),
            "slot_names": self._normalized_slot_scope(slot_names),
        }

    def _reconcile_scope_summary(
        self,
        *,
        topology: Dict[str, Any],
        slot_names: Sequence[str] | None,
        skipped_due_to_prewarm: bool = False,
    ) -> Dict[str, Any]:
        inventory = self._slot_inventory_by_name(topology)
        slot_counts_by_type: Dict[str, int] = {}
        scoped_slot_names = self._normalized_slot_scope(slot_names)
        slot_iterable = sorted(inventory) if scoped_slot_names is None else list(scoped_slot_names)
        considered_slot_count = 0
        for slot_name in slot_iterable:
            slot = inventory.get(str(slot_name))
            if slot is None or str(slot.get("slot_type")) == "fixed_isl":
                continue
            considered_slot_count += 1
            slot_type = self._slot_type(slot)
            slot_counts_by_type[slot_type] = slot_counts_by_type.get(slot_type, 0) + 1
        summary = {
            "scope": "dirty_scope" if scoped_slot_names is not None else "full_sweep",
            "full_sweep": scoped_slot_names is None,
            "requested_slot_count": (
                len(scoped_slot_names) if scoped_slot_names is not None else None
            ),
            "reconciled_slot_count": considered_slot_count,
            "activated_slot_count": 0,
            "deactivated_slot_count": 0,
            "slot_counts_by_type": slot_counts_by_type,
        }
        if skipped_due_to_prewarm:
            summary.update(
                {
                    "scope": (
                        "reused_prewarm_dirty_scope"
                        if scoped_slot_names is not None
                        else "reused_prewarm_full_sweep"
                    ),
                    "skipped_due_to_prewarm": True,
                }
            )
        return summary

    def _slot_type(self, slot: Dict[str, Any]) -> str:
        return str(slot.get("slot_type", "unknown"))

    def _runtime_artifact_inventory(
        self,
        *,
        topology: Dict[str, Any],
        topology_path: Path,
    ) -> Dict[str, Any]:
        inventory = self._inventory(topology, topology_path)
        bridge_names: List[str] = []
        host_interface_names: List[str] = []
        temp_host_interface_names: List[str] = []
        container_interface_names: List[Dict[str, str]] = []
        slot_counts_by_type: Dict[str, int] = {}
        candidate_resource_counts_by_type: Dict[str, int] = {}
        candidate_endpoint_counts_by_type: Dict[str, int] = {}
        preconnected_endpoint_counts_by_type: Dict[str, int] = {}
        for slot_name, slot in sorted(inventory.items()):
            slot_type = self._slot_type(slot)
            slot_counts_by_type[slot_type] = slot_counts_by_type.get(slot_type, 0) + 1
            bridge_names.append(self._bridge_name(slot_name))
            candidate_resources = self._candidate_resources(slot)
            candidate_resource_counts_by_type[slot_type] = (
                candidate_resource_counts_by_type.get(slot_type, 0)
                + len(candidate_resources)
            )
            for resource in candidate_resources.values():
                for endpoint in resource["endpoints"]:
                    host_interface_names.append(str(endpoint["host_interface_name"]))
                    temp_host_interface_names.append(str(endpoint["temp_interface_name"]))
                    container_interface_names.append(
                        {
                            "node_id": str(endpoint["node_id"]),
                            "interface_name": str(endpoint["interface_name"]),
                        }
                    )
                    candidate_endpoint_counts_by_type[slot_type] = (
                        candidate_endpoint_counts_by_type.get(slot_type, 0) + 1
                    )
            preconnected = self._preconnected_endpoint(slot)
            if preconnected is not None:
                host_interface_names.append(str(preconnected["host_interface_name"]))
                container_interface_names.append(
                    {
                        "node_id": str(preconnected["node_id"]),
                        "interface_name": str(preconnected["interface_name"]),
                    }
                )
                preconnected_endpoint_counts_by_type[slot_type] = (
                    preconnected_endpoint_counts_by_type.get(slot_type, 0) + 1
                )
        return {
            "bridge_names": bridge_names,
            "host_interface_names": host_interface_names,
            "temp_host_interface_names": temp_host_interface_names,
            "container_interface_names": container_interface_names,
            "slot_counts_by_type": slot_counts_by_type,
            "candidate_resource_counts_by_type": candidate_resource_counts_by_type,
            "candidate_endpoint_counts_by_type": candidate_endpoint_counts_by_type,
            "preconnected_endpoint_counts_by_type": preconnected_endpoint_counts_by_type,
        }

    def controller(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        reconcile_slot_names: Sequence[str] | None = None,
        prepared_resource_scope_by_slot: Mapping[str, Sequence[str] | set[str]] | None = None,
        record_prewarm_reconcile: bool = False,
        consume_matching_prewarm: bool = False,
        reconcile_mode: str = "apply",
        setup_progress_callback: Callable[[Dict[str, Any]], None] | None = None,
    ) -> DirectHostRuntimeContext:
        with self._lock:
            topology = self._load_topology(topology_path)
            prepare_started_at = time.perf_counter()
            prepared_now = False
            preparation_summary = None
            if (
                not self._runtime_prepared
                or self._prepared_topology_path != str(topology_path)
                or self._prepared_link_state_path != str(link_state_path)
            ):
                if setup_progress_callback is not None:
                    setup_progress_callback(
                        {
                            "stage": "runtime_prepare_started",
                        }
                    )
                preparation_summary = self._prepare_runtime(
                    topology=topology,
                    topology_path=topology_path,
                    link_state_path=link_state_path,
                    prepared_resource_scope_by_slot=prepared_resource_scope_by_slot,
                    progress_callback=setup_progress_callback,
                )
                self._runtime_prepared = True
                self._prepared_topology_path = str(topology_path)
                self._prepared_link_state_path = str(link_state_path)
                prepared_now = True
            prepare_wall_seconds = time.perf_counter() - prepare_started_at
            if setup_progress_callback is not None:
                setup_progress_callback(
                    {
                        "stage": "runtime_prepare_completed",
                        "prepared_now": prepared_now,
                        "prepare_wall_seconds": round(prepare_wall_seconds, 6),
                        "prepared_resource_counts": dict(self._prepared_resource_counts),
                    }
                )

            reconcile_started_at = time.perf_counter()
            prewarm_signature = self._prewarm_reconcile_signature(
                topology_path=topology_path,
                link_state_path=link_state_path,
                slot_names=reconcile_slot_names,
            )
            if setup_progress_callback is not None:
                setup_progress_callback(
                    {
                        "stage": "runtime_reconcile_started",
                        "requested_slot_count": (
                            len({str(slot_name) for slot_name in reconcile_slot_names})
                            if reconcile_slot_names is not None
                            else None
                        ),
                        "record_prewarm_reconcile": bool(record_prewarm_reconcile),
                        "reconcile_mode": str(reconcile_mode),
                    }
                )
            skip_due_to_prewarm = bool(
                consume_matching_prewarm
                and self._pending_prewarm_reconcile_signature == prewarm_signature
            )
            if skip_due_to_prewarm:
                reconcile_summary = self._reconcile_scope_summary(
                    topology=topology,
                    slot_names=reconcile_slot_names,
                    skipped_due_to_prewarm=True,
                )
                self._pending_prewarm_reconcile_signature = None
            elif reconcile_mode == "trusted_skip":
                reconcile_summary = self._reconcile_scope_summary(
                    topology=topology,
                    slot_names=reconcile_slot_names,
                    skipped_due_to_prewarm=False,
                )
                reconcile_summary.update(
                    {
                        "scope": (
                            "trusted_cached_state_skip"
                            if reconcile_summary.get("full_sweep") is not True
                            else "trusted_cached_state_full_skip"
                        ),
                        "skipped_due_to_trusted_state": True,
                    }
                )
            else:
                reconcile_summary = self._reconcile_to_link_state(
                    topology=topology,
                    link_state_path=link_state_path,
                    slot_names=reconcile_slot_names,
                    progress_callback=setup_progress_callback,
                )
            reconcile_wall_seconds = time.perf_counter() - reconcile_started_at
            if setup_progress_callback is not None:
                setup_progress_callback(
                    {
                        "stage": "runtime_reconcile_completed",
                        "reconcile_wall_seconds": round(reconcile_wall_seconds, 6),
                        "reconcile_summary": dict(reconcile_summary),
                    }
                )
            if record_prewarm_reconcile:
                self._pending_prewarm_reconcile_signature = dict(prewarm_signature)
            self._last_setup_timing = {
                "runtime_prepare_wall_seconds": (
                    prepare_wall_seconds if prepared_now else 0.0
                ),
                "runtime_reconcile_wall_seconds": reconcile_wall_seconds,
            }
            self._last_setup_summary = {
                "prepared_now": prepared_now,
                "prepared_backend": self.execution_transport,
                "reconciled_slot_count": int(reconcile_summary["reconciled_slot_count"]),
                "prepared_resource_counts": dict(self._prepared_resource_counts),
                "prepare_summary": preparation_summary,
                "reconcile_summary": reconcile_summary,
            }
            return DirectHostRuntimeContext(
                topology_path=str(topology_path),
                link_state_path=str(link_state_path),
                topology=topology,
            )

    def state_by_name(self, *, link_state_path: Path) -> Dict[str, Dict[str, Any]]:
        return dynamic_slot_state_from_link_state(_load_json(link_state_path))

    def bootstrap_runtime_if_needed(
        self,
        *,
        controller: DirectHostRuntimeContext,
        link_state_path: Path,
        failure_reason: str,
        reconcile_slot_names: Sequence[str] | None = None,
    ) -> Dict[str, Any] | None:
        with self._lock:
            topology = controller.topology
            prepare_started_at = time.perf_counter()
            preparation_summary = self._prepare_runtime(
                topology=topology,
                topology_path=Path(controller.topology_path),
                link_state_path=link_state_path,
            )
            reconcile_started_at = time.perf_counter()
            reconcile_summary = self._reconcile_to_link_state(
                topology=topology,
                link_state_path=link_state_path,
                slot_names=reconcile_slot_names,
            )
            prepare_wall_seconds = time.perf_counter() - prepare_started_at
            reconcile_wall_seconds = time.perf_counter() - reconcile_started_at
            self._last_setup_timing = {
                "runtime_prepare_wall_seconds": prepare_wall_seconds,
                "runtime_reconcile_wall_seconds": reconcile_wall_seconds,
            }
            self._last_setup_summary = {
                "prepared_now": True,
                "prepared_backend": self.execution_transport,
                "reconciled_slot_count": int(reconcile_summary["reconciled_slot_count"]),
                "prepared_resource_counts": dict(self._prepared_resource_counts),
                "trigger": failure_reason,
                "prepare_summary": preparation_summary,
                "reconcile_summary": reconcile_summary,
            }
            self._pending_prewarm_reconcile_signature = None
            return dict(self._last_setup_summary)

    def _load_topology(self, topology_path: Path) -> Dict[str, Any]:
        topology_key = str(topology_path.resolve())
        cached = self._topology_cache.get(topology_key)
        if cached is not None:
            return cached
        topology = _load_json(topology_path)
        self._topology_cache[topology_key] = topology
        self._slot_inventory_cache[topology_key] = self._slot_inventory_by_name(topology)
        return topology

    def _slot_inventory_by_name(self, topology: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        entries: Dict[str, Dict[str, Any]] = {}
        for group in dict(topology.get("slot_inventory", {})).values():
            for entry in list(group):
                entries[str(entry.get("slot_name"))] = dict(entry)
        return entries

    def _inventory(self, topology: Dict[str, Any], topology_path: Path) -> Dict[str, Dict[str, Any]]:
        topology_key = str(topology_path.resolve())
        inventory = self._slot_inventory_cache.get(topology_key)
        if inventory is None:
            inventory = self._slot_inventory_by_name(topology)
            self._slot_inventory_cache[topology_key] = inventory
        return inventory

    def _bridge_name(self, slot_name: str) -> str:
        return _short_name("sbr", slot_name)

    def _host_interface_name(
        self,
        *,
        slot_name: str,
        endpoint: Dict[str, Any],
        role: str,
    ) -> str:
        seed = (
            f"{slot_name}:{role}:{endpoint.get('node_id')}:{endpoint.get('interface_name')}"
        )
        return _short_name("vhs", seed)

    def _temp_interface_name(
        self,
        *,
        slot_name: str,
        endpoint: Dict[str, Any],
        role: str,
    ) -> str:
        seed = (
            f"{slot_name}:{role}:{endpoint.get('node_id')}:{endpoint.get('interface_name')}:tmp"
        )
        return _short_name("vtm", seed)

    def _resource_key(self, slot_type: str, binding: Dict[str, Any]) -> str:
        return dynamic_slot_binding_resource_key(slot_type, binding)

    def _candidate_resources(self, slot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        slot_name = str(slot.get("slot_name"))
        slot_type = str(slot.get("slot_type"))
        if slot_type in {"ut_access", "gw_access"}:
            endpoint_template = dict(slot.get("dynamic_endpoint_template", {}))
            resources: Dict[str, Dict[str, Any]] = {}
            for satellite_id in slot.get("candidate_satellite_ids", []):
                endpoint = {
                    "node_id": str(satellite_id),
                    "node_type": str(endpoint_template.get("node_type")),
                    "interface_name": str(endpoint_template.get("interface_name")),
                    "ip_address": str(endpoint_template.get("ip_address")),
                }
                resource_key = self._resource_key(
                    slot_type,
                    {"satellite_id": str(satellite_id)},
                )
                resources[resource_key] = {
                    "resource_key": resource_key,
                    "slot_name": slot_name,
                    "slot_type": slot_type,
                    "binding": (
                        {"satellite_id": str(satellite_id)}
                        if slot_type == "ut_access"
                        else {"satellite_id": str(satellite_id)}
                    ),
                    "endpoints": [
                        {
                            **endpoint,
                            "host_interface_name": self._host_interface_name(
                                slot_name=slot_name,
                                endpoint=endpoint,
                                role=f"candidate:{satellite_id}",
                            ),
                            "temp_interface_name": self._temp_interface_name(
                                slot_name=slot_name,
                                endpoint=endpoint,
                                role=f"candidate:{satellite_id}",
                            ),
                        }
                    ],
                }
            return resources

        resources = {}
        endpoint_a_template = dict(slot.get("endpoint_a_template", {}))
        endpoint_b_template = dict(slot.get("endpoint_b_template", {}))
        for pair in slot.get("candidate_pairs", []):
            binding = {
                "satellite_a_direction": str(pair.get("satellite_a_direction")),
                "satellite_a_id": str(pair.get("satellite_a_id")),
                "satellite_b_direction": str(pair.get("satellite_b_direction")),
                "satellite_b_id": str(pair.get("satellite_b_id")),
            }
            resource_key = self._resource_key(slot_type, binding)
            endpoint_a = {
                "node_id": binding["satellite_a_id"],
                "node_type": str(endpoint_a_template.get("node_type")),
                "interface_name": str(endpoint_a_template.get("interface_name")),
                "ip_address": str(endpoint_a_template.get("ip_address")),
                "logical_direction": binding["satellite_a_direction"],
            }
            endpoint_b = {
                "node_id": binding["satellite_b_id"],
                "node_type": str(endpoint_b_template.get("node_type")),
                "interface_name": str(endpoint_b_template.get("interface_name")),
                "ip_address": str(endpoint_b_template.get("ip_address")),
                "logical_direction": binding["satellite_b_direction"],
            }
            resources[resource_key] = {
                "resource_key": resource_key,
                "slot_name": slot_name,
                "slot_type": slot_type,
                "binding": binding,
                "endpoints": [
                    {
                        **endpoint_a,
                        "host_interface_name": self._host_interface_name(
                            slot_name=slot_name,
                            endpoint=endpoint_a,
                            role="candidate:a",
                        ),
                        "temp_interface_name": self._temp_interface_name(
                            slot_name=slot_name,
                            endpoint=endpoint_a,
                            role="candidate:a",
                        ),
                    },
                    {
                        **endpoint_b,
                        "host_interface_name": self._host_interface_name(
                            slot_name=slot_name,
                            endpoint=endpoint_b,
                            role="candidate:b",
                        ),
                        "temp_interface_name": self._temp_interface_name(
                            slot_name=slot_name,
                            endpoint=endpoint_b,
                            role="candidate:b",
                        ),
                    },
                ],
            }
        return resources

    def _prepared_resources_for_slot(
        self,
        *,
        slot: Dict[str, Any],
        prepared_resource_scope_by_slot: Mapping[str, Sequence[str] | set[str]] | None = None,
    ) -> Dict[str, Dict[str, Any]]:
        resources = self._candidate_resources(slot)
        if not prepared_resource_scope_by_slot:
            return resources
        slot_name = str(slot.get("slot_name"))
        scoped_keys = prepared_resource_scope_by_slot.get(slot_name)
        if not scoped_keys:
            return resources
        allowed = {str(resource_key) for resource_key in scoped_keys if str(resource_key)}
        if not allowed:
            return resources
        return {
            resource_key: resource
            for resource_key, resource in resources.items()
            if resource_key in allowed
        }

    def _preconnected_endpoint(self, slot: Dict[str, Any]) -> Dict[str, Any] | None:
        preconnected = slot.get("preconnected_endpoint")
        if not isinstance(preconnected, dict):
            return None
        slot_name = str(slot.get("slot_name"))
        endpoint = {
            **dict(preconnected),
            "host_interface_name": self._host_interface_name(
                slot_name=slot_name,
                endpoint=dict(preconnected),
                role="preconnected",
            ),
            "temp_interface_name": self._temp_interface_name(
                slot_name=slot_name,
                endpoint=dict(preconnected),
                role="preconnected",
            ),
        }
        return endpoint

    def _run_command(
        self,
        command: Sequence[str],
        *,
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        timeout_seconds = _command_timeout_seconds(command)
        try:
            completed = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            completed = subprocess.CompletedProcess(
                args=list(command),
                returncode=124,
                stdout=exc.stdout or "",
                stderr=(
                    (exc.stderr or "")
                    + f"\ncommand timed out after {timeout_seconds:.1f}s"
                ).strip(),
            )
        if completed.returncode != 0 and not allow_failure:
            raise RuntimeError(
                f"command failed ({completed.returncode}): {' '.join(command)}: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
        return completed

    def _prepare_bridge_netfilter_passthrough(self) -> None:
        for key in BRIDGE_NETFILTER_SYSCTLS:
            self._run_command(["sudo", "-n", "sysctl", "-w", f"{key}=0"])

    def _host_interface_exists(
        self,
        interface_name: str,
        *,
        known_interfaces: set[str] | None = None,
    ) -> bool:
        if known_interfaces is not None:
            return interface_name in known_interfaces
        return (
            self._run_command(
                ["sudo", "-n", "ip", "link", "show", "dev", interface_name],
                allow_failure=True,
            ).returncode
            == 0
        )

    def _delete_host_interface_if_present(
        self,
        interface_name: str,
        *,
        known_interfaces: set[str] | None = None,
    ) -> None:
        if not self._host_interface_exists(interface_name, known_interfaces=known_interfaces):
            return
        self._run_command(["sudo", "-n", "ip", "link", "del", interface_name])
        if known_interfaces is not None:
            known_interfaces.discard(interface_name)

    def _bridge_exists(
        self,
        bridge_name: str,
        *,
        known_interfaces: set[str] | None = None,
    ) -> bool:
        return self._host_interface_exists(bridge_name, known_interfaces=known_interfaces)

    def _container_pid(self, node_id: str) -> int:
        with self._lock:
            cached = self._container_pid_cache.get(node_id)
            if cached is not None:
                return cached
        result = self._run_command(
            ["docker", "inspect", "-f", "{{.State.Pid}}", node_id]
        )
        pid = int(result.stdout.strip())
        if pid <= 0:
            raise RuntimeError(f"container pid is not valid for {node_id}")
        with self._lock:
            self._container_pid_cache[node_id] = pid
        return pid

    def _nsenter(
        self,
        *,
        node_id: str,
        command: Sequence[str],
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        pid = self._container_pid(node_id)
        return self._run_command(
            ["sudo", "-n", "nsenter", "-t", str(pid), "-n", *command],
            allow_failure=allow_failure,
        )

    def _container_interface_exists(
        self,
        *,
        node_id: str,
        interface_name: str,
        known_interfaces: set[str] | None = None,
    ) -> bool:
        if known_interfaces is not None:
            return interface_name in known_interfaces
        try:
            return (
                self._nsenter(
                    node_id=node_id,
                    command=["ip", "link", "show", "dev", interface_name],
                    allow_failure=True,
                ).returncode
                == 0
            )
        except (RuntimeError, subprocess.CalledProcessError, ValueError):
            return False

    def _delete_container_interface_if_present(
        self,
        *,
        node_id: str,
        interface_name: str,
        known_interfaces: set[str] | None = None,
    ) -> None:
        if not self._container_interface_exists(
            node_id=node_id,
            interface_name=interface_name,
            known_interfaces=known_interfaces,
        ):
            return
        try:
            self._nsenter(
                node_id=node_id,
                command=["ip", "link", "del", interface_name],
            )
            if known_interfaces is not None:
                known_interfaces.discard(interface_name)
        except (RuntimeError, subprocess.CalledProcessError, ValueError):
            return

    def _docker_network_exists(
        self,
        docker_network_name: str,
        *,
        known_networks: set[str] | None = None,
    ) -> bool:
        if known_networks is not None:
            return docker_network_name in known_networks
        return (
            self._run_command(
                ["docker", "network", "inspect", docker_network_name],
                allow_failure=True,
            ).returncode
            == 0
        )

    def _remove_docker_network_if_present(
        self,
        docker_network_name: str,
        *,
        known_networks: set[str] | None = None,
    ) -> None:
        if not self._docker_network_exists(docker_network_name, known_networks=known_networks):
            return
        inspect = self._run_command(["docker", "network", "inspect", docker_network_name])
        payload = json.loads(inspect.stdout)
        containers = (payload[0] if payload else {}).get("Containers") or {}
        for container in containers.values():
            node_id = str(container.get("Name"))
            if node_id:
                self._run_command(
                    [
                        "docker",
                        "network",
                        "disconnect",
                        "-f",
                        docker_network_name,
                        node_id,
                    ],
                    allow_failure=True,
                )
        self._run_command(
            ["docker", "network", "rm", docker_network_name],
            allow_failure=True,
        )
        if known_networks is not None:
            known_networks.discard(docker_network_name)

    def _set_bridge_ready(
        self,
        *,
        bridge_name: str,
        subnet: str,
        bridge_gateway_ip: str | None,
        known_interfaces: set[str] | None = None,
    ) -> None:
        self._run_command(
            ["sudo", "-n", "ip", "link", "add", "name", bridge_name, "type", "bridge"]
        )
        if known_interfaces is not None:
            known_interfaces.add(bridge_name)
        self._run_command(
            ["sudo", "-n", "ip", "addr", "flush", "dev", bridge_name],
            allow_failure=True,
        )
        if bridge_gateway_ip:
            self._run_command(
                [
                    "sudo",
                    "-n",
                    "ip",
                    "addr",
                    "add",
                    f"{bridge_gateway_ip}/{subnet.split('/', 1)[1]}",
                    "dev",
                    bridge_name,
                ]
            )
        self._run_command(["sudo", "-n", "ip", "link", "set", bridge_name, "up"])

    def _create_prepared_veth(
        self,
        *,
        bridge_name: str,
        slot_name: str,
        endpoint: Dict[str, Any],
        activate_now: bool,
        assign_ip: bool,
        known_host_interfaces: set[str] | None = None,
        known_container_interfaces: set[str] | None = None,
    ) -> None:
        host_interface_name = str(endpoint.get("host_interface_name"))
        temp_interface_name = str(endpoint.get("temp_interface_name"))
        node_id = str(endpoint.get("node_id"))
        interface_name = str(endpoint.get("interface_name"))
        ip_address = str(endpoint.get("ip_address"))
        logical_mac = _stable_local_mac(f"{slot_name}:{interface_name}:{ip_address}")
        self._delete_host_interface_if_present(
            host_interface_name,
            known_interfaces=known_host_interfaces,
        )
        self._delete_container_interface_if_present(
            node_id=node_id,
            interface_name=interface_name,
            known_interfaces=known_container_interfaces,
        )
        self._run_command(
            [
                "sudo",
                "-n",
                "ip",
                "link",
                "add",
                host_interface_name,
                "type",
                "veth",
                "peer",
                "name",
                temp_interface_name,
            ]
        )
        if known_host_interfaces is not None:
            known_host_interfaces.add(host_interface_name)
            known_host_interfaces.add(temp_interface_name)
        pid = self._container_pid(node_id)
        self._run_command(
            ["sudo", "-n", "ip", "link", "set", temp_interface_name, "netns", str(pid)]
        )
        if known_host_interfaces is not None:
            known_host_interfaces.discard(temp_interface_name)
        self._nsenter(
            node_id=node_id,
            command=["ip", "link", "set", temp_interface_name, "name", interface_name],
        )
        if known_container_interfaces is not None:
            known_container_interfaces.add(interface_name)
        self._nsenter(
            node_id=node_id,
            command=["ip", "link", "set", "dev", interface_name, "address", logical_mac],
        )
        self._nsenter(
            node_id=node_id,
            command=["ip", "addr", "flush", "dev", interface_name],
            allow_failure=True,
        )
        if assign_ip:
            self._nsenter(
                node_id=node_id,
                command=["ip", "addr", "add", ip_address, "dev", interface_name],
            )
        self._nsenter(
            node_id=node_id,
            command=["ip", "link", "set", interface_name, "down"],
            allow_failure=True,
        )
        self._run_command(
            ["sudo", "-n", "ip", "link", "set", host_interface_name, "down"],
            allow_failure=True,
        )
        if activate_now:
            self._run_command(
                ["sudo", "-n", "ip", "link", "set", host_interface_name, "master", bridge_name]
            )
            self._run_command(
                ["sudo", "-n", "ip", "link", "set", host_interface_name, "up"]
            )
            self._nsenter(
                node_id=node_id,
                command=["ip", "link", "set", interface_name, "up"],
            )

    def _prepare_runtime(
        self,
        *,
        topology: Dict[str, Any],
        topology_path: Path,
        link_state_path: Path,
        prepared_resource_scope_by_slot: Mapping[str, Sequence[str] | set[str]] | None = None,
        progress_callback: Callable[[Dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        self._prepare_bridge_netfilter_passthrough()
        artifact_inventory = self._runtime_artifact_inventory(
            topology=topology,
            topology_path=topology_path,
        )
        inventory = self._inventory(topology, topology_path)
        total_slot_count = len(inventory)
        bridge_count = 0
        preconnected_endpoint_count = 0
        candidate_resource_count = 0
        candidate_endpoint_count = 0
        known_host_interfaces = self._host_interface_name_set()
        known_docker_networks = self._docker_network_name_set()
        known_container_interfaces_by_node: Dict[str, set[str]] = {}
        self._prepared_resource_keys_by_slot = {}
        started_at = time.perf_counter()
        last_progress_emit_at = started_at
        last_progress_slot_count = 0

        def _known_container_interfaces(node_id: str) -> set[str]:
            cached = known_container_interfaces_by_node.get(node_id)
            if cached is not None:
                return cached
            cached = self._container_interface_name_set(node_id=node_id)
            known_container_interfaces_by_node[node_id] = cached
            return cached

        def _emit_prepare_progress(*, processed_slot_count: int, current_slot_name: str | None = None, force: bool = False) -> None:
            nonlocal last_progress_emit_at, last_progress_slot_count
            if progress_callback is None:
                return
            now = time.perf_counter()
            if not force:
                if processed_slot_count <= 0:
                    return
                if (
                    (processed_slot_count - last_progress_slot_count) < RECONCILE_PROGRESS_MIN_SLOT_DELTA
                    and (now - last_progress_emit_at) < RECONCILE_PROGRESS_MIN_INTERVAL_SECONDS
                ):
                    return
            progress_callback(
                {
                    "stage": "runtime_prepare_progress",
                    "processed_slot_count": processed_slot_count,
                    "total_slot_count": total_slot_count,
                    "remaining_slot_count": max(0, total_slot_count - processed_slot_count),
                    "bridge_count": bridge_count,
                    "preconnected_endpoint_count": preconnected_endpoint_count,
                    "candidate_resource_count": candidate_resource_count,
                    "candidate_endpoint_count": candidate_endpoint_count,
                    "current_slot_name": current_slot_name,
                    "elapsed_wall_seconds": round(now - started_at, 6),
                }
            )
            last_progress_emit_at = now
            last_progress_slot_count = processed_slot_count

        for processed_slot_count, (slot_name, slot) in enumerate(sorted(inventory.items()), start=1):
            bridge_name = self._bridge_name(slot_name)
            self._remove_docker_network_if_present(
                str(slot.get("docker_network_name")),
                known_networks=known_docker_networks,
            )
            for resource in self._candidate_resources(slot).values():
                for endpoint in resource["endpoints"]:
                    node_id = str(endpoint["node_id"])
                    self._delete_host_interface_if_present(
                        str(endpoint["host_interface_name"]),
                        known_interfaces=known_host_interfaces,
                    )
                    self._delete_container_interface_if_present(
                        node_id=node_id,
                        interface_name=str(endpoint["interface_name"]),
                        known_interfaces=_known_container_interfaces(node_id),
                    )
            preconnected = self._preconnected_endpoint(slot)
            if preconnected is not None:
                node_id = str(preconnected["node_id"])
                self._delete_host_interface_if_present(
                    str(preconnected["host_interface_name"]),
                    known_interfaces=known_host_interfaces,
                )
                self._delete_container_interface_if_present(
                    node_id=node_id,
                    interface_name=str(preconnected["interface_name"]),
                    known_interfaces=_known_container_interfaces(node_id),
                )
            if self._bridge_exists(bridge_name, known_interfaces=known_host_interfaces):
                self._run_command(
                    ["sudo", "-n", "ip", "link", "set", bridge_name, "down"],
                    allow_failure=True,
                )
                self._run_command(
                    ["sudo", "-n", "ip", "link", "del", bridge_name],
                    allow_failure=True,
                )
                known_host_interfaces.discard(bridge_name)
            self._set_bridge_ready(
                bridge_name=bridge_name,
                subnet=str(slot.get("subnet")),
                bridge_gateway_ip=(
                    None
                    if slot.get("bridge_gateway_ip") in (None, "")
                    else str(slot.get("bridge_gateway_ip"))
                ),
                known_interfaces=known_host_interfaces,
            )
            bridge_count += 1

            if preconnected is not None:
                node_id = str(preconnected["node_id"])
                self._create_prepared_veth(
                    bridge_name=bridge_name,
                    slot_name=slot_name,
                    endpoint=preconnected,
                    activate_now=True,
                    assign_ip=True,
                    known_host_interfaces=known_host_interfaces,
                    known_container_interfaces=_known_container_interfaces(node_id),
                )
                preconnected_endpoint_count += 1

            candidate_resources = self._prepared_resources_for_slot(
                slot=slot,
                prepared_resource_scope_by_slot=prepared_resource_scope_by_slot,
            )
            candidate_resource_count += len(candidate_resources)
            for resource in candidate_resources.values():
                slot_prepared_resource_keys = self._prepared_resource_keys_by_slot.setdefault(
                    slot_name,
                    set(),
                )
                for endpoint in resource["endpoints"]:
                    node_id = str(endpoint["node_id"])
                    self._create_prepared_veth(
                        bridge_name=bridge_name,
                        slot_name=slot_name,
                        endpoint=endpoint,
                        activate_now=False,
                        assign_ip=False,
                        known_host_interfaces=known_host_interfaces,
                        known_container_interfaces=_known_container_interfaces(node_id),
                    )
                    candidate_endpoint_count += 1
                resource_key = str(resource.get("resource_key", ""))
                if resource_key:
                    slot_prepared_resource_keys.add(resource_key)
            _emit_prepare_progress(
                processed_slot_count=processed_slot_count,
                current_slot_name=slot_name,
            )

        self._prepared_resource_counts = {
            "bridge_count": bridge_count,
            "preconnected_endpoint_count": preconnected_endpoint_count,
            "candidate_resource_count": candidate_resource_count,
            "candidate_endpoint_count": candidate_endpoint_count,
        }
        _emit_prepare_progress(
            processed_slot_count=total_slot_count,
            current_slot_name=None,
            force=True,
        )
        self._mark_link_state_prepared(link_state_path=link_state_path)
        return {
            "scope": "full_prepare",
            "full_sweep": True,
            "slot_count": int(sum(artifact_inventory["slot_counts_by_type"].values())),
            "slot_counts_by_type": dict(artifact_inventory["slot_counts_by_type"]),
            "candidate_resource_counts_by_type": dict(
                artifact_inventory["candidate_resource_counts_by_type"]
            ),
            "candidate_endpoint_counts_by_type": dict(
                artifact_inventory["candidate_endpoint_counts_by_type"]
            ),
            "preconnected_endpoint_counts_by_type": dict(
                artifact_inventory["preconnected_endpoint_counts_by_type"]
            ),
            "prepared_resource_counts": dict(self._prepared_resource_counts),
        }

    def _mark_link_state_prepared(self, *, link_state_path: Path) -> None:
        link_state = _load_json(link_state_path)
        link_state["runtime_initialized"] = True
        link_state["host_runtime_backend"] = self.execution_transport
        link_state["host_runtime_prepared"] = True
        link_state["last_host_runtime_prepare_epoch_seconds"] = round(time.time(), 3)
        _write_json(link_state_path, link_state)

    def _reconcile_to_link_state(
        self,
        *,
        topology: Dict[str, Any],
        link_state_path: Path,
        slot_names: Sequence[str] | None = None,
        progress_callback: Callable[[Dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        inventory = self._slot_inventory_by_name(topology)
        link_state = _load_json(link_state_path)
        reconciled = 0
        activated_slot_count = 0
        deactivated_slot_count = 0
        slot_counts_by_type: Dict[str, int] = {}
        scoped_slot_names = (
            {str(slot_name) for slot_name in slot_names}
            if slot_names is not None
            else None
        )
        requested_slot_count = (
            len(scoped_slot_names) if scoped_slot_names is not None else None
        )
        started_at = time.perf_counter()
        last_progress_emit_at = started_at
        last_progress_reconciled = 0

        def _emit_progress(*, current_slot_name: str | None = None, force: bool = False) -> None:
            nonlocal last_progress_emit_at, last_progress_reconciled
            if progress_callback is None:
                return
            now = time.perf_counter()
            if not force:
                if reconciled <= 0:
                    return
                if (
                    (reconciled - last_progress_reconciled) < RECONCILE_PROGRESS_MIN_SLOT_DELTA
                    and (now - last_progress_emit_at) < RECONCILE_PROGRESS_MIN_INTERVAL_SECONDS
                ):
                    return
            progress_callback(
                {
                    "stage": "runtime_reconcile_progress",
                    "scope": "dirty_scope" if scoped_slot_names is not None else "full_sweep",
                    "requested_slot_count": requested_slot_count,
                    "reconciled_slot_count": reconciled,
                    "activated_slot_count": activated_slot_count,
                    "deactivated_slot_count": deactivated_slot_count,
                    "remaining_slot_count": (
                        max(0, requested_slot_count - reconciled)
                        if requested_slot_count is not None
                        else None
                    ),
                    "slot_counts_by_type": dict(slot_counts_by_type),
                    "current_slot_name": current_slot_name,
                    "elapsed_wall_seconds": round(now - started_at, 6),
                }
            )
            last_progress_emit_at = now
            last_progress_reconciled = reconciled

        for slot_entry in link_state.get("slots", []):
            slot_name = str(slot_entry.get("slot_name"))
            if scoped_slot_names is not None and slot_name not in scoped_slot_names:
                continue
            slot = inventory.get(slot_name)
            if slot is None or str(slot_entry.get("slot_type")) == "fixed_isl":
                continue
            reconciled += 1
            slot_type = self._slot_type(slot)
            slot_counts_by_type[slot_type] = slot_counts_by_type.get(slot_type, 0) + 1
            binding = dict(slot_entry.get("binding") or {})
            runtime_parameters = dict(slot_entry.get("runtime_parameters") or {})
            if bool(slot_entry.get("occupied", False)) and binding:
                resource = self._resource_for_binding(slot=slot, binding=binding)
                self._activate_resource(
                    slot=slot,
                    resource=resource,
                    runtime_parameters=runtime_parameters,
                    probe=False,
                )
                activated_slot_count += 1
            else:
                self._deactivate_all_resources(slot=slot)
                deactivated_slot_count += 1
            _emit_progress(current_slot_name=slot_name)
        _emit_progress(force=True)
        return {
            "scope": "dirty_scope" if scoped_slot_names is not None else "full_sweep",
            "full_sweep": scoped_slot_names is None,
            "requested_slot_count": requested_slot_count,
            "reconciled_slot_count": reconciled,
            "activated_slot_count": activated_slot_count,
            "deactivated_slot_count": deactivated_slot_count,
            "slot_counts_by_type": slot_counts_by_type,
        }

    def cleanup_runtime_artifacts(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
    ) -> Dict[str, Any]:
        with self._lock:
            topology = self._load_topology(topology_path)
            artifact_inventory = self._runtime_artifact_inventory(
                topology=topology,
                topology_path=topology_path,
            )
            known_host_interfaces = self._host_interface_name_set()
            known_container_interfaces_by_node: Dict[str, set[str]] = {}

            def _known_container_interfaces(node_id: str) -> set[str]:
                cached = known_container_interfaces_by_node.get(node_id)
                if cached is not None:
                    return cached
                cached = self._container_interface_name_set(node_id=node_id)
                known_container_interfaces_by_node[node_id] = cached
                return cached

            removed_host_interfaces: List[str] = []
            removed_temp_host_interfaces: List[str] = []
            removed_container_interfaces: List[Dict[str, str]] = []
            removed_bridges: List[str] = []
            for interface_name in artifact_inventory["temp_host_interface_names"]:
                if self._host_interface_exists(
                    interface_name,
                    known_interfaces=known_host_interfaces,
                ):
                    self._delete_host_interface_if_present(
                        interface_name,
                        known_interfaces=known_host_interfaces,
                    )
                    removed_temp_host_interfaces.append(interface_name)
            for interface_name in artifact_inventory["host_interface_names"]:
                if self._host_interface_exists(
                    interface_name,
                    known_interfaces=known_host_interfaces,
                ):
                    self._delete_host_interface_if_present(
                        interface_name,
                        known_interfaces=known_host_interfaces,
                    )
                    removed_host_interfaces.append(interface_name)
            # Host-side veth deletion removes the paired container endpoint when it is still
            # attached, so container-side cleanup is only a best-effort fallback for residue.
            for endpoint in artifact_inventory["container_interface_names"]:
                node_id = str(endpoint["node_id"])
                interface_name = str(endpoint["interface_name"])
                known_container_interfaces = _known_container_interfaces(node_id)
                if self._container_interface_exists(
                    node_id=node_id,
                    interface_name=interface_name,
                    known_interfaces=known_container_interfaces,
                ):
                    self._delete_container_interface_if_present(
                        node_id=node_id,
                        interface_name=interface_name,
                        known_interfaces=known_container_interfaces,
                    )
                    removed_container_interfaces.append(
                        {"node_id": node_id, "interface_name": interface_name}
                    )
            for bridge_name in artifact_inventory["bridge_names"]:
                if self._bridge_exists(
                    bridge_name,
                    known_interfaces=known_host_interfaces,
                ):
                    self._run_command(
                        ["sudo", "-n", "ip", "link", "set", bridge_name, "down"],
                        allow_failure=True,
                    )
                    self._run_command(
                        ["sudo", "-n", "ip", "link", "del", bridge_name],
                        allow_failure=True,
                    )
                    known_host_interfaces.discard(bridge_name)
                    removed_bridges.append(bridge_name)
            remaining_host_interfaces = [
                interface_name
                for interface_name in artifact_inventory["host_interface_names"]
                if self._host_interface_exists(
                    interface_name,
                    known_interfaces=known_host_interfaces,
                )
            ]
            remaining_temp_host_interfaces = [
                interface_name
                for interface_name in artifact_inventory["temp_host_interface_names"]
                if self._host_interface_exists(
                    interface_name,
                    known_interfaces=known_host_interfaces,
                )
            ]
            remaining_bridges = [
                bridge_name
                for bridge_name in artifact_inventory["bridge_names"]
                if self._bridge_exists(
                    bridge_name,
                    known_interfaces=known_host_interfaces,
                )
            ]
            self._runtime_prepared = False
            self._prepared_topology_path = None
            self._prepared_link_state_path = None
            self._pending_prewarm_reconcile_signature = None
            self._prepared_resource_counts = {
                "bridge_count": 0,
                "preconnected_endpoint_count": 0,
                "candidate_resource_count": 0,
                "candidate_endpoint_count": 0,
            }
            self._prepared_resource_keys_by_slot = {}
            cleanup_summary = {
                "scope": "direct_backend_runtime_artifacts",
                "removed_bridge_count": len(removed_bridges),
                "removed_host_interface_count": len(removed_host_interfaces),
                "removed_temp_host_interface_count": len(removed_temp_host_interfaces),
                "removed_container_interface_count": len(removed_container_interfaces),
                "remaining_bridge_count": len(remaining_bridges),
                "remaining_host_interface_count": len(remaining_host_interfaces),
                "remaining_temp_host_interface_count": len(remaining_temp_host_interfaces),
                "remaining_bridges": remaining_bridges,
                "remaining_host_interfaces": remaining_host_interfaces,
                "remaining_temp_host_interfaces": remaining_temp_host_interfaces,
                "cleanup_completed": (
                    not remaining_bridges
                    and not remaining_host_interfaces
                    and not remaining_temp_host_interfaces
                ),
            }
            self._last_cleanup_summary = cleanup_summary
            return cleanup_summary

    def _resource_for_binding(self, *, slot: Dict[str, Any], binding: Dict[str, Any]) -> Dict[str, Any]:
        resource_key = self._resource_key(str(slot.get("slot_type")), binding)
        resources = self._candidate_resources(slot)
        resource = resources.get(resource_key)
        if resource is None:
            raise RuntimeError(
                f"no prepared resource for slot {slot.get('slot_name')} binding {binding}"
            )
        return resource

    def _runtime_parameter_netem_args(
        self,
        runtime_parameters: Dict[str, Any],
    ) -> List[str]:
        args: List[str] = []
        delay_ms = runtime_parameters.get("delay_ms")
        if delay_ms is None:
            delay_ms = runtime_parameters.get("latency_ms")
        if delay_ms is None:
            delay_ms = runtime_parameters.get("one_way_delay_ms")
        if delay_ms is not None:
            args.extend(["delay", f"{float(delay_ms):.3f}ms"])
        loss_percent = runtime_parameters.get("loss_percent")
        if loss_percent is None:
            loss_percent = runtime_parameters.get("packet_loss_percent")
        if loss_percent is not None:
            args.extend(["loss", f"{float(loss_percent):.6f}%"])
        rate_mbps = runtime_parameters.get("bandwidth_mbps")
        if rate_mbps is None:
            rate_mbps = runtime_parameters.get("rate_mbps")
        if rate_mbps is not None:
            args.extend(["rate", f"{float(rate_mbps):.6f}mbit"])
        return args

    def _clear_qdisc(self, interface_name: str) -> None:
        self._run_command(
            ["sudo", "-n", "tc", "qdisc", "del", "dev", interface_name, "root"],
            allow_failure=True,
        )

    def _apply_qdisc(self, *, interface_name: str, runtime_parameters: Dict[str, Any]) -> None:
        netem_args = self._runtime_parameter_netem_args(runtime_parameters)
        if not netem_args:
            self._clear_qdisc(interface_name)
            return
        self._run_command(
            [
                "sudo",
                "-n",
                "tc",
                "qdisc",
                "replace",
                "dev",
                interface_name,
                "root",
                "netem",
                *netem_args,
            ]
        )

    def _participant_host_interfaces(
        self,
        *,
        slot: Dict[str, Any],
        resource: Dict[str, Any],
    ) -> List[str]:
        participant_interfaces = [
            str(endpoint.get("host_interface_name"))
            for endpoint in resource.get("endpoints", [])
        ]
        preconnected = self._preconnected_endpoint(slot)
        if preconnected is not None:
            participant_interfaces.append(str(preconnected.get("host_interface_name")))
        return participant_interfaces

    def _ensure_resource_prepared(
        self,
        *,
        slot: Dict[str, Any],
        resource: Dict[str, Any],
    ) -> None:
        slot_name = str(slot.get("slot_name"))
        resource_key = str(resource.get("resource_key", ""))
        if resource_key and resource_key in self._prepared_resource_keys_by_slot.get(slot_name, set()):
            return
        for endpoint in resource.get("endpoints", []):
            self._create_prepared_veth(
                bridge_name=self._bridge_name(slot_name),
                slot_name=slot_name,
                endpoint=dict(endpoint),
                activate_now=False,
                assign_ip=False,
            )
        if resource_key:
            self._prepared_resource_keys_by_slot.setdefault(slot_name, set()).add(resource_key)

    def _activate_resource(
        self,
        *,
        slot: Dict[str, Any],
        resource: Dict[str, Any],
        runtime_parameters: Dict[str, Any],
        probe: bool = True,
    ) -> None:
        self._ensure_resource_prepared(slot=slot, resource=resource)
        bridge_name = self._bridge_name(str(slot.get("slot_name")))
        for endpoint in resource.get("endpoints", []):
            self._run_command(
                [
                    "sudo",
                    "-n",
                    "sh",
                    "-lc",
                    (
                        f"ip link set dev {shlex.quote(str(endpoint.get('host_interface_name')))} "
                        f"master {shlex.quote(bridge_name)} && "
                        f"ip link set dev {shlex.quote(str(endpoint.get('host_interface_name')))} up"
                    ),
                ]
            )
            self._nsenter(
                node_id=str(endpoint.get("node_id")),
                command=[
                    "sh",
                    "-lc",
                    (
                        f"ip addr flush dev {shlex.quote(str(endpoint.get('interface_name')))} || true; "
                        f"ip addr add {shlex.quote(str(endpoint.get('ip_address')))} "
                        f"dev {shlex.quote(str(endpoint.get('interface_name')))} && "
                        f"ip link set dev {shlex.quote(str(endpoint.get('interface_name')))} up"
                    ),
                ],
            )
        for interface_name in self._participant_host_interfaces(slot=slot, resource=resource):
            self._apply_qdisc(interface_name=interface_name, runtime_parameters=runtime_parameters)
        if probe:
            self._probe_activated_resource(slot=slot, resource=resource)

    def _probe_activated_resource(
        self,
        *,
        slot: Dict[str, Any],
        resource: Dict[str, Any],
    ) -> None:
        # Keep the hot-path validation structural rather than traffic-based.
        # The next runtime phase performs explicit access-link warmup and
        # end-to-end connectivity checks, so the host manager only needs to
        # confirm that the activated resource is present, up, and addressed.
        def ensure_endpoint_ready(endpoint: Dict[str, Any]) -> None:
            host_interface_name = str(endpoint.get("host_interface_name"))
            interface_name = str(endpoint.get("interface_name"))
            node_id = str(endpoint.get("node_id"))
            expected_ip = str(endpoint.get("ip_address"))
            host_link = self._run_command(
                ["sudo", "-n", "ip", "-brief", "link", "show", "dev", host_interface_name],
                allow_failure=True,
            )
            if host_link.returncode != 0 or "UP" not in host_link.stdout:
                raise RuntimeError(
                    f"activation validation failed for host interface {host_interface_name}"
                )
            ns_state = self._nsenter(
                node_id=node_id,
                command=[
                    "sh",
                    "-lc",
                    (
                        f"ip -brief link show dev {shlex.quote(interface_name)} && "
                        f"ip -brief addr show dev {shlex.quote(interface_name)}"
                    ),
                ],
                allow_failure=True,
            )
            if ns_state.returncode != 0 or "UP" not in ns_state.stdout:
                raise RuntimeError(
                    f"activation validation failed for {node_id}:{interface_name} link state"
                )
            if expected_ip not in ns_state.stdout:
                raise RuntimeError(
                    f"activation validation failed for {node_id}:{interface_name} address {expected_ip}"
                )

        endpoints = list(resource.get("endpoints", []))
        preconnected = self._preconnected_endpoint(slot)
        if preconnected is not None:
            ensure_endpoint_ready(preconnected)
        for endpoint in endpoints:
            ensure_endpoint_ready(endpoint)

    def _deactivate_resource(
        self,
        *,
        slot: Dict[str, Any],
        resource: Dict[str, Any],
    ) -> None:
        for interface_name in self._participant_host_interfaces(slot=slot, resource=resource):
            self._clear_qdisc(interface_name)
        for endpoint in resource.get("endpoints", []):
            self._nsenter(
                node_id=str(endpoint.get("node_id")),
                command=[
                    "sh",
                    "-lc",
                    (
                        f"ip link set dev {shlex.quote(str(endpoint.get('interface_name')))} down || true; "
                        f"ip addr flush dev {shlex.quote(str(endpoint.get('interface_name')))} || true"
                    ),
                ],
                allow_failure=True,
            )
            self._run_command(
                [
                    "sudo",
                    "-n",
                    "sh",
                    "-lc",
                    (
                        f"ip link set dev {shlex.quote(str(endpoint.get('host_interface_name')))} down || true; "
                        f"ip link set dev {shlex.quote(str(endpoint.get('host_interface_name')))} nomaster || true"
                    ),
                ],
                allow_failure=True,
            )

    def _deactivate_all_resources(self, *, slot: Dict[str, Any]) -> None:
        for resource in self._candidate_resources(slot).values():
            self._deactivate_resource(slot=slot, resource=resource)

    def _dynamic_endpoints_for_binding(
        self,
        *,
        slot: Dict[str, Any],
        binding: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        slot_type = str(slot.get("slot_type"))
        if slot_type == "ut_access":
            template = dict(slot.get("dynamic_endpoint_template", {}))
            return [
                {
                    "node_id": str(binding.get("satellite_id")),
                    "node_type": str(template.get("node_type")),
                    "interface_name": str(template.get("interface_name")),
                    "ip_address": str(template.get("ip_address")),
                }
            ]
        if slot_type == "gw_access":
            template = dict(slot.get("dynamic_endpoint_template", {}))
            return [
                {
                    "node_id": str(binding.get("satellite_id")),
                    "node_type": str(template.get("node_type")),
                    "interface_name": str(template.get("interface_name")),
                    "ip_address": str(template.get("ip_address")),
                }
            ]
        endpoint_a_template = dict(slot.get("endpoint_a_template", {}))
        endpoint_b_template = dict(slot.get("endpoint_b_template", {}))
        return [
            {
                "node_id": str(binding.get("satellite_a_id")),
                "node_type": str(endpoint_a_template.get("node_type")),
                "interface_name": str(endpoint_a_template.get("interface_name")),
                "ip_address": str(endpoint_a_template.get("ip_address")),
                "logical_direction": str(binding.get("satellite_a_direction")),
            },
            {
                "node_id": str(binding.get("satellite_b_id")),
                "node_type": str(endpoint_b_template.get("node_type")),
                "interface_name": str(endpoint_b_template.get("interface_name")),
                "ip_address": str(endpoint_b_template.get("ip_address")),
                "logical_direction": str(binding.get("satellite_b_direction")),
            },
        ]

    def _persist_operation_state(
        self,
        *,
        link_state_path: Path,
        slot: Dict[str, Any],
        operation: Dict[str, Any],
    ) -> None:
        with self._lock:
            link_state = _load_json(link_state_path)
            updated_slots: List[Dict[str, Any]] = []
            for slot_entry in link_state.get("slots", []):
                if str(slot_entry.get("slot_name")) != str(slot.get("slot_name")):
                    updated_slots.append(slot_entry)
                    continue
                updated_entry = dict(slot_entry)
                updated_entry["binding"] = dict(operation.get("target_binding", {}) or {}) or None
                updated_entry["occupied"] = bool(operation.get("target_occupied", False))
                updated_entry["runtime_parameters"] = dict(
                    operation.get("target_runtime_parameters", {}) or {}
                )
                if updated_entry["occupied"] and updated_entry["binding"]:
                    updated_entry["dynamic_endpoints"] = self._dynamic_endpoints_for_binding(
                        slot=slot,
                        binding=dict(updated_entry["binding"]),
                    )
                else:
                    updated_entry["dynamic_endpoints"] = []
                updated_slots.append(updated_entry)
            link_state["slots"] = updated_slots
            link_state["runtime_initialized"] = True
            link_state["host_runtime_backend"] = self.execution_transport
            link_state["host_runtime_prepared"] = True
            _write_json(link_state_path, link_state)

    def apply_operation(
        self,
        *,
        controller: DirectHostRuntimeContext,
        link_state_path: Path,
        operation: Dict[str, Any],
        state_by_name: Dict[str, Dict[str, Any]] | None = None,
        persist_link_state: bool = True,
    ) -> Dict[str, Any]:
        topology = dict(controller.topology)
        slot_inventory = self._inventory(topology, Path(controller.topology_path))
        slot_name = str(operation.get("slot_name"))
        slot = slot_inventory.get(slot_name)
        if slot is None:
            raise RuntimeError(f"missing slot inventory for {slot_name}")
        slot_type = str(slot.get("slot_type"))
        operation_name = str(operation.get("operation"))
        source_binding = dict(operation.get("source_binding", {}) or {})
        target_binding = dict(operation.get("target_binding", {}) or {})
        target_runtime_parameters = dict(
            operation.get("target_runtime_parameters", {}) or {}
        )

        started_at = time.perf_counter()
        qdisc_apply_wall_seconds = 0.0
        metadata_update_wall_seconds = 0.0
        if operation_name == "update_slot_parameters":
            if target_binding:
                target_resource = self._resource_for_binding(slot=slot, binding=target_binding)
                qdisc_started_at = time.perf_counter()
                for interface_name in self._participant_host_interfaces(
                    slot=slot,
                    resource=target_resource,
                ):
                    self._apply_qdisc(
                        interface_name=interface_name,
                        runtime_parameters=target_runtime_parameters,
                    )
                qdisc_apply_wall_seconds = time.perf_counter() - qdisc_started_at
        else:
            if operation_name in {"deactivate_slot_attachment", "rebind_slot_attachment"}:
                if source_binding:
                    source_resource = self._resource_for_binding(
                        slot=slot,
                        binding=source_binding,
                    )
                    self._deactivate_resource(slot=slot, resource=source_resource)
            if operation_name in {"activate_slot_attachment", "rebind_slot_attachment"}:
                if target_binding:
                    target_resource = self._resource_for_binding(
                        slot=slot,
                        binding=target_binding,
                    )
                    self._activate_resource(
                        slot=slot,
                        resource=target_resource,
                        runtime_parameters=target_runtime_parameters,
                    )
                qdisc_apply_wall_seconds = time.perf_counter() - started_at
        metadata_started_at = time.perf_counter()
        if persist_link_state:
            self._persist_operation_state(
                link_state_path=link_state_path,
                slot=slot,
                operation=operation,
            )
            metadata_update_wall_seconds = time.perf_counter() - metadata_started_at
        if state_by_name is not None:
            state_by_name[slot_name] = _state_entry_from_operation(operation, source=False)
        slot_outcome = _slot_outcome_from_operation(slot_name, operation)
        return {
            "slot_name": slot_name,
            "slot_type": slot_type,
            "operation": operation_name,
            "delta_category": slot_outcome["category"],
            "change_class": slot_outcome["change_class"],
            "reused_slot_runtime": slot_outcome["reused"],
            "resulting_state": _slot_summary(_state_entry_from_operation(operation, source=False)),
            "wall_seconds": round(time.perf_counter() - started_at, 3),
            "qdisc_apply_wall_seconds": round(qdisc_apply_wall_seconds, 6),
            "metadata_update_wall_seconds": round(metadata_update_wall_seconds, 6),
            "controller_stdout": "",
        }

    def persist_runtime_state(
        self,
        *,
        controller: DirectHostRuntimeContext,
        link_state_path: Path,
        state_by_name: Dict[str, Dict[str, Any]],
    ) -> None:
        with self._lock:
            topology = dict(controller.topology)
            slot_inventory = self._inventory(topology, Path(controller.topology_path))
            link_state = _load_json(link_state_path)
            updated_slots: List[Dict[str, Any]] = []
            for slot_entry in link_state.get("slots", []):
                slot_name = str(slot_entry.get("slot_name"))
                updated_state = state_by_name.get(slot_name)
                if updated_state is None:
                    updated_slots.append(slot_entry)
                    continue
                updated_entry = dict(slot_entry)
                updated_entry["binding"] = dict(updated_state.get("binding") or {}) or None
                updated_entry["occupied"] = bool(updated_state.get("occupied", False))
                updated_entry["runtime_parameters"] = dict(
                    updated_state.get("runtime_parameters") or {}
                )
                slot = slot_inventory.get(slot_name)
                if updated_entry["occupied"] and updated_entry["binding"] and slot is not None:
                    updated_entry["dynamic_endpoints"] = self._dynamic_endpoints_for_binding(
                        slot=slot,
                        binding=dict(updated_entry["binding"]),
                    )
                else:
                    updated_entry["dynamic_endpoints"] = []
                updated_slots.append(updated_entry)
            link_state["slots"] = updated_slots
            link_state["runtime_initialized"] = True
            link_state["host_runtime_backend"] = self.execution_transport
            link_state["host_runtime_prepared"] = True
            _write_json(link_state_path, link_state)


class InProcessHostRuntimeManager:
    """Own host-side slot state and apply mutation deltas through a runtime envelope."""

    manager_name = "in_process_host_runtime_manager"
    manager_kind = "host_runtime_manager"
    manager_version = "0.2.0"
    executor_name = "minimal_real_network_mutation_executor"
    executor_kind = "host_side_slot_link_runtime_boundary"
    executor_version = "0.2.0"

    def __init__(
        self,
        *,
        max_host_workers: int = 1,
        installer: Any | None = None,
    ) -> None:
        self._max_host_workers = max(1, int(max_host_workers))
        self._installer = installer or DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        self._cached_state_by_slot: Dict[str, Dict[str, Any]] = {}
        self._cached_link_state_path: str | None = None
        self._runtime_initialized = False
        self._last_mutation_id: str | None = None
        self._last_target_snapshot_id: str | None = None
        self._last_target_slice_id: str | None = None
        self._last_runtime_state_refresh_epoch_seconds: float | None = None

    def _refresh_runtime_state(self, *, link_state_path: Path) -> Dict[str, Dict[str, Any]]:
        state = self._installer.state_by_name(link_state_path=link_state_path)
        return self._replace_cached_runtime_state(
            link_state_path=link_state_path,
            state=state,
            runtime_initialized=bool(_load_json(link_state_path).get("runtime_initialized", False)),
        )

    def _replace_cached_runtime_state(
        self,
        *,
        link_state_path: Path,
        state: Dict[str, Dict[str, Any]],
        runtime_initialized: bool,
    ) -> Dict[str, Dict[str, Any]]:
        self._cached_state_by_slot = _clone_slot_state(state)
        self._cached_link_state_path = str(link_state_path)
        self._runtime_initialized = bool(runtime_initialized)
        self._last_runtime_state_refresh_epoch_seconds = round(time.time(), 3)
        return _clone_slot_state(self._cached_state_by_slot)

    def prewarm(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        reconcile_slot_names: Sequence[str] | None = None,
        prepared_resource_scope_by_slot: Mapping[str, Sequence[str] | set[str]] | None = None,
        progress_callback: Callable[[Dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        started_at = time.perf_counter()
        controller = self._installer.controller(
            topology_path=topology_path,
            link_state_path=link_state_path,
            reconcile_slot_names=reconcile_slot_names,
            prepared_resource_scope_by_slot=prepared_resource_scope_by_slot,
            record_prewarm_reconcile=True,
            setup_progress_callback=progress_callback,
        )
        timing = dict(self._installer.consume_setup_timing())
        state = self._refresh_runtime_state(link_state_path=link_state_path)
        return {
            "prewarm_completed": True,
            "prewarm_wall_seconds": round(time.perf_counter() - started_at, 6),
            "manager_timing_breakdown": {
                key: round(float(value), 6) for key, value in sorted(timing.items())
            },
            "preparation_summary": self._installer.last_setup_summary(),
            "host_runtime_state": self.public_state_summary(),
            "managed_slot_count": len(state),
            "topology_path": str(topology_path),
            "link_state_path": str(link_state_path),
            "reconcile_slot_names": list(reconcile_slot_names or []),
            "controller_topology_path": controller.topology_path,
        }

    def public_state_summary(self) -> Dict[str, Any]:
        return {
            "manager_name": self.manager_name,
            "manager_version": self.manager_version,
            "owned_slot_state_count": len(self._cached_state_by_slot),
            "runtime_initialized": self._runtime_initialized,
            "last_mutation_id": self._last_mutation_id,
            "last_target_snapshot_id": self._last_target_snapshot_id,
            "last_target_slice_id": self._last_target_slice_id,
            "cached_link_state_path": self._cached_link_state_path,
            "last_runtime_state_refresh_epoch_seconds": (
                self._last_runtime_state_refresh_epoch_seconds
            ),
            "max_host_workers": self._max_host_workers,
            "installer_runtime": self._installer.public_runtime_summary(),
        }

    def _verify_plan_state(
        self,
        *,
        state_by_name: Dict[str, Dict[str, Any]],
        operations: Sequence[Dict[str, Any]],
        source: bool,
    ) -> Dict[str, Any]:
        mismatches: List[Dict[str, Any]] = []
        for operation in operations:
            slot_name = str(operation.get("slot_name"))
            actual = _slot_summary(state_by_name.get(slot_name))
            expected = _expected_summary(operation, source=source)
            if actual != expected:
                mismatches.append(
                    {
                        "slot_name": slot_name,
                        "expected": expected,
                        "actual": actual,
                    }
                )
        return {
            "checked_slot_count": len(operations),
            "mismatches": mismatches,
            "all_valid": not mismatches,
        }

    def _parallelism_decision(self, operations: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        requested_workers = self._max_host_workers
        if requested_workers <= 1:
            return {
                "requested_workers": requested_workers,
                "effective_workers": 1,
                "parallel_execution_used": False,
                "reason": "single_worker_requested",
                "execution_groups": [
                    {
                        "group_index": 0,
                        "operation_indexes": list(range(len(operations))),
                        "operation_count": len(operations),
                        "parallelized": False,
                    }
                ],
            }
        if len(operations) <= 1:
            return {
                "requested_workers": requested_workers,
                "effective_workers": 1,
                "parallel_execution_used": False,
                "reason": "insufficient_parallel_work",
                "execution_groups": [
                    {
                        "group_index": 0,
                        "operation_indexes": list(range(len(operations))),
                        "operation_count": len(operations),
                        "parallelized": False,
                    }
                ],
            }
        if not self._installer.supports_bounded_parallelism:
            return {
                "requested_workers": requested_workers,
                "effective_workers": 1,
                "parallel_execution_used": False,
                "reason": "transitional_controller_transport_requires_single_writer",
                "execution_groups": [
                    {
                        "group_index": 0,
                        "operation_indexes": list(range(len(operations))),
                        "operation_count": len(operations),
                        "parallelized": False,
                    }
                ],
            }
        groups: List[List[int]] = []
        group_owner_keys: List[set[str]] = []
        for index, operation in enumerate(operations):
            owner_keys = _operation_owner_keys(operation)
            placed = False
            for group_indexes, seen_keys in zip(groups, group_owner_keys):
                if seen_keys & owner_keys:
                    continue
                group_indexes.append(index)
                seen_keys.update(owner_keys)
                placed = True
                break
            if not placed:
                groups.append([index])
                group_owner_keys.append(set(owner_keys))
        max_group_size = max((len(group) for group in groups), default=1)
        effective_workers = max(1, min(requested_workers, max_group_size))
        parallel_execution_used = effective_workers > 1 and any(len(group) > 1 for group in groups)
        return {
            "requested_workers": requested_workers,
            "effective_workers": effective_workers,
            "parallel_execution_used": parallel_execution_used,
            "reason": (
                "parallel_execution_enabled"
                if parallel_execution_used
                else "conflict_safe_serial_groups_selected"
            ),
            "execution_groups": [
                {
                    "group_index": group_index,
                    "operation_indexes": list(group),
                    "operation_count": len(group),
                    "parallelized": len(group) > 1 and effective_workers > 1,
                }
                for group_index, group in enumerate(groups)
            ],
        }

    def _prepare_envelope(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        network_mutation_plan: NetworkMutationPlan,
    ) -> tuple[HostRuntimeEnvelope, Dict[str, float]]:
        state_refresh_started_at = time.perf_counter()
        observed_state = self._refresh_runtime_state(link_state_path=link_state_path)
        initial_state_refresh_wall_seconds = time.perf_counter() - state_refresh_started_at
        envelope_model_started_at = time.perf_counter()
        target_state = _clone_slot_state(observed_state)
        managed_slot_names = [
            str(slot_name)
            for slot_name in network_mutation_plan.target_scope.get("managed_slot_names", [])
        ]
        for operation in network_mutation_plan.operations:
            target_state[str(operation.get("slot_name"))] = _state_entry_from_operation(
                operation,
                source=False,
            )
        parallelism = self._parallelism_decision(network_mutation_plan.operations)
        envelope_seed = {
            "mutation_id": network_mutation_plan.mutation_id,
            "target_scope": dict(network_mutation_plan.target_scope),
            "managed_slot_names": managed_slot_names,
            "operation_count": len(network_mutation_plan.operations),
            "requested_workers": self._max_host_workers,
        }
        envelope = HostRuntimeEnvelope(
            envelope_id=f"hostenv-{_fingerprint(envelope_seed)}",
            mutation_id=network_mutation_plan.mutation_id,
            topology_path=str(topology_path),
            link_state_path=str(link_state_path),
            target_scope=dict(network_mutation_plan.target_scope),
            managed_slot_names=managed_slot_names,
            observed_state=observed_state,
            target_state=target_state,
            operations=[dict(operation) for operation in network_mutation_plan.operations],
            parallelism=parallelism,
        )
        return envelope, {
            "initial_state_refresh_wall_seconds": initial_state_refresh_wall_seconds,
            "envelope_model_wall_seconds": time.perf_counter() - envelope_model_started_at,
        }

    def _delta_summary(self, envelope: HostRuntimeEnvelope) -> Dict[str, Any]:
        operations_by_slot = {
            str(operation.get("slot_name")): dict(operation)
            for operation in envelope.operations
        }
        slot_outcomes = [
            _slot_outcome_from_operation(slot_name, operations_by_slot.get(slot_name))
            for slot_name in envelope.managed_slot_names
        ]
        return {
            "managed_slot_count": len(envelope.managed_slot_names),
            "planned_operation_count": len(envelope.operations),
            "skipped_slot_count": sum(
                1 for outcome in slot_outcomes if outcome["category"] == "skipped"
            ),
            "reused_slot_count": sum(
                1 for outcome in slot_outcomes if bool(outcome.get("reused"))
            ),
            "changed_slot_count": sum(
                1 for outcome in slot_outcomes if outcome["category"] == "changed"
            ),
            "created_slot_count": sum(
                1 for outcome in slot_outcomes if outcome["category"] == "created"
            ),
            "deleted_slot_count": sum(
                1 for outcome in slot_outcomes if outcome["category"] == "deleted"
            ),
            "slot_outcomes": slot_outcomes,
        }

    def _boundary_summary(self, *, parallelism: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "executor_name": self.executor_name,
            "executor_kind": self.executor_kind,
            "executor_version": self.executor_version,
            "real_execution": True,
            "consumer": "host_network_runtime_manager",
            "envelope_kind": "host_runtime_envelope",
            "manager_name": self.manager_name,
            "manager_kind": self.manager_kind,
            "manager_version": self.manager_version,
            "installer_name": self._installer.installer_name,
            "installer_kind": self._installer.installer_kind,
            "installer_version": self._installer.installer_version,
            "execution_transport": self._installer.execution_transport,
            "future_runtime_target": "host_runtime_manager_daemon",
            "concurrency_model": "bounded_parallelism_gated",
            "requested_max_host_workers": parallelism["requested_workers"],
            "effective_max_host_workers": parallelism["effective_workers"],
            "parallel_execution_used": parallelism["parallel_execution_used"],
        }

    def _apply_operations(
        self,
        *,
        controller: Any,
        link_state_path: Path,
        operations: Sequence[Dict[str, Any]],
        working_state: Dict[str, Dict[str, Any]],
        parallelism: Dict[str, Any],
    ) -> tuple[
        List[Dict[str, Any]],
        List[Dict[str, Any]],
        Dict[str, Any],
        Dict[str, float],
        Dict[str, Any],
    ]:
        apply_results: List[Dict[str, Any]] = []
        applied_operations: List[Dict[str, Any]] = []
        apply_started_at = time.perf_counter()
        rollback_started_at = 0.0
        automatic_rollback_wall_seconds = 0.0
        batched_state_persist_wall_seconds = 0.0
        rollback_failure_reason: str | None = None
        failed_operations: List[Dict[str, Any]] = []
        defer_state_persist = bool(
            getattr(self._installer, "supports_batched_link_state_persist", False)
        )
        execution_groups = list(parallelism.get("execution_groups", []))
        group_summaries: List[Dict[str, Any]] = []
        if parallelism.get("parallel_execution_used"):
            for group in execution_groups:
                group_indexes = list(group.get("operation_indexes", []))
                group_started_at = time.perf_counter()
                results_by_index: Dict[int, Dict[str, Any]] = {}
                failures: List[tuple[int, Exception]] = []
                with ThreadPoolExecutor(
                    max_workers=min(
                        int(parallelism.get("effective_workers", 1)),
                        max(1, len(group_indexes)),
                    )
                ) as executor:
                    future_to_index = {
                        executor.submit(
                            self._installer.apply_operation,
                            controller=controller,
                            link_state_path=link_state_path,
                            operation=operations[index],
                            state_by_name=None,
                            persist_link_state=not defer_state_persist,
                        ): index
                        for index in group_indexes
                    }
                    for future in as_completed(future_to_index):
                        index = future_to_index[future]
                        try:
                            results_by_index[index] = future.result()
                        except Exception as exc:  # noqa: BLE001
                            failures.append((index, exc))
                group_summaries.append(
                    _parallel_group_summary(
                        group_index=int(group.get("group_index", len(group_summaries))),
                        operation_indexes=group_indexes,
                        operations=operations,
                        wall_seconds=time.perf_counter() - group_started_at,
                    )
                )
                for index in group_indexes:
                    result = results_by_index.get(index)
                    if result is None:
                        continue
                    operation = operations[index]
                    apply_results.append(result)
                    applied_operations.append(dict(operation))
                    working_state[str(operation.get("slot_name"))] = _state_entry_from_operation(
                        operation,
                        source=False,
                    )
                if failures:
                    failed_operations = [
                        {
                            "operation_index": index,
                            "slot_name": str(operations[index].get("slot_name")),
                            "operation": str(operations[index].get("operation")),
                            "slot_type": str(operations[index].get("slot_type", "unknown")),
                            "error": str(exc),
                        }
                        for index, exc in sorted(failures, key=lambda item: item[0])
                    ]
                    rollback_started_at = time.perf_counter()
                    for operation in reversed(applied_operations):
                        try:
                            self._installer.apply_operation(
                                controller=controller,
                                link_state_path=link_state_path,
                                operation=_inverse_operation(operation),
                                state_by_name=None,
                            )
                        except Exception as exc:  # noqa: BLE001
                            rollback_failure_reason = str(exc)
                            break
                    automatic_rollback_wall_seconds = time.perf_counter() - rollback_started_at
                    failure_index, failure = sorted(failures, key=lambda item: item[0])[0]
                    rollback_suffix = (
                        ""
                        if rollback_failure_reason is None
                        else f"; automatic rollback failed: {rollback_failure_reason}"
                    )
                    raise NetworkMutationExecutionError(
                        f"parallel host mutation failed at operation index {failure_index}: "
                        f"{failure}{rollback_suffix}"
                    )
        else:
            group_started_at = time.perf_counter()
            try:
                for operation in operations:
                    apply_results.append(
                        self._installer.apply_operation(
                            controller=controller,
                            link_state_path=link_state_path,
                            operation=operation,
                            state_by_name=working_state,
                            persist_link_state=not defer_state_persist,
                        )
                    )
                    applied_operations.append(dict(operation))
            except Exception as exc:  # noqa: BLE001
                failed_operations = [
                    {
                        "operation_index": len(applied_operations),
                        "slot_name": str(operation.get("slot_name")),
                        "operation": str(operation.get("operation")),
                        "slot_type": str(operation.get("slot_type", "unknown")),
                        "error": str(exc),
                    }
                ]
                if applied_operations:
                    rollback_started_at = time.perf_counter()
                    for rollback_operation in reversed(applied_operations):
                        try:
                            self._installer.apply_operation(
                                controller=controller,
                                link_state_path=link_state_path,
                                operation=_inverse_operation(rollback_operation),
                                state_by_name=None,
                            )
                        except Exception as rollback_exc:  # noqa: BLE001
                            rollback_failure_reason = str(rollback_exc)
                            break
                    automatic_rollback_wall_seconds = (
                        time.perf_counter() - rollback_started_at
                    )
                rollback_suffix = (
                    ""
                    if rollback_failure_reason is None
                    else f"; automatic rollback failed: {rollback_failure_reason}"
                )
                raise NetworkMutationExecutionError(f"{exc}{rollback_suffix}") from exc
            group_summaries.append(
                _parallel_group_summary(
                    group_index=0,
                    operation_indexes=list(range(len(operations))),
                    operations=operations,
                    wall_seconds=time.perf_counter() - group_started_at,
                )
            )
        if defer_state_persist and applied_operations:
            state_persist_started_at = time.perf_counter()
            self._installer.persist_runtime_state(
                controller=controller,
                link_state_path=link_state_path,
                state_by_name=working_state,
            )
            batched_state_persist_wall_seconds = (
                time.perf_counter() - state_persist_started_at
            )
        apply_operations_wall_seconds = time.perf_counter() - apply_started_at
        self._replace_cached_runtime_state(
            link_state_path=link_state_path,
            state=working_state,
            runtime_initialized=True,
        )
        post_apply_state_refresh_wall_seconds = 0.0
        verify_started_at = time.perf_counter()
        apply_verification = self._verify_plan_state(
            state_by_name=working_state,
            operations=operations,
            source=False,
        )
        post_apply_verification_wall_seconds = time.perf_counter() - verify_started_at
        if not apply_verification["all_valid"]:
            raise NetworkMutationExecutionError(
                "host-side slot state does not match the NetworkMutationPlan target state"
            )
        return apply_results, applied_operations, apply_verification, {
            "operation_apply_wall_seconds": apply_operations_wall_seconds,
            "batched_state_persist_wall_seconds": batched_state_persist_wall_seconds,
            "post_apply_state_refresh_wall_seconds": post_apply_state_refresh_wall_seconds,
            "post_apply_verification_wall_seconds": post_apply_verification_wall_seconds,
            "automatic_rollback_wall_seconds": automatic_rollback_wall_seconds,
        }, {
            "requested_workers": int(parallelism.get("requested_workers", 1)),
            "effective_workers": int(parallelism.get("effective_workers", 1)),
            "parallel_execution_used": bool(parallelism.get("parallel_execution_used")),
            "reason": str(parallelism.get("reason", "serial_execution_selected")),
            "serial_group_count": sum(1 for group in group_summaries if not group["parallelized"]),
            "parallel_group_count": sum(1 for group in group_summaries if group["parallelized"]),
            "group_summaries": group_summaries,
            "failed_operations": failed_operations,
        }

    def apply(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        network_mutation_plan: NetworkMutationPlan,
        reconcile_slot_names: Sequence[str] | None = None,
        reconcile_mode: str = "auto",
    ) -> NetworkMutationApplyResult:
        manager_started_at = time.perf_counter()
        manager_timing_breakdown: Dict[str, float] = {
            "controller_setup_wall_seconds": 0.0,
            "runtime_prepare_wall_seconds": 0.0,
            "runtime_reconcile_wall_seconds": 0.0,
            "trusted_reconcile_repair_wall_seconds": 0.0,
            "initial_state_refresh_wall_seconds": 0.0,
            "envelope_model_wall_seconds": 0.0,
            "preflight_verification_wall_seconds": 0.0,
            "operation_apply_wall_seconds": 0.0,
            "batched_state_persist_wall_seconds": 0.0,
            "post_apply_state_refresh_wall_seconds": 0.0,
            "post_apply_verification_wall_seconds": 0.0,
            "bootstrap_wall_seconds": 0.0,
            "delta_accounting_wall_seconds": 0.0,
            "manager_resync_reconciliation_wall_seconds": 0.0,
            "total_wall_seconds": 0.0,
        }
        if reconcile_mode == "auto":
            effective_reconcile_mode = (
                "apply" if self._last_mutation_id is None else "trusted_skip"
            )
        else:
            effective_reconcile_mode = str(reconcile_mode)
        controller_setup_started_at = time.perf_counter()
        controller = self._installer.controller(
            topology_path=topology_path,
            link_state_path=link_state_path,
            reconcile_slot_names=reconcile_slot_names,
            consume_matching_prewarm=self._last_mutation_id is None,
            reconcile_mode=effective_reconcile_mode,
        )
        manager_timing_breakdown.update(self._installer.consume_setup_timing())
        manager_timing_breakdown["controller_setup_wall_seconds"] = (
            time.perf_counter() - controller_setup_started_at
        )
        envelope, envelope_timing = self._prepare_envelope(
            topology_path=topology_path,
            link_state_path=link_state_path,
            network_mutation_plan=network_mutation_plan,
        )
        manager_timing_breakdown.update(envelope_timing)
        working_state = _clone_slot_state(envelope.observed_state)
        preflight_verify_started_at = time.perf_counter()
        preflight_verification = self._verify_plan_state(
            state_by_name=envelope.observed_state,
            operations=envelope.operations,
            source=True,
        )
        manager_timing_breakdown["preflight_verification_wall_seconds"] = (
            time.perf_counter() - preflight_verify_started_at
        )
        if not preflight_verification["all_valid"]:
            raise NetworkMutationExecutionError(
                "host-side slot state does not match the NetworkMutationPlan source state"
            )

        apply_results: List[Dict[str, Any]] = []
        applied_operations: List[Dict[str, Any]] = []
        failure_reason: str | None = None
        bootstrap_summary: Dict[str, Any] | None = None
        parallel_execution_summary: Dict[str, Any] | None = None

        try:
            (
                apply_results,
                applied_operations,
                apply_verification,
                apply_timing,
                parallel_execution_summary,
            ) = self._apply_operations(
                controller=controller,
                link_state_path=link_state_path,
                operations=envelope.operations,
                working_state=working_state,
                parallelism=envelope.parallelism,
            )
            manager_timing_breakdown.update(apply_timing)
        except Exception as exc:  # noqa: BLE001
            failure_reason = str(exc)
            if not applied_operations:
                if effective_reconcile_mode == "trusted_skip":
                    repair_started_at = time.perf_counter()
                    repair_controller = self._installer.controller(
                        topology_path=topology_path,
                        link_state_path=link_state_path,
                        reconcile_slot_names=reconcile_slot_names,
                        reconcile_mode="apply",
                    )
                    manager_timing_breakdown.update(self._installer.consume_setup_timing())
                    manager_timing_breakdown["controller_setup_wall_seconds"] += (
                        time.perf_counter() - repair_started_at
                    )
                    manager_timing_breakdown["trusted_reconcile_repair_wall_seconds"] += (
                        time.perf_counter() - repair_started_at
                    )
                    try:
                        (
                            apply_results,
                            applied_operations,
                            apply_verification,
                            apply_timing,
                            parallel_execution_summary,
                        ) = self._apply_operations(
                            controller=repair_controller,
                            link_state_path=link_state_path,
                            operations=envelope.operations,
                            working_state=working_state,
                            parallelism=envelope.parallelism,
                        )
                        manager_timing_breakdown.update(apply_timing)
                        controller = repair_controller
                        effective_reconcile_mode = "repair_apply"
                        failure_reason = None
                    except Exception as repair_exc:  # noqa: BLE001
                        failure_reason = str(repair_exc)
                bootstrap_started_at = time.perf_counter()
                if failure_reason is not None:
                    bootstrap_summary = self._installer.bootstrap_runtime_if_needed(
                        controller=controller,
                        link_state_path=link_state_path,
                        failure_reason=failure_reason,
                        reconcile_slot_names=reconcile_slot_names,
                    )
                    manager_timing_breakdown["bootstrap_wall_seconds"] += (
                        time.perf_counter() - bootstrap_started_at
                    )
                    if bootstrap_summary is not None:
                        failure_reason = None
                        (
                            apply_results,
                            applied_operations,
                            apply_verification,
                            apply_timing,
                            parallel_execution_summary,
                        ) = self._apply_operations(
                            controller=controller,
                            link_state_path=link_state_path,
                            operations=envelope.operations,
                            working_state=working_state,
                            parallelism=envelope.parallelism,
                        )
                        manager_timing_breakdown.update(apply_timing)
                    else:
                        state_refresh_started_at = time.perf_counter()
                        apply_state = self._refresh_runtime_state(link_state_path=link_state_path)
                        manager_timing_breakdown["post_apply_state_refresh_wall_seconds"] = (
                            time.perf_counter() - state_refresh_started_at
                        )
                        verify_started_at = time.perf_counter()
                        apply_verification = self._verify_plan_state(
                            state_by_name=apply_state,
                            operations=envelope.operations,
                            source=False,
                        )
                        manager_timing_breakdown["post_apply_verification_wall_seconds"] = (
                            time.perf_counter() - verify_started_at
                        )
            else:
                state_refresh_started_at = time.perf_counter()
                apply_state = self._refresh_runtime_state(link_state_path=link_state_path)
                manager_timing_breakdown["post_apply_state_refresh_wall_seconds"] = (
                    time.perf_counter() - state_refresh_started_at
                )
                verify_started_at = time.perf_counter()
                apply_verification = self._verify_plan_state(
                    state_by_name=apply_state,
                    operations=envelope.operations,
                    source=False,
                )
                manager_timing_breakdown["post_apply_verification_wall_seconds"] = (
                    time.perf_counter() - verify_started_at
                )

        self._last_mutation_id = network_mutation_plan.mutation_id
        self._last_target_snapshot_id = str(
            network_mutation_plan.target_scope.get("target_snapshot_id")
        )
        self._last_target_slice_id = str(network_mutation_plan.target_scope.get("target_slice_id"))
        delta_summary_started_at = time.perf_counter()
        delta_summary = self._delta_summary(envelope)
        delta_summary["executed_operation_count"] = len(apply_results)
        manager_timing_breakdown["delta_accounting_wall_seconds"] = (
            time.perf_counter() - delta_summary_started_at
        )
        manager_timing_breakdown["manager_resync_reconciliation_wall_seconds"] = (
            manager_timing_breakdown["initial_state_refresh_wall_seconds"]
            + manager_timing_breakdown["preflight_verification_wall_seconds"]
            + manager_timing_breakdown["post_apply_state_refresh_wall_seconds"]
            + manager_timing_breakdown["post_apply_verification_wall_seconds"]
        )
        manager_timing_breakdown["total_wall_seconds"] = (
            time.perf_counter() - manager_started_at
        )

        execution_seed = {
            "mutation_id": network_mutation_plan.mutation_id,
            "operation_count": len(network_mutation_plan.operations),
            "executor_name": self.executor_name,
        }
        execution_id = f"hostmut-{_fingerprint(execution_seed)}"
        return NetworkMutationApplyResult(
            execution_id=execution_id,
            boundary=self._boundary_summary(parallelism=envelope.parallelism),
            target_scope=dict(network_mutation_plan.target_scope),
            preflight=preflight_verification,
            envelope={
                "envelope_id": envelope.envelope_id,
                "managed_slot_count": len(envelope.managed_slot_names),
                "parallelism": dict(envelope.parallelism),
            },
            apply_results=apply_results,
            apply_verification=apply_verification,
            outcome={
                "apply_success": failure_reason is None
                and bool(apply_verification.get("all_valid")),
                "failure_reason": failure_reason,
                "operation_count": len(network_mutation_plan.operations),
                "applied_operations": applied_operations,
                "operation_timing_summary": _summarize_operation_results(apply_results),
                "delta_summary": delta_summary,
                "parallelism": dict(envelope.parallelism),
                "parallel_execution_summary": dict(parallel_execution_summary or {}),
                "manager_timing_breakdown": {
                    key: round(value, 6)
                    for key, value in sorted(manager_timing_breakdown.items())
                },
                "bootstrap_summary": bootstrap_summary,
                "preparation_summary": self._installer.last_setup_summary(),
                "host_runtime_state": self.public_state_summary(),
                "effective_reconcile_mode": effective_reconcile_mode,
            },
        )

    def rollback(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        network_mutation_plan: NetworkMutationPlan,
        applied_operations: Sequence[Dict[str, Any]],
    ) -> NetworkMutationRollbackResult:
        rollback_results: List[Dict[str, Any]] = []
        rollback_failure_reason: str | None = None
        rollback_controller = self._installer.controller(
            topology_path=topology_path,
            link_state_path=link_state_path,
        )
        rollback_state = self._refresh_runtime_state(link_state_path=link_state_path)
        try:
            for operation in reversed(list(applied_operations)):
                rollback_results.append(
                    self._installer.apply_operation(
                        controller=rollback_controller,
                        link_state_path=link_state_path,
                        operation=_inverse_operation(operation),
                        state_by_name=rollback_state,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            rollback_failure_reason = str(exc)
        rollback_state = self._refresh_runtime_state(link_state_path=link_state_path)
        rollback_verification = self._verify_plan_state(
            state_by_name=rollback_state,
            operations=network_mutation_plan.operations,
            source=True,
        )
        if rollback_failure_reason is not None:
            rollback_verification["rollback_failure_reason"] = rollback_failure_reason

        execution_seed = {
            "mutation_id": network_mutation_plan.mutation_id,
            "operation_count": len(applied_operations),
            "executor_name": self.executor_name,
            "phase": "rollback",
        }
        execution_id = f"hostmut-{_fingerprint(execution_seed)}"
        return NetworkMutationRollbackResult(
            execution_id=execution_id,
            boundary=self._boundary_summary(
                parallelism=self._parallelism_decision(applied_operations)
            ),
            rollback_results=rollback_results,
            rollback_verification=rollback_verification,
            outcome={
                "rollback_completed": bool(rollback_verification.get("all_valid")),
                "failure_reason": rollback_failure_reason,
                "operation_count": len(applied_operations),
                "operation_timing_summary": _summarize_operation_results(rollback_results),
                "host_runtime_state": self.public_state_summary(),
            },
        )

    def rollback_many(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        rollback_requests: Sequence[Mapping[str, Any]],
        progress_callback: Callable[[Dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        """Rollback a final teardown sequence with one state refresh window.

        Per-epoch rollback still executes in reverse epoch order and preserves
        the same per-entry verification boundary, but avoids the old final
        teardown pattern of refreshing the full host runtime state before and
        after every individual epoch rollback.
        """
        requests = [dict(request) for request in rollback_requests]
        final_source_operations_by_slot: Dict[str, Dict[str, Any]] = {}
        for request in requests:
            for operation in list(request.get("applied_operations") or []):
                slot_name = str(operation.get("slot_name"))
                final_source_operations_by_slot.setdefault(slot_name, dict(operation))

        rollback_controller = self._installer.controller(
            topology_path=topology_path,
            link_state_path=link_state_path,
        )
        refresh_started_at = time.perf_counter()
        rollback_state = self._refresh_runtime_state(link_state_path=link_state_path)
        initial_refresh_wall_seconds = time.perf_counter() - refresh_started_at

        host_rollbacks: List[Dict[str, Any]] = []
        all_rollback_results: List[Dict[str, Any]] = []
        rollback_failure_reason: str | None = None
        total_requests = len(requests)
        for rollback_index, request in enumerate(reversed(requests), start=1):
            network_mutation_plan = request["host_plan"]
            applied_operations = list(request.get("applied_operations") or [])
            entry_started_at = time.perf_counter()
            entry_results: List[Dict[str, Any]] = []
            entry_failure_reason: str | None = None
            try:
                for operation in reversed(applied_operations):
                    entry_results.append(
                        self._installer.apply_operation(
                            controller=rollback_controller,
                            link_state_path=link_state_path,
                            operation=_inverse_operation(operation),
                            state_by_name=rollback_state,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                entry_failure_reason = str(exc)
                rollback_failure_reason = entry_failure_reason

            entry_verification = self._verify_plan_state(
                state_by_name=rollback_state,
                operations=network_mutation_plan.operations,
                source=True,
            )
            if entry_failure_reason is not None:
                entry_verification["rollback_failure_reason"] = entry_failure_reason

            execution_seed = {
                "mutation_id": network_mutation_plan.mutation_id,
                "operation_count": len(applied_operations),
                "executor_name": self.executor_name,
                "phase": "batched_rollback",
                "rollback_index": rollback_index,
            }
            rollback_result = NetworkMutationRollbackResult(
                execution_id=f"hostmut-{_fingerprint(execution_seed)}",
                boundary={
                    **self._boundary_summary(
                        parallelism=self._parallelism_decision(applied_operations)
                    ),
                    "rollback_batch_mode": "final_teardown_shared_state_refresh",
                },
                rollback_results=entry_results,
                rollback_verification=entry_verification,
                outcome={
                    "rollback_completed": entry_failure_reason is None
                    and bool(entry_verification.get("all_valid")),
                    "failure_reason": entry_failure_reason,
                    "operation_count": len(applied_operations),
                    "operation_timing_summary": _summarize_operation_results(
                        entry_results
                    ),
                    "host_runtime_state": self.public_state_summary(),
                },
            ).to_dict()
            rollback_result["timing"] = {
                "rollback_wall_seconds": time.perf_counter() - entry_started_at,
            }
            host_rollbacks.append(rollback_result)
            all_rollback_results.extend(entry_results)
            if progress_callback is not None:
                progress_callback(
                    {
                        "completed_host_rollbacks": rollback_index,
                        "total_host_rollbacks": total_requests,
                        "rollback_completed": rollback_result["outcome"].get(
                            "rollback_completed"
                        ),
                        "rollback_wall_seconds": rollback_result["timing"].get(
                            "rollback_wall_seconds"
                        ),
                        "rollback_batch_mode": "final_teardown_shared_state_refresh",
                    }
                )
            if entry_failure_reason is not None:
                break

        final_refresh_started_at = time.perf_counter()
        final_state = self._refresh_runtime_state(link_state_path=link_state_path)
        final_refresh_wall_seconds = time.perf_counter() - final_refresh_started_at
        final_verification = self._verify_plan_state(
            state_by_name=final_state,
            operations=list(final_source_operations_by_slot.values()),
            source=True,
        )
        if rollback_failure_reason is not None:
            final_verification["rollback_failure_reason"] = rollback_failure_reason

        return {
            "host_rollbacks": host_rollbacks,
            "rollback_verification": {
                "all_valid": (
                    rollback_failure_reason is None
                    and all(
                        bool(
                            rollback.get("outcome", {}).get("rollback_completed")
                        )
                        for rollback in host_rollbacks
                    )
                    and bool(final_verification.get("all_valid"))
                ),
                "entry_count": len(host_rollbacks),
                "requested_entry_count": total_requests,
                "final_source_verification": final_verification,
                "failure_reason": rollback_failure_reason,
            },
            "outcome": {
                "rollback_completed": (
                    rollback_failure_reason is None
                    and len(host_rollbacks) == total_requests
                    and bool(final_verification.get("all_valid"))
                ),
                "failure_reason": rollback_failure_reason,
                "operation_count": sum(
                    int(rollback.get("outcome", {}).get("operation_count", 0) or 0)
                    for rollback in host_rollbacks
                ),
                "operation_timing_summary": _summarize_operation_results(
                    all_rollback_results
                ),
                "host_runtime_state": self.public_state_summary(),
            },
            "timing": {
                "initial_state_refresh_wall_seconds": round(
                    initial_refresh_wall_seconds,
                    6,
                ),
                "final_state_refresh_wall_seconds": round(
                    final_refresh_wall_seconds,
                    6,
                ),
                "host_rollback_wall_seconds": round(
                    sum(
                        float(
                            rollback.get("timing", {}).get(
                                "rollback_wall_seconds",
                                0.0,
                            )
                            or 0.0
                        )
                        for rollback in host_rollbacks
                    ),
                    6,
                ),
                "rollback_batch_mode": "final_teardown_shared_state_refresh",
            },
        }

    def cleanup_runtime_artifacts(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
    ) -> Dict[str, Any]:
        cleanup_summary = self._installer.cleanup_runtime_artifacts(
            topology_path=topology_path,
            link_state_path=link_state_path,
        )
        self._cached_state_by_slot = {}
        self._cached_link_state_path = None
        self._runtime_initialized = False
        self._last_mutation_id = None
        self._last_target_snapshot_id = None
        self._last_target_slice_id = None
        self._last_runtime_state_refresh_epoch_seconds = round(time.time(), 3)
        return cleanup_summary


class MinimalRealNetworkMutationExecutor:
    """Compatibility wrapper around the in-process Host Runtime Manager."""

    executor_name = InProcessHostRuntimeManager.executor_name
    executor_kind = InProcessHostRuntimeManager.executor_kind
    executor_version = InProcessHostRuntimeManager.executor_version

    def __init__(self, *, max_host_workers: int = 1) -> None:
        self._manager = InProcessHostRuntimeManager(max_host_workers=max_host_workers)

    def apply(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        network_mutation_plan: NetworkMutationPlan,
        reconcile_slot_names: Sequence[str] | None = None,
    ) -> NetworkMutationApplyResult:
        return self._manager.apply(
            topology_path=topology_path,
            link_state_path=link_state_path,
            network_mutation_plan=network_mutation_plan,
            reconcile_slot_names=reconcile_slot_names,
        )

    def rollback(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        network_mutation_plan: NetworkMutationPlan,
        applied_operations: Sequence[Dict[str, Any]],
    ) -> NetworkMutationRollbackResult:
        return self._manager.rollback(
            topology_path=topology_path,
            link_state_path=link_state_path,
            network_mutation_plan=network_mutation_plan,
            applied_operations=applied_operations,
        )

    def rollback_many(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        rollback_requests: Sequence[Mapping[str, Any]],
        progress_callback: Callable[[Dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        return self._manager.rollback_many(
            topology_path=topology_path,
            link_state_path=link_state_path,
            rollback_requests=rollback_requests,
            progress_callback=progress_callback,
        )

    def prewarm(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        reconcile_slot_names: Sequence[str] | None = None,
        prepared_resource_scope_by_slot: Mapping[str, Sequence[str] | set[str]] | None = None,
        progress_callback: Callable[[Dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        return self._manager.prewarm(
            topology_path=topology_path,
            link_state_path=link_state_path,
            reconcile_slot_names=reconcile_slot_names,
            prepared_resource_scope_by_slot=prepared_resource_scope_by_slot,
            progress_callback=progress_callback,
        )

    def cleanup_runtime_artifacts(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
    ) -> Dict[str, Any]:
        return self._manager.cleanup_runtime_artifacts(
            topology_path=topology_path,
            link_state_path=link_state_path,
        )

    def host_runtime_state(self) -> Dict[str, Any]:
        return self._manager.public_state_summary()
