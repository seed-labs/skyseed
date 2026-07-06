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
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence

from contracts import NetworkMutationPlan
from controller import SatelliteController
from network_mutation import dynamic_slot_state_from_link_state
from runtime_operation_metrics import RuntimeOperationMetrics


BRIDGE_NETFILTER_SYSCTLS = (
    "net.bridge.bridge-nf-call-iptables",
    "net.bridge.bridge-nf-call-ip6tables",
    "net.bridge.bridge-nf-call-arptables",
)

DEFAULT_HOST_COMMAND_TIMEOUT_SECONDS = 120.0
DOCKER_INSPECT_TIMEOUT_SECONDS = 30.0
RECONCILE_PROGRESS_MIN_SLOT_DELTA = 8
RECONCILE_PROGRESS_MIN_INTERVAL_SECONDS = 15.0

QDISC_RUNTIME_PARAMETER_FIELDS = {
    "bandwidth_bps",
    "bandwidth_mbps",
    "delay_ms",
    "latency_ms",
    "loss_percent",
    "one_way_delay_ms",
    "packet_loss_percent",
    "rate_bps",
    "rate_mbps",
}


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


def _short_name(prefix: str, seed: str) -> str:
    return f"{prefix}{_fingerprint({'seed': seed})[:11]}"[:15]


def _stable_local_mac(seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    octets = [0x02, digest[0], digest[1], digest[2], digest[3], digest[4]]
    return ":".join(f"{octet:02x}" for octet in octets)


def runtime_parameters_require_qdisc(runtime_parameters: Mapping[str, Any]) -> bool:
    return any(
        key in runtime_parameters and runtime_parameters.get(key) is not None
        for key in QDISC_RUNTIME_PARAMETER_FIELDS
    )


def _normalized_changed_runtime_parameter_fields(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, Mapping):
        return {
            str(key).removesuffix("_changed")
            for key, changed in value.items()
            if changed
        }
    if isinstance(value, (list, tuple, set)):
        return {str(field).removesuffix("_changed") for field in value}
    return {str(value).removesuffix("_changed")}


def runtime_parameter_update_requires_qdisc(operation: Mapping[str, Any]) -> bool:
    target_runtime_parameters = dict(
        operation.get("target_runtime_parameters")
        or operation.get("runtime_parameters")
        or {}
    )
    if str(operation.get("operation") or "") != "update_slot_parameters":
        return runtime_parameters_require_qdisc(target_runtime_parameters)

    changed_fields: set[str] = set()
    for key in (
        "changed_runtime_parameter_fields",
        "parameter_changed_fields",
        "changed_parameter_fields",
    ):
        changed_fields.update(
            _normalized_changed_runtime_parameter_fields(operation.get(key))
        )
    if changed_fields:
        return any(field in QDISC_RUNTIME_PARAMETER_FIELDS for field in changed_fields)

    source_runtime_parameters = operation.get("source_runtime_parameters")
    if isinstance(source_runtime_parameters, Mapping):
        source = dict(source_runtime_parameters)
        return any(
            field in QDISC_RUNTIME_PARAMETER_FIELDS
            and source.get(field) != target_runtime_parameters.get(field)
            for field in set(source) | set(target_runtime_parameters)
        )

    return runtime_parameters_require_qdisc(target_runtime_parameters)


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
        self._qdisc_lifecycle_metrics: Dict[str, int] = {}
        self._qdisc_runtime_args_by_interface: Dict[str, tuple[str, ...]] = {}
        self._operation_metrics = RuntimeOperationMetrics()
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

    def reset_operation_metrics(self) -> None:
        self._operation_metrics.reset()
        self._qdisc_lifecycle_metrics = {}

    def consume_operation_metrics(self, *, reset: bool = True) -> Dict[str, Any]:
        snapshot = self._operation_metrics.snapshot(reset=reset)
        snapshot["qdisc_lifecycle_metrics"] = dict(self._qdisc_lifecycle_metrics)
        if reset:
            self._qdisc_lifecycle_metrics = {}
        return snapshot

    def _record_qdisc_lifecycle(self, key: str, count: int = 1) -> None:
        self._qdisc_lifecycle_metrics[key] = (
            int(self._qdisc_lifecycle_metrics.get(key, 0)) + int(count)
        )

    def _record_deactivate_lifecycle(
        self,
        lifecycle_mode: str,
        *,
        skipped_no_residue: bool = False,
        endpoint_count: int = 0,
        host_interface_count: int = 0,
    ) -> None:
        if skipped_no_residue:
            self._record_qdisc_lifecycle(
                "link_deactivate_skipped_no_residue",
                endpoint_count + host_interface_count,
            )
            self._record_qdisc_lifecycle(
                "addr_flush_skipped_no_residue",
                endpoint_count,
            )
            self._record_qdisc_lifecycle(
                "nomaster_skipped_no_residue",
                host_interface_count,
            )
            return
        self._record_qdisc_lifecycle(
            "link_deactivate_attempted",
            endpoint_count + host_interface_count,
        )
        self._record_qdisc_lifecycle("addr_flush_attempted", endpoint_count)
        self._record_qdisc_lifecycle("nomaster_attempted", host_interface_count)
        if lifecycle_mode == "dirty_recovery_reset":
            self._record_qdisc_lifecycle("dirty_recovery_deactivate_count")
        elif lifecycle_mode == "failure_rollback_cleanup":
            self._record_qdisc_lifecycle("failure_cleanup_deactivate_count")
        elif lifecycle_mode == "final_cleanup":
            self._record_qdisc_lifecycle("final_cleanup_deactivate_count")

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
        qdisc_runtime_parameters = dict(
            operation.get("bpf_owned_host_qdisc_runtime_parameters")
            or operation.get("host_qdisc_runtime_parameters")
            or target_runtime_parameters
        )
        captured_stdout = io.StringIO()
        started_at = time.perf_counter()
        qdisc_apply_wall_seconds = 0.0
        qdisc_required = runtime_parameter_update_requires_qdisc(operation)

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

        if operation_name != "update_slot_parameters" or qdisc_required:
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
            "qdisc_required": bool(qdisc_required),
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
        self._pending_prewarm_reconcile_signature: Dict[str, Any] | None = None
        self._qdisc_lifecycle_metrics: Dict[str, int] = {}
        self._qdisc_runtime_args_by_interface: Dict[str, tuple[str, ...]] = {}
        self._operation_metrics = RuntimeOperationMetrics()

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

    def _slot_inventory_for_names(
        self,
        topology: Dict[str, Any],
        slot_names: Iterable[str],
    ) -> Dict[str, Dict[str, Any]]:
        wanted = {str(slot_name) for slot_name in slot_names if str(slot_name)}
        if not wanted:
            return {}
        entries: Dict[str, Dict[str, Any]] = {}
        for group in dict(topology.get("slot_inventory", {})).values():
            for entry in list(group):
                slot_name = str(dict(entry).get("slot_name"))
                if slot_name in wanted:
                    entries[slot_name] = dict(entry)
                    if len(entries) == len(wanted):
                        return entries
        return entries

    def consume_setup_timing(self) -> Dict[str, float]:
        return dict(self._last_setup_timing)

    def reset_operation_metrics(self) -> None:
        self._operation_metrics.reset()
        self._qdisc_lifecycle_metrics = {}

    def consume_operation_metrics(self, *, reset: bool = True) -> Dict[str, Any]:
        snapshot = self._operation_metrics.snapshot(reset=reset)
        snapshot["qdisc_lifecycle_metrics"] = dict(self._qdisc_lifecycle_metrics)
        if reset:
            self._qdisc_lifecycle_metrics = {}
        return snapshot

    def _record_qdisc_lifecycle(self, key: str, count: int = 1) -> None:
        self._qdisc_lifecycle_metrics[key] = (
            int(self._qdisc_lifecycle_metrics.get(key, 0)) + int(count)
        )

    def _record_deactivate_lifecycle(
        self,
        lifecycle_mode: str,
        *,
        skipped_no_residue: bool = False,
        endpoint_count: int = 0,
        host_interface_count: int = 0,
    ) -> None:
        if skipped_no_residue:
            self._record_qdisc_lifecycle(
                "link_deactivate_skipped_no_residue",
                endpoint_count + host_interface_count,
            )
            self._record_qdisc_lifecycle(
                "addr_flush_skipped_no_residue",
                endpoint_count,
            )
            self._record_qdisc_lifecycle(
                "nomaster_skipped_no_residue",
                host_interface_count,
            )
            return
        self._record_qdisc_lifecycle(
            "link_deactivate_attempted",
            endpoint_count + host_interface_count,
        )
        self._record_qdisc_lifecycle("addr_flush_attempted", endpoint_count)
        self._record_qdisc_lifecycle("nomaster_attempted", host_interface_count)
        if lifecycle_mode == "dirty_recovery_reset":
            self._record_qdisc_lifecycle("dirty_recovery_deactivate_count")
        elif lifecycle_mode == "failure_rollback_cleanup":
            self._record_qdisc_lifecycle("failure_cleanup_deactivate_count")
        elif lifecycle_mode == "final_cleanup":
            self._record_qdisc_lifecycle("final_cleanup_deactivate_count")

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
        prepared_resource_scope_by_slot: Mapping[str, Sequence[str] | set[str]] | None = None,
    ) -> Dict[str, Any]:
        scoped_resource_keys_by_slot: Dict[str, set[str]] = {}
        if prepared_resource_scope_by_slot:
            scoped_resource_keys_by_slot = {
                str(slot_name): {
                    str(resource_key)
                    for resource_key in list(resource_keys or [])
                    if str(resource_key)
                }
                for slot_name, resource_keys in dict(
                    prepared_resource_scope_by_slot or {}
                ).items()
                if str(slot_name)
            }
        inventory = (
            self._slot_inventory_for_names(
                topology,
                scoped_resource_keys_by_slot.keys(),
            )
            if scoped_resource_keys_by_slot
            else self._inventory(topology, topology_path)
        )
        bridge_names: List[str] = []
        host_interface_names: List[str] = []
        temp_host_interface_names: List[str] = []
        container_interface_names: List[Dict[str, str]] = []
        slot_counts_by_type: Dict[str, int] = {}
        candidate_resource_counts_by_type: Dict[str, int] = {}
        candidate_endpoint_counts_by_type: Dict[str, int] = {}
        preconnected_endpoint_counts_by_type: Dict[str, int] = {}
        missing_slot_names = sorted(
            set(scoped_resource_keys_by_slot).difference(inventory)
        )
        slot_iterable = (
            [(slot_name, inventory[slot_name]) for slot_name in sorted(scoped_resource_keys_by_slot) if slot_name in inventory]
            if scoped_resource_keys_by_slot
            else sorted(inventory.items())
        )
        for slot_name, slot in slot_iterable:
            slot_type = self._slot_type(slot)
            slot_counts_by_type[slot_type] = slot_counts_by_type.get(slot_type, 0) + 1
            bridge_names.append(self._bridge_name(slot_name))
            candidate_resources = self._prepared_resources_for_slot(
                slot=slot,
                prepared_resource_scope_by_slot=(
                    {slot_name: scoped_resource_keys_by_slot[slot_name]}
                    if scoped_resource_keys_by_slot
                    else None
                ),
            )
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
            "cleanup_scope_source": (
                "prepared_owned_resource_index"
                if prepared_resource_scope_by_slot
                else "full_topology_candidate_inventory_fallback"
            ),
            "bounded_owned_scope": bool(prepared_resource_scope_by_slot),
            "prepared_slot_scope_count": (
                len(prepared_resource_scope_by_slot)
                if prepared_resource_scope_by_slot
                else 0
            ),
            "slot_counts_by_type": slot_counts_by_type,
            "candidate_resource_counts_by_type": candidate_resource_counts_by_type,
            "candidate_endpoint_counts_by_type": candidate_endpoint_counts_by_type,
            "preconnected_endpoint_counts_by_type": preconnected_endpoint_counts_by_type,
            "missing_slot_names": missing_slot_names,
            "inventory_scope": (
                "prepared_owned_resource_index_slots"
                if scoped_resource_keys_by_slot
                else "full_topology_slot_inventory"
            ),
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
            prepared_paths_match = (
                self._prepared_topology_path == str(topology_path)
                and self._prepared_link_state_path == str(link_state_path)
            )
            if not self._runtime_prepared:
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
            elif not prepared_paths_match:
                if reconcile_slot_names is None:
                    raise RuntimeError(
                        "direct_backend_rolling_topology_switch_requires_explicit_reconcile_scope"
                    )
                if setup_progress_callback is not None:
                    setup_progress_callback(
                        {
                            "stage": "runtime_prepare_skipped_existing_runtime",
                            "reason": "rolling_topology_switch_uses_additive_prepared_inventory",
                            "topology_path": str(topology_path),
                            "link_state_path": str(link_state_path),
                            "reconcile_slot_count": len(
                                {str(slot_name) for slot_name in reconcile_slot_names}
                            ),
                        }
                    )
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
                lifecycle_mode = (
                    "fresh_startup_install"
                    if prepared_now and record_prewarm_reconcile
                    else "normal_runtime_update"
                )
                reconcile_summary = self._reconcile_to_link_state(
                    topology=topology,
                    link_state_path=link_state_path,
                    slot_names=reconcile_slot_names,
                    lifecycle_mode=lifecycle_mode,
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
                "qdisc_lifecycle_metrics": dict(self._qdisc_lifecycle_metrics),
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
        if scoped_keys is None:
            return {}
        if not scoped_keys:
            return {}
        allowed = {str(resource_key) for resource_key in scoped_keys if str(resource_key)}
        if not allowed:
            return {}
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
        started_at = time.perf_counter()
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
        try:
            self._operation_metrics.record_command(
                command,
                wall_seconds=time.perf_counter() - started_at,
                returncode=int(completed.returncode),
            )
        except Exception:
            pass
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

    @staticmethod
    def _idempotent_delete_status(completed: subprocess.CompletedProcess[str]) -> str:
        if int(completed.returncode) == 0:
            return "deleted"
        output = f"{completed.stderr} {completed.stdout}".lower()
        absent_fragments = (
            "cannot find device",
            "does not exist",
            "no such device",
            "no such file or directory",
        )
        if any(fragment in output for fragment in absent_fragments):
            return "absent"
        return "failed"

    def _delete_host_interface_best_effort(self, interface_name: str) -> str:
        completed = self._run_command(
            ["sudo", "-n", "ip", "link", "del", interface_name],
            allow_failure=True,
        )
        return self._idempotent_delete_status(completed)

    def _delete_bridge_best_effort(self, bridge_name: str) -> str:
        self._run_command(
            ["sudo", "-n", "ip", "link", "set", bridge_name, "down"],
            allow_failure=True,
        )
        completed = self._run_command(
            ["sudo", "-n", "ip", "link", "del", bridge_name],
            allow_failure=True,
        )
        return self._idempotent_delete_status(completed)

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

    def _delete_container_interface_best_effort(
        self,
        *,
        node_id: str,
        interface_name: str,
    ) -> str:
        try:
            completed = self._nsenter(
                node_id=node_id,
                command=["ip", "link", "del", interface_name],
                allow_failure=True,
            )
        except (RuntimeError, subprocess.CalledProcessError, ValueError):
            return "absent"
        return self._idempotent_delete_status(completed)

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
        self._qdisc_runtime_args_by_interface = {}
        scoped_prepare = bool(prepared_resource_scope_by_slot)
        scoped_resource_keys_by_slot = {
            str(slot_name): {
                str(resource_key)
                for resource_key in list(resource_keys or [])
                if str(resource_key)
            }
            for slot_name, resource_keys in dict(
                prepared_resource_scope_by_slot or {}
            ).items()
            if str(slot_name)
        }
        artifact_inventory = self._runtime_artifact_inventory(
            topology=topology,
            topology_path=topology_path,
            prepared_resource_scope_by_slot=prepared_resource_scope_by_slot,
        )
        inventory = (
            self._slot_inventory_for_names(
                topology,
                scoped_resource_keys_by_slot.keys(),
            )
            if scoped_prepare
            else self._inventory(topology, topology_path)
        )
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

        missing_slot_names = sorted(
            set(scoped_resource_keys_by_slot).difference(inventory)
        )
        if missing_slot_names:
            raise RuntimeError(
                "bounded_prepare_missing_slot_inventory:"
                + json.dumps(
                    {"missing_slot_names": missing_slot_names},
                    sort_keys=True,
                )
            )
        for processed_slot_count, (slot_name, slot) in enumerate(sorted(inventory.items()), start=1):
            bridge_name = self._bridge_name(slot_name)
            self._remove_docker_network_if_present(
                str(slot.get("docker_network_name")),
                known_networks=known_docker_networks,
            )
            resources_to_prepare = self._prepared_resources_for_slot(
                slot=slot,
                prepared_resource_scope_by_slot=(
                    {slot_name: scoped_resource_keys_by_slot[slot_name]}
                    if scoped_prepare
                    else None
                ),
            )
            for resource in resources_to_prepare.values():
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

            candidate_resources = resources_to_prepare
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
            "scope": "bounded_prepare" if scoped_prepare else "full_prepare",
            "full_sweep": not scoped_prepare,
            "prepared_scope_source": (
                "prepared_owned_resource_index"
                if scoped_prepare
                else "full_topology_candidate_inventory"
            ),
            "inventory_scope": str(artifact_inventory.get("inventory_scope") or ""),
            "missing_slot_names": list(missing_slot_names),
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

    def prepare_additive_resources(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        prepared_resource_scope_by_slot: Mapping[str, Sequence[str] | set[str]],
        progress_callback: Callable[[Dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if not self._runtime_prepared:
                raise RuntimeError("additive_prepare_requires_existing_runtime_prepared")
            topology = self._load_topology(topology_path)
            inventory = self._inventory(topology, topology_path)
            scoped = {
                str(slot_name): {
                    str(resource_key)
                    for resource_key in list(resource_keys or [])
                    if str(resource_key)
                }
                for slot_name, resource_keys in dict(
                    prepared_resource_scope_by_slot or {}
                ).items()
                if str(slot_name)
            }
            if not scoped:
                return {
                    "schema_version": "skyseed-direct-backend-additive-prepare-v1",
                    "ok": True,
                    "additive_prepare_completed": True,
                    "scope": "bounded_additive_prepare",
                    "full_sweep": False,
                    "slot_count": 0,
                    "candidate_resource_count": 0,
                    "candidate_endpoint_count": 0,
                    "bridge_created_count": 0,
                    "bridge_reused_count": 0,
                    "preconnected_endpoint_created_count": 0,
                    "preconnected_endpoint_reused_count": 0,
                    "resource_created_count": 0,
                    "resource_reused_count": 0,
                    "resource_skipped_count": 0,
                    "wall_seconds": 0.0,
                }
            started_at = time.perf_counter()
            self._prepare_bridge_netfilter_passthrough()
            known_host_interfaces = self._host_interface_name_set()
            known_container_interfaces_by_node: Dict[str, set[str]] = {}

            def _known_container_interfaces(node_id: str) -> set[str]:
                cached = known_container_interfaces_by_node.get(node_id)
                if cached is not None:
                    return cached
                cached = self._container_interface_name_set(node_id=node_id)
                known_container_interfaces_by_node[node_id] = cached
                return cached

            def _container_iface_present(endpoint: Mapping[str, Any]) -> bool:
                return str(endpoint.get("interface_name")) in _known_container_interfaces(
                    str(endpoint.get("node_id"))
                )

            def _create_endpoint_checked(
                *,
                slot_name: str,
                bridge_name: str,
                endpoint: Dict[str, Any],
                activate_now: bool,
                assign_ip: bool,
                resource_key: str,
            ) -> None:
                host_interface_name = str(endpoint.get("host_interface_name"))
                if host_interface_name in known_host_interfaces or _container_iface_present(
                    endpoint
                ):
                    raise RuntimeError(
                        "additive_prepare_existing_untracked_interface:"
                        + json.dumps(
                            {
                                "slot_name": slot_name,
                                "resource_key": resource_key,
                                "host_interface_name": host_interface_name,
                                "node_id": str(endpoint.get("node_id")),
                                "interface_name": str(endpoint.get("interface_name")),
                            },
                            sort_keys=True,
                        )
                    )
                self._create_prepared_veth(
                    bridge_name=bridge_name,
                    slot_name=slot_name,
                    endpoint=endpoint,
                    activate_now=activate_now,
                    assign_ip=assign_ip,
                    known_host_interfaces=known_host_interfaces,
                    known_container_interfaces=_known_container_interfaces(
                        str(endpoint.get("node_id"))
                    ),
                )

            bridge_created_count = 0
            bridge_reused_count = 0
            preconnected_endpoint_created_count = 0
            preconnected_endpoint_reused_count = 0
            resource_created_count = 0
            resource_reused_count = 0
            resource_skipped_count = 0
            candidate_endpoint_count = 0
            processed_slot_count = 0
            total_slot_count = len(scoped)
            for slot_name in sorted(scoped):
                slot = inventory.get(slot_name)
                if slot is None:
                    raise RuntimeError(f"additive_prepare_missing_slot_inventory:{slot_name}")
                bridge_name = self._bridge_name(slot_name)
                if self._bridge_exists(bridge_name, known_interfaces=known_host_interfaces):
                    bridge_reused_count += 1
                else:
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
                    bridge_created_count += 1

                preconnected = self._preconnected_endpoint(slot)
                if preconnected is not None:
                    if (
                        str(preconnected.get("host_interface_name"))
                        in known_host_interfaces
                        or _container_iface_present(preconnected)
                    ):
                        preconnected_endpoint_reused_count += 1
                    else:
                        _create_endpoint_checked(
                            slot_name=slot_name,
                            bridge_name=bridge_name,
                            endpoint=preconnected,
                            activate_now=True,
                            assign_ip=True,
                            resource_key="preconnected",
                        )
                        preconnected_endpoint_created_count += 1

                resources = self._prepared_resources_for_slot(
                    slot=slot,
                    prepared_resource_scope_by_slot={slot_name: scoped[slot_name]},
                )
                for resource_key, resource in sorted(resources.items()):
                    if resource_key in self._prepared_resource_keys_by_slot.get(
                        slot_name, set()
                    ):
                        resource_reused_count += 1
                        continue
                    endpoints = list(resource.get("endpoints") or [])
                    for endpoint_any in endpoints:
                        _create_endpoint_checked(
                            slot_name=slot_name,
                            bridge_name=bridge_name,
                            endpoint=dict(endpoint_any),
                            activate_now=False,
                            assign_ip=False,
                            resource_key=str(resource_key),
                        )
                        candidate_endpoint_count += 1
                    self._prepared_resource_keys_by_slot.setdefault(
                        slot_name, set()
                    ).add(str(resource_key))
                    resource_created_count += 1
                resource_skipped_count += max(0, len(scoped[slot_name]) - len(resources))
                processed_slot_count += 1
                if progress_callback is not None:
                    progress_callback(
                        {
                            "stage": "additive_prepare_progress",
                            "processed_slot_count": processed_slot_count,
                            "total_slot_count": total_slot_count,
                            "remaining_slot_count": max(
                                0, total_slot_count - processed_slot_count
                            ),
                            "current_slot_name": slot_name,
                            "bridge_created_count": bridge_created_count,
                            "bridge_reused_count": bridge_reused_count,
                            "resource_created_count": resource_created_count,
                            "resource_reused_count": resource_reused_count,
                            "candidate_endpoint_count": candidate_endpoint_count,
                            "elapsed_wall_seconds": round(
                                time.perf_counter() - started_at,
                                6,
                            ),
                        }
                    )
            self._prepared_resource_counts["bridge_count"] += bridge_created_count
            self._prepared_resource_counts[
                "preconnected_endpoint_count"
            ] += preconnected_endpoint_created_count
            self._prepared_resource_counts[
                "candidate_resource_count"
            ] += resource_created_count
            self._prepared_resource_counts[
                "candidate_endpoint_count"
            ] += candidate_endpoint_count
            wall_seconds = round(time.perf_counter() - started_at, 6)
            summary = {
                "schema_version": "skyseed-direct-backend-additive-prepare-v1",
                "ok": True,
                "additive_prepare_completed": True,
                "scope": "bounded_additive_prepare",
                "full_sweep": False,
                "topology_path": str(topology_path),
                "link_state_path": str(link_state_path),
                "slot_count": total_slot_count,
                "candidate_resource_count": sum(len(v) for v in scoped.values()),
                "candidate_endpoint_count": candidate_endpoint_count,
                "bridge_created_count": bridge_created_count,
                "bridge_reused_count": bridge_reused_count,
                "preconnected_endpoint_created_count": (
                    preconnected_endpoint_created_count
                ),
                "preconnected_endpoint_reused_count": preconnected_endpoint_reused_count,
                "resource_created_count": resource_created_count,
                "resource_reused_count": resource_reused_count,
                "resource_skipped_count": resource_skipped_count,
                "prepared_resource_counts": dict(self._prepared_resource_counts),
                "prepared_resource_scope_by_slot": {
                    slot_name: sorted(resource_keys)
                    for slot_name, resource_keys in sorted(scoped.items())
                },
                "wall_seconds": wall_seconds,
            }
            self._last_setup_summary = {
                "prepared_now": False,
                "prepared_backend": self.execution_transport,
                "additive_prepare_summary": dict(summary),
                "prepared_resource_counts": dict(self._prepared_resource_counts),
            }
            return summary

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
        lifecycle_mode: str = "normal_runtime_update",
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
                    lifecycle_mode=lifecycle_mode,
                    probe=False,
                )
                activated_slot_count += 1
            else:
                self._deactivate_all_resources(
                    slot=slot,
                    lifecycle_mode=lifecycle_mode,
                    skip_qdisc_no_residue=lifecycle_mode == "fresh_startup_install",
                    skip_link_no_residue=lifecycle_mode == "fresh_startup_install",
                )
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
            "qdisc_lifecycle_mode": lifecycle_mode,
            "qdisc_lifecycle_metrics": dict(self._qdisc_lifecycle_metrics),
        }

    def cleanup_runtime_artifacts(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
    ) -> Dict[str, Any]:
        with self._lock:
            topology = self._load_topology(topology_path)
            prepared_cleanup_scope = (
                {
                    str(slot_name): {str(key) for key in keys if str(key)}
                    for slot_name, keys in self._prepared_resource_keys_by_slot.items()
                    if keys
                }
                if self._prepared_resource_keys_by_slot
                else None
            )
            if prepared_cleanup_scope is None:
                cleanup_metrics = {
                    "raw_resource_count": 0,
                    "unique_resource_count": 0,
                    "duplicate_cleanup_skipped_count": 0,
                    "skipped_noop_count": 0,
                    "cleanup_resource_attempted_count": 0,
                    "cleanup_resource_succeeded_count": 0,
                    "cleanup_resource_failed_count": 0,
                    "host_cleanup_grouped_operation_count": 0,
                    "cleanup_scope_source": "missing_prepared_owned_resource_index",
                    "bounded_owned_scope": False,
                    "prepared_slot_scope_count": 0,
                    "full_topology_candidate_inventory_fallback": False,
                    "cleanup_execution_model": "blocked_missing_owned_resource_index",
                    "cleanup_existence_probe_count": 0,
                    "diagnostic_scan_wall_seconds": 0.0,
                    "global_scan_diagnostic_only": False,
                    "outcome_labels": [
                        "NORMAL_PATH_NO_HIDDEN_REPAIR",
                        "OWNED_RESOURCE_INDEX_MISSING_BLOCKER",
                        "CLEANUP_NO_GLOBAL_EXISTENCE_SCAN",
                    ],
                    "resource_groups": {
                        "temp_host_interface": 0,
                        "host_interface": 0,
                        "container_interface": 0,
                        "bridge": 0,
                    },
                }
                cleanup_summary = {
                    "scope": "direct_backend_runtime_artifacts",
                    "removed_bridge_count": 0,
                    "removed_host_interface_count": 0,
                    "removed_temp_host_interface_count": 0,
                    "removed_container_interface_count": 0,
                    "remaining_bridge_count": 0,
                    "remaining_host_interface_count": 0,
                    "remaining_temp_host_interface_count": 0,
                    "remaining_bridges": [],
                    "remaining_host_interfaces": [],
                    "remaining_temp_host_interfaces": [],
                    "cleanup_metrics": cleanup_metrics,
                    "cleanup_completed": False,
                    "reason": "OWNED_RESOURCE_INDEX_MISSING_BLOCKER",
                }
                self._last_cleanup_summary = cleanup_summary
                return cleanup_summary
            artifact_inventory = self._runtime_artifact_inventory(
                topology=topology,
                topology_path=topology_path,
                prepared_resource_scope_by_slot=prepared_cleanup_scope,
            )
            def _unique_strings(values: Sequence[str]) -> tuple[List[str], int]:
                seen: set[str] = set()
                unique: List[str] = []
                duplicate_count = 0
                for value in values:
                    rendered = str(value)
                    if rendered in seen:
                        duplicate_count += 1
                        continue
                    seen.add(rendered)
                    unique.append(rendered)
                return unique, duplicate_count

            def _unique_endpoints(
                values: Sequence[Mapping[str, str]],
            ) -> tuple[List[Dict[str, str]], int]:
                seen: set[tuple[str, str]] = set()
                unique: List[Dict[str, str]] = []
                duplicate_count = 0
                for endpoint in values:
                    key = (
                        str(endpoint.get("node_id", "")),
                        str(endpoint.get("interface_name", "")),
                    )
                    if key in seen:
                        duplicate_count += 1
                        continue
                    seen.add(key)
                    unique.append({"node_id": key[0], "interface_name": key[1]})
                return unique, duplicate_count

            temp_host_interface_names, duplicate_temp_host_interfaces = _unique_strings(
                artifact_inventory["temp_host_interface_names"]
            )
            host_interface_names, duplicate_host_interfaces = _unique_strings(
                artifact_inventory["host_interface_names"]
            )
            bridge_names, duplicate_bridges = _unique_strings(
                artifact_inventory["bridge_names"]
            )
            container_interface_names, duplicate_container_interfaces = _unique_endpoints(
                artifact_inventory["container_interface_names"]
            )
            cleanup_metrics: Dict[str, Any] = {
                "raw_resource_count": (
                    len(artifact_inventory["temp_host_interface_names"])
                    + len(artifact_inventory["host_interface_names"])
                    + len(artifact_inventory["container_interface_names"])
                    + len(artifact_inventory["bridge_names"])
                ),
                "unique_resource_count": (
                    len(temp_host_interface_names)
                    + len(host_interface_names)
                    + len(container_interface_names)
                    + len(bridge_names)
                ),
                "duplicate_cleanup_skipped_count": (
                    duplicate_temp_host_interfaces
                    + duplicate_host_interfaces
                    + duplicate_container_interfaces
                    + duplicate_bridges
                ),
                "skipped_noop_count": 0,
                "cleanup_resource_attempted_count": 0,
                "cleanup_resource_succeeded_count": 0,
                "cleanup_resource_failed_count": 0,
                "host_cleanup_grouped_operation_count": 4,
                "cleanup_execution_model": (
                    "owned_index_delete_without_global_existence_scan"
                ),
                "cleanup_existence_probe_count": 0,
                "diagnostic_scan_wall_seconds": 0.0,
                "cleanup_scope_source": artifact_inventory.get("cleanup_scope_source"),
                "bounded_owned_scope": bool(artifact_inventory.get("bounded_owned_scope")),
                "prepared_slot_scope_count": int(
                    artifact_inventory.get("prepared_slot_scope_count") or 0
                ),
                "full_topology_candidate_inventory_fallback": not bool(
                    artifact_inventory.get("bounded_owned_scope")
                ),
                "resource_groups": {
                    "temp_host_interface": len(temp_host_interface_names),
                    "host_interface": len(host_interface_names),
                    "container_interface": len(container_interface_names),
                    "bridge": len(bridge_names),
                },
                "missing_slot_count": len(
                    list(artifact_inventory.get("missing_slot_names") or [])
                ),
                "missing_slot_names": list(
                    artifact_inventory.get("missing_slot_names") or []
                ),
                "inventory_scope": str(
                    artifact_inventory.get("inventory_scope") or ""
                ),
                "global_scan_diagnostic_only": False,
                "outcome_labels": [
                    "NORMAL_PATH_OWNED_SCOPE_ONLY",
                    "NORMAL_PATH_NO_HIDDEN_REPAIR",
                    "CLEANUP_BOUNDED_OWNED_SCOPE_USED",
                    "CLEANUP_NO_GLOBAL_EXISTENCE_SCAN",
                ],
            }

            removed_host_interfaces: List[str] = []
            removed_temp_host_interfaces: List[str] = []
            removed_container_interfaces: List[Dict[str, str]] = []
            removed_bridges: List[str] = []
            for interface_name in temp_host_interface_names:
                cleanup_metrics["cleanup_resource_attempted_count"] += 1
                status = self._delete_host_interface_best_effort(interface_name)
                if status == "deleted":
                    removed_temp_host_interfaces.append(interface_name)
                    cleanup_metrics["cleanup_resource_succeeded_count"] += 1
                elif status == "absent":
                    cleanup_metrics["skipped_noop_count"] += 1
                else:
                    cleanup_metrics["cleanup_resource_failed_count"] += 1
            for interface_name in host_interface_names:
                cleanup_metrics["cleanup_resource_attempted_count"] += 1
                status = self._delete_host_interface_best_effort(interface_name)
                if status == "deleted":
                    removed_host_interfaces.append(interface_name)
                    cleanup_metrics["cleanup_resource_succeeded_count"] += 1
                elif status == "absent":
                    cleanup_metrics["skipped_noop_count"] += 1
                else:
                    cleanup_metrics["cleanup_resource_failed_count"] += 1
            # Host-side veth deletion removes the paired container endpoint when it is still
            # attached. Container-side cleanup remains bounded to owned endpoints and does not
            # pre-scan the namespace before issuing an idempotent delete.
            for endpoint in container_interface_names:
                node_id = str(endpoint["node_id"])
                interface_name = str(endpoint["interface_name"])
                cleanup_metrics["cleanup_resource_attempted_count"] += 1
                status = self._delete_container_interface_best_effort(
                    node_id=node_id,
                    interface_name=interface_name,
                )
                if status == "deleted":
                    removed_container_interfaces.append(
                        {"node_id": node_id, "interface_name": interface_name}
                    )
                    cleanup_metrics["cleanup_resource_succeeded_count"] += 1
                elif status == "absent":
                    cleanup_metrics["skipped_noop_count"] += 1
                else:
                    cleanup_metrics["cleanup_resource_failed_count"] += 1
            for bridge_name in bridge_names:
                cleanup_metrics["cleanup_resource_attempted_count"] += 1
                status = self._delete_bridge_best_effort(bridge_name)
                if status == "deleted":
                    removed_bridges.append(bridge_name)
                    cleanup_metrics["cleanup_resource_succeeded_count"] += 1
                elif status == "absent":
                    cleanup_metrics["skipped_noop_count"] += 1
                else:
                    cleanup_metrics["cleanup_resource_failed_count"] += 1
            remaining_host_interfaces: List[str] = []
            remaining_temp_host_interfaces: List[str] = []
            remaining_bridges: List[str] = []
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
            self._qdisc_runtime_args_by_interface = {}
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
                "cleanup_metrics": cleanup_metrics,
                "cleanup_completed": (
                    int(cleanup_metrics["cleanup_resource_failed_count"]) == 0
                    and int(cleanup_metrics["missing_slot_count"]) == 0
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

    def _clear_qdisc(
        self,
        interface_name: str,
        *,
        lifecycle_mode: str = "normal_runtime_update",
        skip_no_residue: bool = False,
    ) -> None:
        if skip_no_residue:
            self._record_qdisc_lifecycle("qdisc_delete_skipped_no_residue")
            self._qdisc_runtime_args_by_interface.pop(interface_name, None)
            return
        self._record_qdisc_lifecycle("qdisc_delete_attempted")
        result = self._run_command(
            ["sudo", "-n", "tc", "qdisc", "del", "dev", interface_name, "root"],
            allow_failure=True,
        )
        if result.returncode == 0:
            self._record_qdisc_lifecycle("qdisc_delete_required_existing_mismatch")
        else:
            self._record_qdisc_lifecycle("qdisc_delete_failure_count")
        self._qdisc_runtime_args_by_interface.pop(interface_name, None)
        self._record_qdisc_lifecycle(f"qdisc_delete_mode:{lifecycle_mode}")

    def _apply_qdisc(
        self,
        *,
        interface_name: str,
        runtime_parameters: Dict[str, Any],
        lifecycle_mode: str = "normal_runtime_update",
    ) -> bool:
        netem_args = self._runtime_parameter_netem_args(runtime_parameters)
        if not netem_args:
            self._clear_qdisc(interface_name, lifecycle_mode=lifecycle_mode)
            return True
        normalized_args = tuple(str(arg) for arg in netem_args)
        if self._qdisc_runtime_args_by_interface.get(interface_name) == normalized_args:
            self._record_qdisc_lifecycle("qdisc_replace_skipped_unchanged")
            self._record_qdisc_lifecycle(f"qdisc_replace_skip_mode:{lifecycle_mode}")
            return False
        self._record_qdisc_lifecycle("qdisc_replace_count")
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
        self._qdisc_runtime_args_by_interface[interface_name] = normalized_args
        return True

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
        raise RuntimeError(
            "runtime_resource_materialization_forbidden_missing_prepared_resource:"
            + json.dumps(
                {
                    "slot_name": slot_name,
                    "resource_key": resource_key,
                },
                sort_keys=True,
            )
        )

    def _activate_resource(
        self,
        *,
        slot: Dict[str, Any],
        resource: Dict[str, Any],
        runtime_parameters: Dict[str, Any],
        lifecycle_mode: str = "normal_runtime_update",
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
            self._apply_qdisc(
                interface_name=interface_name,
                runtime_parameters=runtime_parameters,
                lifecycle_mode=lifecycle_mode,
            )
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
        lifecycle_mode: str = "normal_runtime_update",
        skip_qdisc_no_residue: bool = False,
        skip_link_no_residue: bool = False,
    ) -> None:
        host_interfaces = self._participant_host_interfaces(slot=slot, resource=resource)
        endpoints = list(resource.get("endpoints", []))
        for interface_name in host_interfaces:
            self._clear_qdisc(
                interface_name,
                lifecycle_mode=lifecycle_mode,
                skip_no_residue=skip_qdisc_no_residue,
            )
        if skip_link_no_residue:
            self._record_deactivate_lifecycle(
                lifecycle_mode,
                skipped_no_residue=True,
                endpoint_count=len(endpoints),
                host_interface_count=len(host_interfaces),
            )
            return
        self._record_deactivate_lifecycle(
            lifecycle_mode,
            endpoint_count=len(endpoints),
            host_interface_count=len(host_interfaces),
        )
        for endpoint in endpoints:
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

    def _deactivate_all_resources(
        self,
        *,
        slot: Dict[str, Any],
        lifecycle_mode: str = "normal_runtime_update",
        skip_qdisc_no_residue: bool = False,
        skip_link_no_residue: bool = False,
    ) -> None:
        for resource in self._candidate_resources(slot).values():
            self._deactivate_resource(
                slot=slot,
                resource=resource,
                lifecycle_mode=lifecycle_mode,
                skip_qdisc_no_residue=skip_qdisc_no_residue,
                skip_link_no_residue=skip_link_no_residue,
            )

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
        qdisc_runtime_parameters = dict(
            operation.get("bpf_owned_host_qdisc_runtime_parameters")
            or operation.get("host_qdisc_runtime_parameters")
            or target_runtime_parameters
        )

        started_at = time.perf_counter()
        qdisc_apply_wall_seconds = 0.0
        metadata_update_wall_seconds = 0.0
        qdisc_apply_attempt_count = 0
        qdisc_apply_skipped_unchanged_count = 0
        qdisc_required = runtime_parameter_update_requires_qdisc(operation)
        if operation_name == "update_slot_parameters":
            if target_binding and qdisc_required:
                target_resource = self._resource_for_binding(slot=slot, binding=target_binding)
                qdisc_started_at = time.perf_counter()
                for interface_name in self._participant_host_interfaces(
                    slot=slot,
                    resource=target_resource,
                ):
                    qdisc_changed = self._apply_qdisc(
                        interface_name=interface_name,
                        runtime_parameters=qdisc_runtime_parameters,
                        lifecycle_mode="parameter_only_update",
                    )
                    if qdisc_changed:
                        qdisc_apply_attempt_count += 1
                    else:
                        qdisc_apply_skipped_unchanged_count += 1
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
            "qdisc_required": bool(qdisc_required),
            "qdisc_apply_wall_seconds": round(qdisc_apply_wall_seconds, 6),
            "qdisc_apply_attempt_count": qdisc_apply_attempt_count,
            "qdisc_apply_skipped_unchanged_count": qdisc_apply_skipped_unchanged_count,
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

    def _reset_installer_operation_metrics(self) -> None:
        reset = getattr(self._installer, "reset_operation_metrics", None)
        if callable(reset):
            reset()

    def _consume_installer_operation_metrics(self) -> Dict[str, Any] | None:
        consume = getattr(self._installer, "consume_operation_metrics", None)
        if not callable(consume):
            return None
        return dict(consume(reset=True))

    def _compact_final_rollback_operations(
        self,
        *,
        requests: Sequence[Mapping[str, Any]],
        current_state: Mapping[str, Mapping[str, Any]],
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Dict[str, Any]]]:
        final_source_operations_by_slot: Dict[str, Dict[str, Any]] = {}
        requested_operation_count = 0
        for request in requests:
            for operation in list(request.get("applied_operations") or []):
                requested_operation_count += 1
                slot_name = str(operation.get("slot_name"))
                final_source_operations_by_slot.setdefault(slot_name, dict(operation))

        compact_operations: List[Dict[str, Any]] = []
        skipped_noop_count = 0
        for slot_name, source_operation in sorted(final_source_operations_by_slot.items()):
            desired_state = _state_entry_from_operation(source_operation, source=True)
            current_entry = dict(current_state.get(slot_name, {}) or {})
            current_summary = _slot_summary(current_entry)
            desired_summary = _slot_summary(desired_state)
            if current_summary == desired_summary:
                skipped_noop_count += 1
                continue
            source_occupied = bool(current_entry.get("occupied", False))
            target_occupied = bool(desired_state.get("occupied", False))
            if source_occupied and target_occupied:
                if current_summary["binding"] == desired_summary["binding"]:
                    operation_name = "update_slot_parameters"
                else:
                    operation_name = "rebind_slot_attachment"
            elif source_occupied:
                operation_name = "deactivate_slot_attachment"
            else:
                operation_name = "activate_slot_attachment"
            compact_operations.append(
                {
                    **dict(source_operation),
                    "operation": operation_name,
                    "source_occupied": source_occupied,
                    "target_occupied": target_occupied,
                    "source_binding": dict(current_entry.get("binding", {}) or {}),
                    "target_binding": dict(desired_state.get("binding", {}) or {}),
                    "source_endpoints": [
                        dict(endpoint) for endpoint in current_entry.get("endpoints", []) or []
                    ],
                    "target_endpoints": [
                        dict(endpoint) for endpoint in desired_state.get("endpoints", []) or []
                    ],
                    "source_runtime_parameters": dict(
                        current_entry.get("runtime_parameters", {}) or {}
                    ),
                    "target_runtime_parameters": dict(
                        desired_state.get("runtime_parameters", {}) or {}
                    ),
                    "rollback_compaction": {
                        "source": "final_teardown_current_state_to_original_source",
                        "original_requested_operation_count": requested_operation_count,
                    },
                }
            )
        cleanup_metrics = {
            "requested_entry_count": len(requests),
            "requested_operation_count": requested_operation_count,
            "unique_slot_count": len(final_source_operations_by_slot),
            "compact_operation_count": len(compact_operations),
            "skipped_noop_count": skipped_noop_count,
            "duplicate_cleanup_skipped_count": max(
                0,
                requested_operation_count
                - len(compact_operations)
                - skipped_noop_count,
            ),
            "rollback_attempted_count": len(compact_operations),
        }
        return compact_operations, cleanup_metrics, final_source_operations_by_slot

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
        self._reset_installer_operation_metrics()
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
        typed_operation_metrics = self._consume_installer_operation_metrics()
        preparation_summary = self._installer.last_setup_summary()
        prepared_counts = dict(preparation_summary.get("prepared_resource_counts") or {})
        expected_cardinality = sum(
            int(prepared_counts.get(key) or 0)
            for key in (
                "bridge_count",
                "preconnected_endpoint_count",
                "candidate_resource_count",
                "candidate_endpoint_count",
            )
        )
        prewarm_wall_seconds = round(time.perf_counter() - started_at, 6)
        scope_items = sorted(
            (
                str(slot_name),
                tuple(sorted(str(resource_key) for resource_key in resource_keys)),
            )
            for slot_name, resource_keys in dict(
                prepared_resource_scope_by_slot or {}
            ).items()
        )
        startup_operation_trace = [
            {
                "operation_id": "direct-backend-prewarm-static-inventory",
                "operation_type": (
                    "prewarm_materialize_bounded_owned_scope"
                    if prepared_resource_scope_by_slot is not None
                    else "prewarm_materialize_full_scope"
                ),
                "owner_module": "satellite/network_runtime.py",
                "stage": "static_inventory_prewarm",
                "batch": "direct_backend_materialization",
                "target_resource": str(topology_path),
                "target_resource_type": "topology_slot_inventory",
                "intent": (
                    "materialize prepared owned resource scope into inactive runtime resources"
                    if prepared_resource_scope_by_slot is not None
                    else "materialize full static slot inventory into inactive runtime resources"
                ),
                "reason": "profile requested static inventory materialization before runtime horizon",
                "idempotency_key": json.dumps(
                    {
                        "topology_path": str(topology_path),
                        "link_state_path": str(link_state_path),
                        "reconcile_slot_names": list(reconcile_slot_names or []),
                        "prepared_resource_scope_by_slot": scope_items,
                    },
                    sort_keys=True,
                ),
                "expected_cardinality": expected_cardinality,
                "actual_attempt_count": int(
                    dict(typed_operation_metrics or {}).get("command_count") or 1
                ),
                "duplicate_count": int(
                    dict(typed_operation_metrics or {}).get(
                        "duplicate_operation_skipped_count"
                    )
                    or 0
                ),
                "retry_count": 0,
                "duration_seconds": prewarm_wall_seconds,
                "state_changed": int(
                    dict(typed_operation_metrics or {}).get("command_count") or 0
                )
                > 0,
                "result": "completed",
                "error": "",
                "full_scope": prepared_resource_scope_by_slot is None,
            }
        ]
        return {
            "prewarm_completed": True,
            "prewarm_wall_seconds": prewarm_wall_seconds,
            "manager_timing_breakdown": {
                key: round(float(value), 6) for key, value in sorted(timing.items())
            },
            "typed_operation_metrics": typed_operation_metrics,
            "startup_operation_trace": startup_operation_trace,
            "preparation_summary": preparation_summary,
            "host_runtime_state": self.public_state_summary(),
            "managed_slot_count": len(state),
            "topology_path": str(topology_path),
            "link_state_path": str(link_state_path),
            "reconcile_slot_names": list(reconcile_slot_names or []),
            "controller_topology_path": controller.topology_path,
        }

    def prepare_additive_resources(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        prepared_resource_scope_by_slot: Mapping[str, Sequence[str] | set[str]],
        progress_callback: Callable[[Dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        started_at = time.perf_counter()
        self._reset_installer_operation_metrics()
        summary = self._installer.prepare_additive_resources(
            topology_path=topology_path,
            link_state_path=link_state_path,
            prepared_resource_scope_by_slot=prepared_resource_scope_by_slot,
            progress_callback=progress_callback,
        )
        typed_operation_metrics = self._consume_installer_operation_metrics()
        return {
            **dict(summary),
            "additive_prepare_completed": True,
            "additive_prepare_wall_seconds": round(
                time.perf_counter() - started_at,
                6,
            ),
            "typed_operation_metrics": typed_operation_metrics,
            "host_runtime_state": self.public_state_summary(),
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

    def _trusted_plan_state_verification(
        self,
        *,
        operations: Sequence[Dict[str, Any]],
        source: bool,
        reason: str,
    ) -> Dict[str, Any]:
        return {
            "checked_slot_count": len(operations),
            "mismatches": [],
            "all_valid": True,
            "verification_skipped": True,
            "skip_reason": reason,
            "trusted_state_source": (
                "compiled_network_mutation_plan_source_state"
                if source
                else "applied_operation_results_and_cached_runtime_state"
            ),
            "hot_path_rescan_avoided": True,
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
        apply_verification = self._trusted_plan_state_verification(
            operations=operations,
            source=False,
            reason=(
                "trusted_hot_path_uses_apply_results_without_post_apply_rescan"
            ),
        )
        post_apply_verification_wall_seconds = 0.0
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
        self._reset_installer_operation_metrics()
        if reconcile_mode == "auto":
            effective_reconcile_mode = (
                "apply" if self._last_mutation_id is None else "trusted_skip"
            )
        else:
            effective_reconcile_mode = str(reconcile_mode)
        if reconcile_slot_names is None:
            raise NetworkMutationExecutionError(
                "OWNED_RESOURCE_INDEX_MISSING_BLOCKER:"
                "normal runtime apply requires explicit reconcile_slot_names"
            )
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
        preflight_verification = self._trusted_plan_state_verification(
            operations=envelope.operations,
            source=True,
            reason=(
                "trusted_hot_path_uses_prepared_mutation_plan_without_source_rescan"
            ),
        )

        apply_results: List[Dict[str, Any]] = []
        applied_operations: List[Dict[str, Any]] = []
        failure_reason: str | None = None
        apply_verification: Dict[str, Any] = {
            "all_valid": False,
            "reason": "apply_not_completed",
        }
        bootstrap_summary: Dict[str, Any] | None = None
        parallel_execution_summary: Dict[str, Any] | None = None
        hidden_repair_blocked = False

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
                    hidden_repair_blocked = True
                    failure_reason = (
                        "FULL_SCOPE_RECOVERY_BLOCKER:"
                        "trusted_reconcile_repair_disabled:"
                        + failure_reason
                    )
                bootstrap_started_at = time.perf_counter()
                if failure_reason is not None and not hidden_repair_blocked:
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
                elif hidden_repair_blocked:
                    state_refresh_started_at = time.perf_counter()
                    apply_state = self._refresh_runtime_state(
                        link_state_path=link_state_path
                    )
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
                "typed_operation_metrics": self._consume_installer_operation_metrics(),
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
                "normal_path_scope_contract": {
                    "outcome_labels": [
                        "NORMAL_PATH_OWNED_SCOPE_ONLY",
                        "NORMAL_PATH_NO_HIDDEN_REPAIR",
                        *(
                            [
                                "FULL_SCOPE_RECOVERY_BLOCKER",
                                "NOOP_FULL_SCOPE_RECONCILE_BLOCKER",
                            ]
                            if hidden_repair_blocked
                            else []
                        ),
                        "GLOBAL_SCAN_DIAGNOSTIC_ONLY",
                    ],
                    "explicit_reconcile_slot_count": len(
                        {str(slot_name) for slot_name in reconcile_slot_names}
                    ),
                    "global_scan_diagnostic_only": True,
                    "hidden_repair_blocked": hidden_repair_blocked,
                    "full_scope_recovery_used": False,
                },
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
        self._reset_installer_operation_metrics()
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
                "typed_operation_metrics": self._consume_installer_operation_metrics(),
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
        self._reset_installer_operation_metrics()
        rollback_controller = self._installer.controller(
            topology_path=topology_path,
            link_state_path=link_state_path,
        )
        refresh_started_at = time.perf_counter()
        rollback_state = self._refresh_runtime_state(link_state_path=link_state_path)
        initial_refresh_wall_seconds = time.perf_counter() - refresh_started_at
        (
            compact_operations,
            cleanup_metrics,
            final_source_operations_by_slot,
        ) = self._compact_final_rollback_operations(
            requests=requests,
            current_state=rollback_state,
        )

        host_rollbacks: List[Dict[str, Any]] = []
        all_rollback_results: List[Dict[str, Any]] = []
        rollback_failure_reason: str | None = None
        total_requests = len(requests)
        entry_started_at = time.perf_counter()
        entry_results: List[Dict[str, Any]] = []
        entry_failure_reason: str | None = None
        apply_timing: Dict[str, float] = {
            "operation_apply_wall_seconds": 0.0,
            "batched_state_persist_wall_seconds": 0.0,
            "post_apply_state_refresh_wall_seconds": 0.0,
            "post_apply_verification_wall_seconds": 0.0,
            "automatic_rollback_wall_seconds": 0.0,
        }
        parallel_execution_summary: Dict[str, Any] = {}
        if compact_operations:
            try:
                (
                    entry_results,
                    _applied_operations,
                    _apply_verification,
                    apply_timing,
                    parallel_execution_summary,
                ) = self._apply_operations(
                    controller=rollback_controller,
                    link_state_path=link_state_path,
                    operations=compact_operations,
                    working_state=rollback_state,
                    parallelism=self._parallelism_decision(compact_operations),
                )
            except Exception as exc:  # noqa: BLE001
                entry_failure_reason = str(exc)
                rollback_failure_reason = entry_failure_reason

        entry_verification = self._verify_plan_state(
            state_by_name=rollback_state,
            operations=list(final_source_operations_by_slot.values()),
            source=True,
        )
        if entry_failure_reason is not None:
            entry_verification["rollback_failure_reason"] = entry_failure_reason
        if requests:
            execution_seed = {
                "mutation_id": "final_teardown_compact",
                "operation_count": len(compact_operations),
                "executor_name": self.executor_name,
                "phase": "compacted_batched_rollback",
            }
            rollback_result = NetworkMutationRollbackResult(
                execution_id=f"hostmut-{_fingerprint(execution_seed)}",
                boundary={
                    **self._boundary_summary(
                        parallelism=self._parallelism_decision(compact_operations)
                    ),
                    "rollback_batch_mode": "final_teardown_compacted_current_to_source",
                },
                rollback_results=entry_results,
                rollback_verification=entry_verification,
                outcome={
                    "rollback_completed": entry_failure_reason is None
                    and bool(entry_verification.get("all_valid")),
                    "failure_reason": entry_failure_reason,
                    "operation_count": len(compact_operations),
                    "requested_operation_count": cleanup_metrics[
                        "requested_operation_count"
                    ],
                    "operation_timing_summary": _summarize_operation_results(
                        entry_results
                    ),
                    "cleanup_metrics": dict(cleanup_metrics),
                    "parallel_execution_summary": dict(parallel_execution_summary),
                    "host_runtime_state": self.public_state_summary(),
                },
            ).to_dict()
            rollback_result["timing"] = {
                "rollback_wall_seconds": time.perf_counter() - entry_started_at,
                **{key: round(value, 6) for key, value in apply_timing.items()},
            }
            host_rollbacks.append(rollback_result)
            all_rollback_results.extend(entry_results)
            if progress_callback is not None:
                progress_callback(
                    {
                        "completed_host_rollbacks": total_requests,
                        "total_host_rollbacks": total_requests,
                        "compacted_host_rollback_records": 1,
                        "rollback_completed": rollback_result["outcome"].get(
                            "rollback_completed"
                        ),
                        "rollback_wall_seconds": rollback_result["timing"].get(
                            "rollback_wall_seconds"
                        ),
                        "rollback_batch_mode": "final_teardown_compacted_current_to_source",
                        "cleanup_metrics": dict(cleanup_metrics),
                    }
                )

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
        typed_operation_metrics = self._consume_installer_operation_metrics()
        rollback_completed = (
            rollback_failure_reason is None
            and (not requests or len(host_rollbacks) == 1)
            and bool(final_verification.get("all_valid"))
        )
        finalized_cleanup_metrics = {
            **dict(cleanup_metrics),
            "rollback_succeeded_count": (
                len(compact_operations) if rollback_completed else 0
            ),
            "rollback_failed_count": (
                0 if rollback_completed else len(compact_operations)
            ),
        }
        if host_rollbacks:
            host_rollbacks[-1].setdefault("outcome", {}).update(
                {
                    "cleanup_metrics": dict(finalized_cleanup_metrics),
                    "typed_operation_metrics": typed_operation_metrics,
                }
            )

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
                "cleanup_metrics": dict(finalized_cleanup_metrics),
            },
            "outcome": {
                "rollback_completed": rollback_completed,
                "failure_reason": rollback_failure_reason,
                "operation_count": sum(
                    int(rollback.get("outcome", {}).get("operation_count", 0) or 0)
                    for rollback in host_rollbacks
                ),
                "requested_operation_count": cleanup_metrics[
                    "requested_operation_count"
                ],
                "operation_timing_summary": _summarize_operation_results(
                    all_rollback_results
                ),
                "typed_operation_metrics": typed_operation_metrics,
                "cleanup_metrics": dict(finalized_cleanup_metrics),
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
                "rollback_batch_mode": "final_teardown_compacted_current_to_source",
            },
        }

    def cleanup_runtime_artifacts(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
    ) -> Dict[str, Any]:
        self._reset_installer_operation_metrics()
        cleanup_summary = self._installer.cleanup_runtime_artifacts(
            topology_path=topology_path,
            link_state_path=link_state_path,
        )
        operation_metrics = self._consume_installer_operation_metrics()
        if operation_metrics is not None:
            cleanup_summary["typed_operation_metrics"] = operation_metrics
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
        if reconcile_slot_names is None:
            reconcile_slot_names = sorted(
                {
                    str(operation.get("slot_name"))
                    for operation in network_mutation_plan.operations
                    if operation.get("slot_name")
                }
            )
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

    def prepare_additive_resources(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
        prepared_resource_scope_by_slot: Mapping[str, Sequence[str] | set[str]],
        progress_callback: Callable[[Dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        return self._manager.prepare_additive_resources(
            topology_path=topology_path,
            link_state_path=link_state_path,
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
