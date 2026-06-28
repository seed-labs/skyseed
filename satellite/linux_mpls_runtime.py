#!/usr/bin/env python3
# encoding: utf-8

"""Minimal real Linux/MPLS runtime realization for the Iteration 2 experiment."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from contracts import RuntimeUpdatePlan, TopologySnapshot
from distribution_store import DistributedEnvelopeBatch
from mpls_host import ensure_host_mpls_ready
from runtime_operation_metrics import (
    classify_node_local_command_scope,
    summarize_node_local_batches,
)
from temporal_planning import gz_fec_plan

DOCKER_EXEC_TIMEOUT_SECONDS = 120.0
DOCKER_INSPECT_TIMEOUT_SECONDS = 30.0
NODE_LOCAL_BATCH_TIMEOUT_SECONDS = 900.0
OPERATIONAL_CLEAN_ROLLBACK_BATCH_TIMEOUT_SECONDS = 30.0
DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE = "reapplied_gateway_class_sample"
LIGHTWEIGHT_RUNTIME_MODES = {"fast-demo", "operational_clean"}
IPTABLES_WAIT_SECONDS = 10
BPF_MPLS_LABEL_BASE = 524288
BPF_MPLS_LABEL_MAX = 1048575
FORWARDING_BACKEND_MODES = ("linux_mpls", "bpf_shadow", "bpf_canary", "bpf_owned")
BPF_OWNED_ELIGIBLE_SCOPES = ("satellite_core_mpls_lfib", "mpls_readiness_sysctl")
BPF_OWNED_PROOF_SCHEMA_VERSION = "skyseed-bpf-owned-readiness-proof-v1"
BPF_MPLS_INTERFACE_ROLE_CLASSIFIER = 1
BPF_MPLS_INTERFACE_ROLE_MPLS = 2
BPF_OWNED_DEFAULT_MAX_ATTACH_INTERFACES = 256


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


def _sha256(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _timed_subprocess_run(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    started_at = time.perf_counter()
    try:
        completed = subprocess.run(
            list(command),
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
        completed.skyseed_wall_seconds = time.perf_counter() - started_at  # type: ignore[attr-defined]
        return completed
    except subprocess.TimeoutExpired as exc:
        completed = subprocess.CompletedProcess(
            args=list(command),
            returncode=124,
            stdout=exc.stdout or "",
            stderr=((exc.stderr or "") + f"\ncommand timed out after {timeout_seconds:.1f}s").strip(),
        )
        completed.skyseed_wall_seconds = time.perf_counter() - started_at  # type: ignore[attr-defined]
        return completed


def _topology_entry_by_id(
    entries: Sequence[Dict[str, Any]],
    key: str,
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        entry_id = str(entry.get(key))
        result[entry_id] = dict(entry)
    return result


def _strip_prefix(ip_address: str) -> str:
    return str(ip_address).split("/", 1)[0]


def _active_link_by_id(
    topology_snapshot: TopologySnapshot,
    link_id: str,
) -> Dict[str, Any]:
    return next(
        link
        for link in topology_snapshot.active_links
        if str(link.get("link_id")) == link_id
    )


def _endpoint_for_node(link: Dict[str, Any], node_id: str) -> Dict[str, Any]:
    return next(
        dict(endpoint)
        for endpoint in link.get("endpoints", [])
        if str(endpoint.get("node_id")) == node_id
    )


def _flow_contexts_from_plan(
    runtime_update_plan: RuntimeUpdatePlan,
    *,
    target_ut_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    selected_ut_ids = (
        set(str(ut_id) for ut_id in target_ut_ids)
        if target_ut_ids is not None
        else None
    )
    flow_contexts: Dict[str, Dict[str, Any]] = {}
    for subplan in runtime_update_plan.node_subplans.values():
        for action in subplan.get("actions", []):
            if str(action.get("action_type")) != "forwarding_entry":
                continue
            match = dict(action.get("intent", {}).get("match", {}))
            outcome = dict(action.get("intent", {}).get("outcome", {}))
            local_role = str(outcome.get("local_role", match.get("node_role", "")))
            if local_role == "ut_ingress":
                gateway_selection = dict(outcome.get("gateway_selection", {}))
                ut_id = str(gateway_selection.get("ut_id", ""))
                if selected_ut_ids is not None and ut_id not in selected_ut_ids:
                    continue
                gateway_bindings = [
                    dict(binding)
                    for binding in gateway_selection.get("gateway_class_bindings", [])
                ]
                if not gateway_bindings:
                    gateway_bindings = [
                        dict(gateway_selection.get("gateway_class_binding", {}))
                    ]
                for gateway_binding in gateway_bindings:
                    ingress_transport = dict(gateway_binding.get("ingress_transport", {}))
                    flow_id = str(
                        gateway_binding.get(
                            "flow_id",
                            gateway_selection.get("flow_id", match.get("flow_id", ut_id)),
                        )
                    )
                    if not flow_id:
                        continue
                    flow_contexts.setdefault(flow_id, {}).update(
                        {
                            "flow_id": flow_id,
                            "ut_id": ut_id,
                            "zone_id": gateway_selection.get("zone_id"),
                            "gateway_class_id": gateway_binding.get(
                                "gateway_class_id"
                            ),
                            "serving_satellite_id": gateway_binding.get(
                                "serving_satellite_id"
                            ),
                            "selected_gateway_id": gateway_binding.get(
                                "selected_gateway_id"
                            ),
                            "ut_ingress_label": ingress_transport.get("ingress_label"),
                        }
                    )
                continue

            flow_context = dict(outcome.get("flow_context", {}))
            if not flow_context:
                continue
            ut_id = str(flow_context.get("ut_id", ""))
            if selected_ut_ids is not None and ut_id not in selected_ut_ids:
                continue
            flow_id = str(flow_context.get("flow_id", match.get("flow_id", ut_id)))
            if not flow_id:
                continue
            if not flow_context.get("gateway_class_id"):
                zone_id = flow_context.get("zone_id")
                gateway_id = flow_context.get("selected_gateway_id")
                if zone_id and gateway_id:
                    flow_context["gateway_class_id"] = (
                        f"{zone_id}:default-gateway:{gateway_id}"
                    )
            flow_contexts.setdefault(flow_id, {}).update(flow_context)
    if not flow_contexts:
        return []
    return [flow_contexts[flow_id] for flow_id in sorted(flow_contexts)]


def _realization_support_summary(
    runtime_update_plan: RuntimeUpdatePlan,
    *,
    target_ut_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    flow_contexts = _flow_contexts_from_plan(
        runtime_update_plan,
        target_ut_ids=target_ut_ids,
    )
    blockers: List[str] = []
    direct_flow_ids: List[str] = []
    multi_hop_flow_ids: List[str] = []
    if not flow_contexts:
        blockers.append("no_resolved_experiment_flows")
        return {
            "supported": False,
            "total_flow_count": 0,
            "direct_flow_ids": direct_flow_ids,
            "multi_hop_flow_ids": multi_hop_flow_ids,
            "blockers": blockers,
        }
    flow_ids_by_node: Dict[str, set[str]] = {}
    for node_id, subplan in runtime_update_plan.node_subplans.items():
        for action in subplan.get("actions", []):
            flow_context = dict(action.get("intent", {}).get("outcome", {}).get("flow_context", {}))
            flow_id = str(flow_context.get("flow_id", ""))
            if flow_id:
                flow_ids_by_node.setdefault(str(node_id), set()).add(flow_id)
    for flow_context in flow_contexts:
        flow_id = str(flow_context.get("flow_id"))
        satellite_path = tuple(str(node_id) for node_id in flow_context.get("satellite_path", []))
        forward_labels = tuple(int(label) for label in flow_context.get("forward_labels", []))
        reverse_labels = tuple(int(label) for label in flow_context.get("reverse_labels", []))
        if len(satellite_path) <= 1:
            direct_flow_ids.append(flow_id)
        else:
            multi_hop_flow_ids.append(flow_id)
        if not satellite_path:
            blockers.append(f"missing_satellite_path:{flow_id}")
        if len(forward_labels) == 0 or len(reverse_labels) == 0:
            blockers.append(f"missing_mpls_labels:{flow_id}")
        if len(forward_labels) != len(reverse_labels):
            blockers.append(f"mismatched_forward_reverse_label_counts:{flow_id}")
        expected_segment_count = len(satellite_path)
        if len(forward_labels) != expected_segment_count:
            blockers.append(
                f"unexpected_forward_label_count:{flow_id}:{len(forward_labels)}:{expected_segment_count}"
            )
        for satellite_node_id in satellite_path:
            if flow_id not in flow_ids_by_node.get(str(satellite_node_id), set()):
                blockers.append(
                    f"missing_satellite_subplan_for_flow:{flow_id}:{satellite_node_id}"
                )
    return {
        "supported": len(blockers) == 0,
        "total_flow_count": len(flow_contexts),
        "direct_flow_ids": direct_flow_ids,
        "multi_hop_flow_ids": multi_hop_flow_ids,
        "blockers": blockers,
    }


@dataclass(frozen=True)
class MplsPathSegment:
    link_id: str
    source_node_id: str
    source_node_type: str
    source_interface: str
    source_ip: str
    destination_node_id: str
    destination_node_type: str
    destination_interface: str
    destination_ip: str
    label: int


@dataclass(frozen=True)
class DirectGatewayExperimentContext:
    flow_id: str
    ut_id: str
    zone_id: Optional[str]
    serving_satellite_id: str
    selected_gateway_id: str
    selected_gateway_satellite_id: str
    zone_return_prefixes: Tuple[str, ...]
    ping_target_ip: str
    target_host_node_id: str
    policy_table_id: int
    policy_rule_priority: int
    satellite_path: Tuple[str, ...]
    forward_labels: Tuple[int, ...]
    reverse_labels: Tuple[int, ...]
    ut_access_interface: str
    ut_access_ip: str
    ut_access_peer_ip: str
    gateway_access_interface: str
    gateway_access_ip: str
    gateway_access_peer_ip: str
    gateway_nat_interface: str
    gateway_nat_source_ip: str
    forward_segments: Tuple[MplsPathSegment, ...]
    reverse_segments: Tuple[MplsPathSegment, ...]
    validation_targets: Tuple[Dict[str, str], ...]
    gateway_class_id: Optional[str] = None
    return_path_intent: Dict[str, Any] = field(default_factory=dict)
    traffic_scope: str = ""

    @property
    def return_target_prefix(self) -> str:
        return f"{self.ut_access_ip}/32"

    @property
    def forward_label(self) -> int:
        return int(self.forward_labels[0])

    @property
    def reverse_label(self) -> int:
        return int(self.reverse_labels[0])

    @property
    def is_direct_gateway_path(self) -> bool:
        return len(self.satellite_path) <= 1

    @property
    def nodes_requiring_platform_labels(self) -> Tuple[str, ...]:
        nodes = {
            self.serving_satellite_id,
            self.selected_gateway_id,
            *self.satellite_path,
        }
        return tuple(sorted(node_id for node_id in nodes if node_id))

    @property
    def mpls_input_interfaces(self) -> Tuple[Tuple[str, str], ...]:
        receiving_interfaces = {
            (segment.destination_node_id, segment.destination_interface)
            for segment in (*self.forward_segments, *self.reverse_segments)
            if segment.destination_node_type in {"satellite", "gateway"}
        }
        return tuple(sorted(receiving_interfaces))


@dataclass(frozen=True)
class ZoneReturnInstallIntent:
    target_key: str
    selected_gateway_id: str
    zone_id: str
    zone_serving_satellite_id: str
    gateway_class_id: str
    route_prefixes: Tuple[str, ...]
    reverse_labels: Tuple[int, ...]
    reverse_segments: Tuple[MplsPathSegment, ...]
    flow_ids: Tuple[str, ...]
    ut_ids: Tuple[str, ...]
    current_linux_realization_mode: str
    semantic_scope: str = "gateway_to_zone_then_local_ut_delivery"
    target_backbone_state_model: str = "shared_gateway_zone_or_zone_return_reachability"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SharedZoneReturnInstallPlan:
    target_key: str
    selected_gateway_id: str
    zone_id: str
    zone_serving_satellite_id: str
    gateway_class_id: str
    route_prefixes: Tuple[str, ...]
    reverse_labels: Tuple[int, ...]
    reverse_segments: Tuple[MplsPathSegment, ...]
    flow_ids: Tuple[str, ...]
    ut_ids: Tuple[str, ...]
    install_behavior: str = "installed_shared_zone_return_backbone"
    planned_owner: str = "satellite_backbone"
    planned_scope: str = "gateway_zone_or_zone_return_target"
    planned_route_model: str = "shared_gateway_zone_or_zone_return_reachability"
    replaces_current_install_points: Tuple[str, ...] = (
        "gateway_ut32_return_route",
        "reverse_lfib_label_stack",
        "reverse_transit_lfib_entry",
    )
    preserves_edge_install_points: Tuple[str, ...] = (
        "serving_satellite_ut32_delivery_route",
        "gateway_nat_forward_rules",
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeCommandSpec:
    node_id: str
    phase: str
    description: str
    command: Tuple[str, ...]
    allow_failure: bool = False
    ownership_status: str = "linux_owned"
    apply_action: str = "applied_by_linux"
    backend_scope: str = "unclassified"
    skip_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "phase": self.phase,
            "description": self.description,
            "command": list(self.command),
            "allow_failure": self.allow_failure,
            "ownership_status": self.ownership_status,
            "apply_action": self.apply_action,
            "backend_scope": self.backend_scope,
            "skip_reason": self.skip_reason,
        }


@dataclass(frozen=True)
class RuntimeCommandResult:
    node_id: str
    phase: str
    description: str
    command: Tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    allow_failure: bool
    wall_seconds: float = 0.0

    @property
    def success(self) -> bool:
        return self.returncode == 0 or self.allow_failure

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "phase": self.phase,
            "description": self.description,
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "allow_failure": self.allow_failure,
            "success": self.success,
            "wall_seconds": self.wall_seconds,
        }


@dataclass(frozen=True)
class NodeLocalBatchSpec:
    node_id: str
    phase: str
    command_specs: Tuple[RuntimeCommandSpec, ...]

    @property
    def command_count(self) -> int:
        return len(self.command_specs)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "phase": self.phase,
            "command_count": self.command_count,
            "descriptions": [spec.description for spec in self.command_specs],
            "commands": [list(spec.command) for spec in self.command_specs],
            "allow_failures": [spec.allow_failure for spec in self.command_specs],
            "ownership_statuses": [spec.ownership_status for spec in self.command_specs],
            "apply_actions": [spec.apply_action for spec in self.command_specs],
            "backend_scopes": [spec.backend_scope for spec in self.command_specs],
            "skip_reasons": [spec.skip_reason for spec in self.command_specs],
        }


@dataclass(frozen=True)
class NodeLocalBatchResult:
    node_id: str
    phase: str
    command_count: int
    descriptions: Tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    wall_seconds: float
    command_timings: Tuple[Dict[str, Any], ...] = ()

    @property
    def success(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "phase": self.phase,
            "command_count": self.command_count,
            "descriptions": list(self.descriptions),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "success": self.success,
            "node_local_batch": True,
            "wall_seconds": self.wall_seconds,
            "command_timings": [dict(item) for item in self.command_timings],
        }


@dataclass(frozen=True)
class RuntimeExecutionResult:
    execution_id: str
    backend: Dict[str, Any]
    target_slice: Dict[str, Any]
    host_runtime_boundary: Dict[str, Any]
    preflight: Dict[str, Any]
    apply_results: List[Dict[str, Any]]
    verify_results: List[Dict[str, Any]]
    ping_result: Dict[str, Any]
    rollback_results: List[Dict[str, Any]]
    rollback_verification: Dict[str, Any]
    timing: Dict[str, Any]
    outcome: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HostRuntimeMutationBlocked(RuntimeError):
    """Raised when real execution would require host-side slot mutation."""


class RuntimeCommandFailure(RuntimeError):
    """Raised when one real runtime command fails."""

    def __init__(self, result: RuntimeCommandResult):
        self.result = result
        super().__init__(
            f"{result.node_id}:{result.phase}:{result.description} failed with {result.returncode}"
        )


class NodeLocalBatchFailure(RuntimeError):
    """Raised when one node-local batch execution fails."""

    def __init__(self, result: NodeLocalBatchResult):
        self.result = result
        super().__init__(f"{result.node_id}:{result.phase} batch failed with {result.returncode}")


@dataclass(frozen=True)
class ActiveRuntimeSession:
    session_id: str
    backend: Dict[str, Any]
    target_slice: Dict[str, Any]
    host_runtime_boundary: Dict[str, Any]
    preflight: Dict[str, Any]
    apply_results: List[Dict[str, Any]]
    verify_results: List[Dict[str, Any]]
    ping_results: List[Dict[str, Any]]
    timing: Dict[str, Any]
    contexts: Tuple[DirectGatewayExperimentContext, ...]
    context_preflights: Tuple[Dict[str, Any], ...]

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "backend": dict(self.backend or {}),
            "target_slice": dict(self.target_slice or {}),
            "host_runtime_boundary": dict(self.host_runtime_boundary or {}),
            "preflight": dict(self.preflight or {}),
            "apply_results": list(self.apply_results or []),
            "verify_results": list(self.verify_results or []),
            "ping_results": list(self.ping_results or []),
            "timing": dict(self.timing or {}),
        }


def _copy_bpf_prepare_timing_to_phase_timing(
    phase_timing: Dict[str, Any],
    prepare_timing: Dict[str, Any],
) -> None:
    for timing_key in (
        "bpf_reuse_audit_seconds",
        "bpf_map_compile_seconds",
        "bpf_map_write_seconds",
        "active_generation_flip_seconds",
        "bpf_attach_seconds",
        "bpf_live_role_read_seconds",
    ):
        phase_timing[timing_key] = float(prepare_timing.get(timing_key) or 0.0)
    for count_key in (
        "bpf_map_write_update_count",
        "bpf_map_update_count",
        "active_generation_update_count",
    ):
        phase_timing[count_key] = int(prepare_timing.get(count_key) or 0)


def _context_signature(context: DirectGatewayExperimentContext) -> str:
    """Fingerprint dataplane-relevant context so unchanged flows can be held."""
    return _fingerprint(asdict(context))


def _zone_id_from_gateway_class(gateway_class_id: str | None) -> str:
    rendered = str(gateway_class_id or "").strip()
    if ":default-gateway:" not in rendered:
        return ""
    return rendered.split(":default-gateway:", 1)[0]


def _zone_return_target_key(context: DirectGatewayExperimentContext) -> Tuple[str, str, str]:
    intent = dict(context.return_path_intent or {})
    explicit_target = dict(intent.get("shared_backbone_return_target") or {})
    explicit_gateway_id = str(explicit_target.get("gateway_id") or "").strip()
    explicit_zone_id = str(explicit_target.get("zone_id") or "").strip()
    explicit_serving_satellite_id = str(
        explicit_target.get("zone_serving_satellite_id") or ""
    ).strip()
    if explicit_gateway_id and explicit_zone_id and explicit_serving_satellite_id:
        return explicit_gateway_id, explicit_zone_id, explicit_serving_satellite_id

    zone_id = str(
        intent.get("zone_id")
        or context.zone_id
        or _zone_id_from_gateway_class(context.gateway_class_id)
        or f"legacy-ut:{context.ut_id}"
    )
    selected_gateway_id = str(
        intent.get("selected_gateway_id") or context.selected_gateway_id
    )
    zone_serving_satellite_id = str(
        intent.get("zone_serving_satellite_id") or context.serving_satellite_id
    )
    return selected_gateway_id, zone_id, zone_serving_satellite_id


def _zone_return_install_intents(
    contexts: Sequence[DirectGatewayExperimentContext],
) -> Tuple[ZoneReturnInstallIntent, ...]:
    grouped: Dict[Tuple[str, str, str], List[DirectGatewayExperimentContext]] = {}
    for context in contexts:
        grouped.setdefault(_zone_return_target_key(context), []).append(context)

    intents: List[ZoneReturnInstallIntent] = []
    for key in sorted(grouped):
        selected_gateway_id, zone_id, zone_serving_satellite_id = key
        grouped_contexts = sorted(grouped[key], key=lambda context: context.flow_id)
        path_signatures = {
            tuple(
                (
                    segment.link_id,
                    segment.source_node_id,
                    segment.source_interface,
                    segment.destination_node_id,
                    segment.destination_ip,
                )
                for segment in context.reverse_segments
            )
            for context in grouped_contexts
        }
        if len(path_signatures) != 1:
            raise RuntimeError(
                "conflicting shared zone return paths for "
                f"{selected_gateway_id}:{zone_id}:{zone_serving_satellite_id}"
            )
        representative = grouped_contexts[0]
        if (
            str(representative.reverse_segments[0].source_node_id)
            != str(selected_gateway_id)
            or str(representative.reverse_segments[-1].destination_node_id)
            != str(zone_serving_satellite_id)
        ):
            raise RuntimeError(
                "shared zone return target does not match reverse path endpoints for "
                f"{selected_gateway_id}:{zone_id}:{zone_serving_satellite_id}"
            )
        gateway_class_ids = sorted(
            {
                str(context.gateway_class_id or "")
                for context in grouped_contexts
                if str(context.gateway_class_id or "")
            }
        )
        route_prefixes = sorted(
            {
                str(prefix)
                for context in grouped_contexts
                for prefix in (
                    list(
                        dict(
                            dict(context.return_path_intent or {}).get(
                                "candidate_shared_zone_return_route",
                                {},
                            )
                        ).get("route_prefixes", [])
                    )
                    or list(context.zone_return_prefixes)
                )
                if str(prefix)
            }
        )
        realization_modes = sorted(
            {
                str(
                    dict(context.return_path_intent or {})
                    .get("current_linux_realization", {})
                    .get("mode", "per_ut_flow_compatibility")
                )
                for context in grouped_contexts
            }
        )
        intents.append(
            ZoneReturnInstallIntent(
                target_key=":".join(key),
                selected_gateway_id=selected_gateway_id,
                zone_id=zone_id,
                zone_serving_satellite_id=zone_serving_satellite_id,
                gateway_class_id=(
                    gateway_class_ids[0]
                    if len(gateway_class_ids) == 1
                    else ",".join(gateway_class_ids)
                ),
                route_prefixes=tuple(route_prefixes),
                reverse_labels=tuple(representative.reverse_labels),
                reverse_segments=tuple(representative.reverse_segments),
                flow_ids=tuple(context.flow_id for context in grouped_contexts),
                ut_ids=tuple(sorted({context.ut_id for context in grouped_contexts})),
                current_linux_realization_mode=(
                    realization_modes[0]
                    if len(realization_modes) == 1
                    else ",".join(realization_modes)
                ),
            )
        )
    return tuple(intents)


def _zone_return_install_intents_for_contexts(
    all_intents: Sequence[ZoneReturnInstallIntent],
    contexts: Sequence[DirectGatewayExperimentContext],
) -> Tuple[ZoneReturnInstallIntent, ...]:
    """Select stable active-group intents touched by a changed context subset."""
    target_keys = {":".join(_zone_return_target_key(context)) for context in contexts}
    return tuple(
        intent
        for intent in sorted(all_intents, key=lambda item: item.target_key)
        if intent.target_key in target_keys
    )


def _shared_zone_return_install_plans(
    intents: Sequence[ZoneReturnInstallIntent],
) -> Tuple[SharedZoneReturnInstallPlan, ...]:
    return tuple(
        SharedZoneReturnInstallPlan(
            target_key=intent.target_key,
            selected_gateway_id=intent.selected_gateway_id,
            zone_id=intent.zone_id,
            zone_serving_satellite_id=intent.zone_serving_satellite_id,
            gateway_class_id=intent.gateway_class_id,
            route_prefixes=tuple(intent.route_prefixes),
            reverse_labels=tuple(intent.reverse_labels),
            reverse_segments=tuple(intent.reverse_segments),
            flow_ids=tuple(intent.flow_ids),
            ut_ids=tuple(intent.ut_ids),
        )
        for intent in sorted(intents, key=lambda item: item.target_key)
    )


def _shared_gz_backbone_intents(
    intents: Sequence[ZoneReturnInstallIntent],
) -> Tuple[ZoneReturnInstallIntent, ...]:
    return tuple(
        intent
        for intent in intents
        if str(intent.current_linux_realization_mode) == "shared_gz_backbone"
    )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def _compatibility_return_state_counts(
    contexts: Sequence[DirectGatewayExperimentContext],
) -> Dict[str, int]:
    flow_count = len(tuple(contexts))
    reverse_label_count = sum(len(context.reverse_labels) for context in contexts)
    reverse_transit_lfib_count = sum(
        max(0, len(context.reverse_labels) - 1) for context in contexts
    )
    return {
        "flow_count": flow_count,
        "gateway_ut32_return_route_count": flow_count,
        "serving_satellite_ut32_delivery_route_count": flow_count,
        "reverse_label_count": reverse_label_count,
        "reverse_transit_lfib_entry_count": reverse_transit_lfib_count,
        "gateway_nat_forward_rule_count": flow_count * 3,
        "current_backbone_return_state_item_count": (
            flow_count + reverse_label_count + reverse_transit_lfib_count
        ),
        "current_edge_return_state_item_count": flow_count + (flow_count * 3),
    }


def _context_linux_realization_mode(context: DirectGatewayExperimentContext) -> str:
    return str(
        dict(context.return_path_intent or {})
        .get("current_linux_realization", {})
        .get("mode", "per_ut_flow_compatibility")
    )


def _uses_shared_gz_backbone(
    contexts: Sequence[DirectGatewayExperimentContext],
) -> bool:
    realized_contexts = [
        context
        for context in contexts
        if dict(context.return_path_intent or {}).get("intent_kind")
        == "zone_return_delivery"
    ]
    return bool(realized_contexts) and all(
        _context_linux_realization_mode(context) == "shared_gz_backbone"
        for context in realized_contexts
    )


def _shared_gz_return_state_counts(
    contexts: Sequence[DirectGatewayExperimentContext],
    intents: Sequence[ZoneReturnInstallIntent],
) -> Dict[str, int]:
    flow_count = len(tuple(contexts))
    intent_count = len(tuple(intents))
    shared_zone_route_count = sum(
        max(1, len(tuple(intent.route_prefixes))) for intent in intents
    )
    shared_reverse_label_entry_count = sum(len(intent.reverse_labels) for intent in intents)
    shared_reverse_transit_lfib_count = sum(
        max(0, len(intent.reverse_labels) - 1) for intent in intents
    )
    shared_reverse_pop_lfib_count = sum(1 for intent in intents if intent.reverse_labels)
    return {
        "flow_count": flow_count,
        "gateway_ut32_return_route_count": 0,
        "serving_satellite_ut32_delivery_route_count": flow_count,
        "reverse_label_count": 0,
        "reverse_transit_lfib_entry_count": 0,
        "gateway_nat_forward_rule_count": flow_count * 3,
        "current_backbone_return_state_item_count": intent_count,
        "current_edge_return_state_item_count": flow_count + (flow_count * 3),
        "shared_gz_backbone_group_count": intent_count,
        "shared_gz_conceptual_backbone_state_item_count": intent_count,
        "forbidden_legacy_backbone_state_item_count": 0,
        "linux_shared_zone_route_count": shared_zone_route_count,
        "linux_shared_reverse_label_entry_count": shared_reverse_label_entry_count,
        "linux_shared_reverse_lfib_entry_count": shared_reverse_label_entry_count,
        "linux_shared_reverse_transit_lfib_entry_count": shared_reverse_transit_lfib_count,
        "linux_shared_reverse_pop_lfib_entry_count": shared_reverse_pop_lfib_count,
        "linux_shared_backbone_command_state_item_count": (
            shared_zone_route_count + shared_reverse_label_entry_count
        ),
    }


def _actual_return_state_counts(
    contexts: Sequence[DirectGatewayExperimentContext],
    intents: Sequence[ZoneReturnInstallIntent],
) -> Dict[str, int]:
    if _uses_shared_gz_backbone(contexts):
        return _shared_gz_return_state_counts(contexts, intents)
    return _compatibility_return_state_counts(contexts)


def _candidate_shared_return_label_stack_keys(
    contexts: Sequence[DirectGatewayExperimentContext],
) -> Tuple[str, ...]:
    keys = set()
    for context in contexts:
        intent = dict(context.return_path_intent or {})
        candidate = dict(intent.get("candidate_shared_return_label_stack") or {})
        target_key = str(candidate.get("target_key") or "").strip()
        if target_key:
            keys.add(target_key)
            continue
        shared_target = dict(intent.get("shared_backbone_return_target") or {})
        shared_target_key = str(shared_target.get("target_key") or "").strip()
        if not shared_target_key:
            continue
        reverse_signature = ":".join(segment.link_id for segment in context.reverse_segments)
        keys.add(f"{shared_target_key}:reverse-path:{reverse_signature}")
    return tuple(sorted(keys))


def _candidate_shared_zone_return_route_keys(
    contexts: Sequence[DirectGatewayExperimentContext],
) -> Tuple[str, ...]:
    keys = set()
    for context in contexts:
        intent = dict(context.return_path_intent or {})
        candidate = dict(intent.get("candidate_shared_zone_return_route") or {})
        target_key = str(candidate.get("target_key") or "").strip()
        if target_key:
            keys.add(target_key)
            continue
        shared_target = dict(intent.get("shared_backbone_return_target") or {})
        shared_target_key = str(shared_target.get("target_key") or "").strip()
        if shared_target_key:
            keys.add(f"{shared_target_key}:zone-route")
    return tuple(sorted(keys))


def _zone_return_install_boundary_payload(
    *,
    all_contexts: Sequence[DirectGatewayExperimentContext],
    reapplied_contexts: Sequence[DirectGatewayExperimentContext],
    all_intents: Sequence[ZoneReturnInstallIntent],
    reapplied_intents: Sequence[ZoneReturnInstallIntent],
) -> Dict[str, Any]:
    all_counts = _actual_return_state_counts(all_contexts, all_intents)
    reapplied_counts = _actual_return_state_counts(reapplied_contexts, reapplied_intents)
    all_legacy_projection_counts = _compatibility_return_state_counts(all_contexts)
    reapplied_legacy_projection_counts = _compatibility_return_state_counts(
        reapplied_contexts
    )
    all_target_count = len(tuple(all_intents))
    reapplied_target_count = len(tuple(reapplied_intents))
    all_candidate_label_stack_keys = _candidate_shared_return_label_stack_keys(
        all_contexts
    )
    reapplied_candidate_label_stack_keys = _candidate_shared_return_label_stack_keys(
        reapplied_contexts
    )
    all_candidate_zone_route_keys = _candidate_shared_zone_return_route_keys(
        all_contexts
    )
    reapplied_candidate_zone_route_keys = _candidate_shared_zone_return_route_keys(
        reapplied_contexts
    )
    all_shared_zone_return_install_plans = _shared_zone_return_install_plans(
        all_intents
    )
    reapplied_shared_zone_return_install_plans = _shared_zone_return_install_plans(
        reapplied_intents
    )
    return {
        "boundary_kind": "derived_shared_zone_return_targets",
        "install_behavior": (
            "shared_gz_backbone"
            if _uses_shared_gz_backbone(all_contexts)
            else "shared_zone_return_backbone_with_edge_compatibility"
        ),
        "shared_zone_return_install_plan_behavior": (
            "installed_shared_zone_return_backbone"
        ),
        "candidate_shared_zone_route_behavior": (
            "installed_as_shared_zone_return_backbone"
        ),
        "candidate_shared_label_stack_behavior": (
            "installed_as_shared_zone_return_backbone"
        ),
        "state_count_model": {
            "current_backbone_return_state_item_count": (
                "conceptual shared G/Z backbone group count when install_behavior "
                "is shared_gz_backbone; legacy per-UT backbone item count otherwise"
            ),
            "forbidden_legacy_backbone_counts": (
                "gateway_ut32_return_route_count, reverse_label_count, and "
                "reverse_transit_lfib_entry_count are actual forbidden legacy "
                "backbone install counts, not historical projections"
            ),
            "legacy_compatibility_projection": (
                "what the old per-UT/per-flow compatibility model would have "
                "installed for the same active flows"
            ),
            "linux_shared_backbone_command_counts": (
                "actual Linux command-state entries for shared G/Z route, LFIB, "
                "transit swap, and Z pop"
            ),
            "edge_counts": (
                "serving-satellite local UT delivery and gateway edge policy remain "
                "per-UT/per-flow edge state, outside backbone MPLS return semantics"
            ),
        },
        "scale_summary": {
            "all": {
                **all_counts,
                "legacy_compatibility_projection": all_legacy_projection_counts,
                "target_shared_backbone_return_state_item_count": all_target_count,
                "candidate_shared_zone_route_count": len(
                    all_candidate_zone_route_keys
                ),
                "candidate_shared_label_stack_count": len(
                    all_candidate_label_stack_keys
                ),
                "current_to_target_backbone_state_ratio": _ratio(
                    int(all_counts["current_backbone_return_state_item_count"]),
                    all_target_count,
                ),
                "current_reverse_labels_to_candidate_label_stack_ratio": _ratio(
                    int(all_counts["reverse_label_count"]),
                    len(all_candidate_label_stack_keys),
                ),
            },
            "reapplied": {
                **reapplied_counts,
                "legacy_compatibility_projection": reapplied_legacy_projection_counts,
                "target_shared_backbone_return_state_item_count": reapplied_target_count,
                "candidate_shared_zone_route_count": len(
                    reapplied_candidate_zone_route_keys
                ),
                "candidate_shared_label_stack_count": len(
                    reapplied_candidate_label_stack_keys
                ),
                "current_to_target_backbone_state_ratio": _ratio(
                    int(reapplied_counts["current_backbone_return_state_item_count"]),
                    reapplied_target_count,
                ),
                "current_reverse_labels_to_candidate_label_stack_ratio": _ratio(
                    int(reapplied_counts["reverse_label_count"]),
                    len(reapplied_candidate_label_stack_keys),
                ),
            },
        },
        "all_intent_count": all_target_count,
        "reapplied_intent_count": reapplied_target_count,
        "all_candidate_shared_zone_route_count": len(all_candidate_zone_route_keys),
        "reapplied_candidate_shared_zone_route_count": len(
            reapplied_candidate_zone_route_keys
        ),
        "all_shared_zone_return_install_plan_count": len(
            all_shared_zone_return_install_plans
        ),
        "reapplied_shared_zone_return_install_plan_count": len(
            reapplied_shared_zone_return_install_plans
        ),
        "all_candidate_shared_label_stack_count": len(all_candidate_label_stack_keys),
        "reapplied_candidate_shared_label_stack_count": len(
            reapplied_candidate_label_stack_keys
        ),
        "all_candidate_shared_zone_route_keys": list(all_candidate_zone_route_keys),
        "reapplied_candidate_shared_zone_route_keys": list(
            reapplied_candidate_zone_route_keys
        ),
        "all_candidate_shared_label_stack_keys": list(all_candidate_label_stack_keys),
        "reapplied_candidate_shared_label_stack_keys": list(
            reapplied_candidate_label_stack_keys
        ),
        "all_intents": [intent.to_dict() for intent in all_intents],
        "reapplied_intents": [intent.to_dict() for intent in reapplied_intents],
        "all_shared_zone_return_install_plans": [
            plan.to_dict() for plan in all_shared_zone_return_install_plans
        ],
        "reapplied_shared_zone_return_install_plans": [
            plan.to_dict() for plan in reapplied_shared_zone_return_install_plans
        ],
        "install_mapping": _shared_zone_return_install_mapping(
            all_intents=all_intents,
            reapplied_intents=reapplied_intents,
        ),
    }


def _shared_zone_return_apply_specs(
    intents: Sequence[ZoneReturnInstallIntent],
) -> List[RuntimeCommandSpec]:
    specs: List[RuntimeCommandSpec] = []
    for intent in intents:
        if not intent.route_prefixes:
            raise RuntimeError(
                "shared zone return install requires at least one route prefix for "
                f"{intent.target_key}"
            )
        if not intent.reverse_labels or not intent.reverse_segments:
            raise RuntimeError(
                "shared zone return install requires reverse labels and segments for "
                f"{intent.target_key}"
            )
        first_segment = intent.reverse_segments[0]
        for route_prefix in intent.route_prefixes:
            specs.append(
                RuntimeCommandSpec(
                    node_id=intent.selected_gateway_id,
                    phase="apply",
                    description=(
                        "install shared gateway zone-return ingress route "
                        f"for {intent.target_key}"
                    ),
                    command=(
                        "ip",
                        "route",
                        "replace",
                        str(route_prefix),
                        "encap",
                        "mpls",
                        str(intent.reverse_labels[0]),
                        "via",
                        "inet",
                        first_segment.destination_ip,
                        "dev",
                        first_segment.source_interface,
                    ),
                )
            )
        for index, segment in enumerate(intent.reverse_segments[1:], start=1):
            specs.append(
                RuntimeCommandSpec(
                    node_id=segment.source_node_id,
                    phase="apply",
                    description=(
                        "install shared reverse LFIB on "
                        f"{segment.source_node_id} for {intent.target_key}"
                    ),
                    command=(
                        "ip",
                        "-f",
                        "mpls",
                        "route",
                        "replace",
                        str(intent.reverse_labels[index - 1]),
                        "as",
                        str(intent.reverse_labels[index]),
                        "via",
                        "inet",
                        segment.destination_ip,
                        "dev",
                        segment.source_interface,
                    ),
                )
            )
        specs.append(
            RuntimeCommandSpec(
                node_id=intent.zone_serving_satellite_id,
                phase="apply",
                description=(
                    "install shared serving-satellite reverse pop entry "
                    f"for {intent.target_key}"
                ),
                command=(
                    "ip",
                    "-f",
                    "mpls",
                    "route",
                    "replace",
                    str(intent.reverse_labels[-1]),
                    "dev",
                    "lo",
                ),
            )
        )
    return specs


def _shared_zone_return_rollback_specs(
    intents: Sequence[ZoneReturnInstallIntent],
) -> List[RuntimeCommandSpec]:
    specs: List[RuntimeCommandSpec] = []
    for intent in intents:
        for route_prefix in intent.route_prefixes:
            specs.append(
                RuntimeCommandSpec(
                    node_id=intent.selected_gateway_id,
                    phase="rollback",
                    description=(
                        "delete shared gateway zone-return ingress route "
                        f"for {intent.target_key}"
                    ),
                    command=("ip", "route", "del", str(route_prefix)),
                    allow_failure=True,
                )
            )
        for label in intent.reverse_labels:
            specs.append(
                RuntimeCommandSpec(
                    node_id=(
                        intent.zone_serving_satellite_id
                        if int(label) == int(intent.reverse_labels[-1])
                        else next(
                            (
                                segment.source_node_id
                                for index, segment in enumerate(
                                    intent.reverse_segments[1:],
                                    start=1,
                                )
                                if int(intent.reverse_labels[index - 1]) == int(label)
                            ),
                            intent.selected_gateway_id,
                        )
                    ),
                    phase="rollback",
                    description=(
                        "delete shared zone-return MPLS entry "
                        f"for {intent.target_key}"
                    ),
                    command=("ip", "-f", "mpls", "route", "del", str(label)),
                    allow_failure=True,
                )
            )
    return specs


def _shared_zone_return_rollback_specs_for_contexts(
    contexts: Sequence[DirectGatewayExperimentContext],
    *,
    preserve_target_keys: Sequence[str] = (),
) -> List[RuntimeCommandSpec]:
    preserved = {str(target_key) for target_key in preserve_target_keys}
    intents = tuple(
        intent
        for intent in _shared_gz_backbone_intents(
            _zone_return_install_intents(contexts)
        )
        if intent.target_key not in preserved
    )
    return _shared_zone_return_rollback_specs(intents)


def _command_specs_payload(specs: Sequence[RuntimeCommandSpec]) -> List[Dict[str, Any]]:
    return [spec.to_dict() for spec in specs]


def _runtime_epoch_intent_payload(
    runtime_update_plan: RuntimeUpdatePlan,
) -> Dict[str, Any]:
    target_snapshot = dict(runtime_update_plan.target_snapshot or {})
    return {
        "plan_id": runtime_update_plan.plan_id,
        "slice_id": target_snapshot.get("slice_id"),
        "snapshot_id": target_snapshot.get("snapshot_id"),
        "time_slice": target_snapshot.get("time_slice"),
        "planner": dict(runtime_update_plan.planner or {}),
        "apply_model": dict(runtime_update_plan.apply_model or {}),
    }


def _gz_fec_intent_payload(runtime_update_plan: RuntimeUpdatePlan) -> Dict[str, Any]:
    plan = gz_fec_plan(runtime_update_plan)
    return {
        "model": plan.get("model"),
        "plan_id": plan.get("plan_id"),
        "target_snapshot": dict(plan.get("target_snapshot", {})),
        "planning_scope": dict(plan.get("planning_scope", {})),
        "forward_fecs": list(plan.get("forward_fecs", []) or []),
        "return_fecs": list(plan.get("return_fecs", []) or []),
    }


def _bpf_owned_intent_payload(
    runtime_update_plan: RuntimeUpdatePlan,
) -> Dict[str, Any]:
    runtime_epoch_intent = _runtime_epoch_intent_payload(runtime_update_plan)
    gz_intent = _gz_fec_intent_payload(runtime_update_plan)
    return {
        "schema_version": "skyseed-bpf-owned-intent-v1",
        "runtime_epoch_intent": runtime_epoch_intent,
        "gz_fec_intent": gz_intent,
    }


def _bpf_owned_entry_hash(entries: Sequence[Dict[str, Any]]) -> str:
    return _sha256({"entries": list(entries)})


def _bpf_owned_attached_interfaces_from_entries(
    entries: Sequence[Dict[str, Any]],
) -> List[str]:
    interfaces: set[str] = set()
    for entry in entries:
        for link_id in list(entry.get("link_ids", []) or []):
            if str(link_id):
                interfaces.add(str(link_id))
        next_hop = dict(entry.get("next_hop") or {})
        logical_edge = next_hop.get("logical_edge")
        if str(logical_edge or ""):
            interfaces.add(str(logical_edge))
    return sorted(interfaces)


def _bpf_owned_short_name(prefix: str, seed: str) -> str:
    return f"{prefix}{_fingerprint({'seed': seed})[:11]}"[:15]


def _bpf_owned_stable_u32(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], byteorder="little", signed=False)


def _bpf_owned_runtime_generation_id(
    runtime_update_plan: RuntimeUpdatePlan,
) -> Tuple[int, str]:
    target_snapshot = dict(runtime_update_plan.target_snapshot or {})
    for field_name in ("slice_id", "time_slice", "snapshot_id"):
        value = str(target_snapshot.get(field_name) or "").strip()
        if not value:
            continue
        suffix = value.rsplit("-", 1)[-1]
        if suffix.isdigit():
            return max(0, int(suffix) - 1), f"target_snapshot.{field_name}"
    plan_id = str(runtime_update_plan.plan_id or "")
    return _bpf_owned_stable_u32(plan_id) & 0x7FFFFFFF, "stable_plan_id_hash"


def _gateway_nat_source_ip_for_flow(
    *,
    base_ip: str,
    ut_id: str,
    used_ips: set[str],
) -> str:
    parts = str(base_ip).split(".")
    if len(parts) != 4:
        used_ips.add(str(base_ip))
        return str(base_ip)
    try:
        octets = [int(part) for part in parts]
    except ValueError:
        used_ips.add(str(base_ip))
        return str(base_ip)
    if any(octet < 0 or octet > 255 for octet in octets):
        used_ips.add(str(base_ip))
        return str(base_ip)

    ut_number_text = "".join(ch for ch in str(ut_id) if ch.isdigit())
    ut_number = int(ut_number_text or "0")
    preferred_host_octet = 32 + (ut_number % 190)
    prefix = ".".join(str(octet) for octet in octets[:3])
    reserved = {0, 1, 2, 255}
    for offset in range(223):
        host_octet = 32 + ((preferred_host_octet - 32 + offset) % 223)
        if host_octet in reserved:
            continue
        candidate = f"{prefix}.{host_octet}"
        if candidate not in used_ips:
            used_ips.add(candidate)
            return candidate

    used_ips.add(str(base_ip))
    return str(base_ip)


def _bpf_owned_netns_interface_ref(*, node_id: str, interface_name: str) -> str:
    return f"netns:{node_id}:{interface_name}"


def _bpf_owned_slot_inventory_by_name(topology_path: Path) -> Dict[str, Dict[str, Any]]:
    topology = _load_json(topology_path)
    inventory: Dict[str, Dict[str, Any]] = {}
    for slots in dict(topology.get("slot_inventory", {})).values():
        for slot in slots or []:
            slot_name = str(slot.get("slot_name") or "")
            if slot_name:
                inventory[slot_name] = dict(slot)
    return inventory


def _bpf_owned_host_interface_name(
    *,
    slot_name: str,
    endpoint: Dict[str, Any],
    role: str,
) -> str:
    seed = (
        f"{slot_name}:{role}:{endpoint.get('node_id')}:{endpoint.get('interface_name')}"
    )
    return _bpf_owned_short_name("vhs", seed)


def _bpf_owned_ut_access_satellite_endpoint(
    *,
    context: DirectGatewayExperimentContext,
) -> Dict[str, Any]:
    return {
        "node_id": context.serving_satellite_id,
        "node_type": "satellite",
        "interface_name": context.ut_access_interface,
        "ip_address": context.ut_access_peer_ip,
    }


def _bpf_owned_endpoint_role_for_slot(
    *,
    slot: Dict[str, Any],
    endpoint: Dict[str, Any],
) -> str:
    slot_type = str(slot.get("slot_type") or "")
    endpoint_node_id = str(endpoint.get("node_id") or "")
    preconnected = slot.get("preconnected_endpoint")
    if isinstance(preconnected, dict) and (
        str(preconnected.get("node_id") or "") == endpoint_node_id
        and str(preconnected.get("interface_name") or "")
        == str(endpoint.get("interface_name") or "")
    ):
        return "preconnected"
    if slot_type in {"ut_access", "gw_access"}:
        return f"candidate:{endpoint_node_id}"
    endpoint_a_template = dict(slot.get("endpoint_a_template") or {})
    if str(endpoint_a_template.get("interface_name") or "") == str(
        endpoint.get("interface_name") or ""
    ):
        return "candidate:a"
    return "candidate:b"


def _bpf_owned_host_interfaces_for_contexts(
    *,
    topology_path: Path,
    contexts: Sequence[DirectGatewayExperimentContext],
) -> List[str]:
    attached: set[str] = set()
    for context in contexts:
        attached.add(
            _bpf_owned_netns_interface_ref(
                node_id=context.serving_satellite_id,
                interface_name=context.ut_access_interface,
            )
        )
        attached.add(
            _bpf_owned_netns_interface_ref(
                node_id=context.selected_gateway_id,
                interface_name=context.gateway_nat_interface,
            )
        )
        for segment in context.forward_segments:
            attached.add(
                _bpf_owned_netns_interface_ref(
                    node_id=segment.source_node_id,
                    interface_name=segment.source_interface,
                )
            )
        for segment in context.reverse_segments:
            attached.add(
                _bpf_owned_netns_interface_ref(
                    node_id=segment.source_node_id,
                    interface_name=segment.source_interface,
                )
            )
            attached.add(
                _bpf_owned_netns_interface_ref(
                    node_id=segment.destination_node_id,
                    interface_name=segment.destination_interface,
                )
            )
    return sorted(attached)


def _bpf_owned_ut_access_host_interface(
    *,
    inventory: Dict[str, Dict[str, Any]],
    context: DirectGatewayExperimentContext,
) -> str:
    return _bpf_owned_netns_interface_ref(
        node_id=context.serving_satellite_id,
        interface_name=context.ut_access_interface,
    )


def _bpf_owned_host_interface_for_segment_endpoint(
    *,
    inventory: Dict[str, Dict[str, Any]],
    segment: MplsPathSegment,
    endpoint_role: str,
) -> str:
    slot = inventory.get(segment.link_id)
    if slot is None:
        return ""
    if endpoint_role == "source":
        endpoint = {
            "node_id": segment.source_node_id,
            "node_type": segment.source_node_type,
            "interface_name": segment.source_interface,
            "ip_address": segment.source_ip,
        }
    else:
        endpoint = {
            "node_id": segment.destination_node_id,
            "node_type": segment.destination_node_type,
            "interface_name": segment.destination_interface,
            "ip_address": segment.destination_ip,
        }
    if str(endpoint.get("node_type") or "") not in {"satellite", "gateway"}:
        return ""
    role = _bpf_owned_endpoint_role_for_slot(slot=slot, endpoint=endpoint)
    return _bpf_owned_host_interface_name(
        slot_name=segment.link_id,
        endpoint=endpoint,
        role=role,
    )


def _bpf_owned_dataplane_entries_for_contexts(
    *,
    topology_path: Path,
    contexts: Sequence[DirectGatewayExperimentContext],
) -> List[Dict[str, Any]]:
    if not contexts:
        return []
    inventory = _bpf_owned_slot_inventory_by_name(topology_path)
    entries: List[Dict[str, Any]] = []
    seen: Dict[Tuple[str, str, str], Tuple[int, int]] = {}
    nat_handoff_key_counts: Dict[Tuple[str, str, str], int] = {}
    for context in contexts:
        if not context.reverse_segments or not context.gateway_nat_interface:
            continue
        nat_ingress_interface = _bpf_owned_netns_interface_ref(
            node_id=context.selected_gateway_id,
            interface_name=context.gateway_nat_interface,
        )
        nat_handoff_key = (
            nat_ingress_interface,
            str(context.gateway_nat_source_ip),
            str(context.selected_gateway_id),
        )
        nat_handoff_key_counts[nat_handoff_key] = (
            nat_handoff_key_counts.get(nat_handoff_key, 0) + 1
        )

    def add_entry(
        *,
        context: DirectGatewayExperimentContext,
        direction: str,
        segment: MplsPathSegment,
        target_ip: str,
        ingress_interface: str,
        egress_interface: str,
        l2_src_node_id: str,
        l2_src_interface: str,
        l2_dst_node_id: str,
        l2_dst_interface: str,
        new_dst_ip: str = "",
        route_selection_priority: int = 0,
    ) -> bool:
        if not target_ip or not ingress_interface or not egress_interface:
            return False
        if ingress_interface == egress_interface:
            return False
        key = (direction, ingress_interface, target_ip)
        existing = seen.get(key)
        if existing is not None and route_selection_priority <= existing[0]:
            return False
        entry = {
            "scope": "bpf_owned_gz_packet_route",
            "direction": direction,
            "flow_id": context.flow_id,
            "ut_id": context.ut_id,
            "target_ip": target_ip,
            "target_host_node_id": context.target_host_node_id,
            "selected_gateway_id": context.selected_gateway_id,
            "serving_satellite_id": context.serving_satellite_id,
            "slot_name": segment.link_id,
            "slot_type": "satellite_core_gz",
            "ingress_interface": ingress_interface,
            "egress_interface": egress_interface,
            "runtime_generation": 0,
            "parameter_generation": 0,
            "parameter_loss_ppm": 0,
            "parameter_mark": _bpf_owned_stable_u32(
                f"{context.flow_id}:{direction}:{segment.link_id}:{target_ip}"
            )
            & 0xFFFF,
            "route_selection_priority": int(route_selection_priority),
            "segment": {
                "link_id": segment.link_id,
                "source_node_id": segment.source_node_id,
                "source_interface": segment.source_interface,
                "destination_node_id": segment.destination_node_id,
                "destination_interface": segment.destination_interface,
                "label": segment.label,
            },
            "l2_rewrite": {
                "source_node_id": l2_src_node_id,
                "source_interface": l2_src_interface,
                "destination_node_id": l2_dst_node_id,
                "destination_interface": l2_dst_interface,
            },
        }
        if new_dst_ip:
            entry["new_dst_ip"] = new_dst_ip
        if existing is None:
            seen[key] = (int(route_selection_priority), len(entries))
            entries.append(entry)
        else:
            entry_index = existing[1]
            entries[entry_index] = entry
            seen[key] = (int(route_selection_priority), entry_index)
        return True

    def route_priority(context: DirectGatewayExperimentContext, target_ip: str) -> int:
        priority = 0
        if str(target_ip) == str(context.ping_target_ip):
            priority += 100
        if (
            str(context.traffic_scope) == "special_prefix_override"
            or ":override:" in str(context.flow_id)
        ):
            priority += 10
        return priority

    for context in contexts:
        forward_targets = sorted(
            {
                str(target.get("ip_address") or "")
                for target in context.validation_targets
                if str(target.get("ip_address") or "")
            }
            | {str(context.ping_target_ip)}
        )
        ut_access_ingress_interface = _bpf_owned_ut_access_host_interface(
            inventory=inventory,
            context=context,
        )
        if context.forward_segments:
            first_forward_segment = context.forward_segments[0]
            first_gateway_egress_interface = (
                _bpf_owned_netns_interface_ref(
                    node_id=first_forward_segment.source_node_id,
                    interface_name=first_forward_segment.source_interface,
                )
            )
            for target_ip in forward_targets:
                add_entry(
                    context=context,
                    direction="g_to_z_access_ingress",
                    segment=first_forward_segment,
                    target_ip=target_ip,
                    ingress_interface=ut_access_ingress_interface,
                    egress_interface=first_gateway_egress_interface,
                    l2_src_node_id=first_forward_segment.source_node_id,
                    l2_src_interface=first_forward_segment.source_interface,
                    l2_dst_node_id=first_forward_segment.destination_node_id,
                    l2_dst_interface=first_forward_segment.destination_interface,
                    route_selection_priority=route_priority(context, target_ip),
                )
        for segment in context.forward_segments:
            ingress_interface = _bpf_owned_netns_interface_ref(
                node_id=segment.source_node_id,
                interface_name=segment.source_interface,
            )
            egress_interface = _bpf_owned_netns_interface_ref(
                node_id=segment.source_node_id,
                interface_name=segment.source_interface,
            )
            for target_ip in forward_targets:
                add_entry(
                    context=context,
                    direction="g_to_z",
                    segment=segment,
                    target_ip=target_ip,
                    ingress_interface=ingress_interface,
                    egress_interface=egress_interface,
                    l2_src_node_id=segment.source_node_id,
                    l2_src_interface=segment.source_interface,
                    l2_dst_node_id=segment.destination_node_id,
                    l2_dst_interface=segment.destination_interface,
                    route_selection_priority=route_priority(context, target_ip),
                )
        for segment in context.reverse_segments:
            ingress_interface = _bpf_owned_netns_interface_ref(
                node_id=segment.destination_node_id,
                interface_name=segment.destination_interface,
            )
            egress_interface = _bpf_owned_netns_interface_ref(
                node_id=context.serving_satellite_id,
                interface_name=context.ut_access_interface,
            )
            add_entry(
                context=context,
                direction="z_to_g",
                segment=segment,
                target_ip=context.ut_access_ip,
                ingress_interface=ingress_interface,
                egress_interface=egress_interface,
                l2_src_node_id=context.serving_satellite_id,
                l2_src_interface=context.ut_access_interface,
                l2_dst_node_id=context.ut_id,
                l2_dst_interface=context.ut_access_interface,
                new_dst_ip=context.ut_access_ip,
                route_selection_priority=route_priority(context, context.ut_access_ip),
            )
        if context.reverse_segments and context.gateway_nat_interface:
            first_reverse_segment = context.reverse_segments[0]
            nat_ingress_interface = _bpf_owned_netns_interface_ref(
                node_id=context.selected_gateway_id,
                interface_name=context.gateway_nat_interface,
            )
            nat_handoff_key = (
                nat_ingress_interface,
                str(context.gateway_nat_source_ip),
                str(context.selected_gateway_id),
            )
            if nat_handoff_key_counts.get(nat_handoff_key, 0) > 1:
                continue
            reverse_egress_interface = _bpf_owned_netns_interface_ref(
                node_id=first_reverse_segment.source_node_id,
                interface_name=first_reverse_segment.source_interface,
            )
            add_entry(
                context=context,
                direction="z_to_g_nat_ingress",
                segment=first_reverse_segment,
                target_ip=context.gateway_nat_source_ip,
                ingress_interface=nat_ingress_interface,
                egress_interface=reverse_egress_interface,
                l2_src_node_id=first_reverse_segment.source_node_id,
                l2_src_interface=first_reverse_segment.source_interface,
                l2_dst_node_id=first_reverse_segment.destination_node_id,
                l2_dst_interface=first_reverse_segment.destination_interface,
                new_dst_ip=context.ut_access_ip,
                route_selection_priority=route_priority(
                    context,
                    context.gateway_nat_source_ip,
                ),
            )
    return [entry for entry in entries if entry]


def _bpf_owned_forward_targets_for_context(
    context: DirectGatewayExperimentContext,
) -> List[str]:
    context_target_node_id = str(context.target_host_node_id or "")
    return sorted(
        {
            str(target.get("ip_address") or "")
            for target in context.validation_targets
            if str(target.get("ip_address") or "")
            and (
                str(target.get("node_id") or "") == context_target_node_id
                or str(target.get("ip_address") or "") == str(context.ping_target_ip)
            )
        }
        | {str(context.ping_target_ip)}
    )


def _bpf_mpls_synthetic_ifindex(interface_ref: str) -> int:
    # Keep synthetic indexes positive and below the signed 31-bit ceiling used by
    # common tooling while preserving deterministic offline map keys.
    return 1 + (_bpf_owned_stable_u32(f"bpf-mpls-ifindex:{interface_ref}") & 0x7FFFFFFE)


def _bpf_mpls_synthetic_mac(interface_ref: str) -> str:
    digest = hashlib.sha256(f"bpf-mpls-mac:{interface_ref}".encode("utf-8")).digest()
    first = (digest[0] & 0xFE) | 0x02
    return ":".join(f"{byte:02x}" for byte in bytes([first]) + digest[1:6])


def _bpf_mpls_path_node_sequence(segments: Sequence[MplsPathSegment]) -> List[str]:
    if not segments:
        return []
    return [
        str(segments[0].source_node_id),
        *[str(segment.destination_node_id) for segment in segments],
    ]


def _bpf_mpls_label_allocation_discriminator(
    *,
    direction: str,
    context: DirectGatewayExperimentContext,
) -> str:
    if str(direction) != "z_to_g":
        return ""
    return ":".join(
        [
            "zone_local_delivery",
            str(context.serving_satellite_id),
            str(context.ut_id),
            str(context.ut_access_interface),
            str(context.ut_access_ip),
        ]
    )


def _bpf_mpls_label_allocations(
    *,
    contexts: Sequence[DirectGatewayExperimentContext],
    generation_id: int,
    g_to_z_direction: str,
    z_to_g_direction: str,
    stable_u32_fn: Any,
) -> Tuple[Dict[Tuple[str, str, int, str], int], List[Dict[str, Any]]]:
    intents: List[Dict[str, Any]] = []

    def fec_ids(context: DirectGatewayExperimentContext) -> Tuple[str, str]:
        return _bpf_mpls_context_fec_ids(context, stable_u32_fn)

    for context in sorted(
        contexts,
        key=lambda item: (
            str(item.flow_id),
            str(item.ut_id),
            str(item.selected_gateway_id),
        ),
    ):
        forward_fec_id, reverse_fec_id = fec_ids(context)
        for direction, fec_id, segments in (
            (g_to_z_direction, forward_fec_id, context.forward_segments),
            (z_to_g_direction, reverse_fec_id, context.reverse_segments),
        ):
            path_node_sequence = _bpf_mpls_path_node_sequence(segments)
            for hop_index, segment in enumerate(segments):
                intents.append(
                    {
                        "generation": int(generation_id),
                        "direction": direction,
                        "fec_id": fec_id,
                        "flow_id": context.flow_id,
                        "ut_id": context.ut_id,
                        "zone_id": context.zone_id,
                        "selected_gateway_id": context.selected_gateway_id,
                        "hop_index": hop_index,
                        "path_node_sequence": path_node_sequence,
                        "link_id": segment.link_id,
                        "source_node_id": segment.source_node_id,
                        "source_interface": segment.source_interface,
                        "destination_node_id": segment.destination_node_id,
                        "destination_interface": segment.destination_interface,
                        "original_label": int(segment.label),
                        "allocation_discriminator": (
                            _bpf_mpls_label_allocation_discriminator(
                                direction=direction,
                                context=context,
                            )
                        ),
                    }
                )

    allocation_by_key: Dict[Tuple[str, str, int, str], int] = {}
    allocation_table: List[Dict[str, Any]] = []
    used_labels: Dict[int, Tuple[str, str, int, str]] = {}
    for index, intent in enumerate(
        sorted(
            intents,
            key=lambda item: (
                int(item["generation"]),
                str(item["direction"]),
                str(item["fec_id"]),
                int(item["hop_index"]),
                str(item.get("allocation_discriminator") or ""),
                str(item["flow_id"]),
            ),
        )
    ):
        key = (
            str(intent["direction"]),
            str(intent["fec_id"]),
            int(intent["hop_index"]),
            str(intent.get("allocation_discriminator") or ""),
        )
        if key in allocation_by_key:
            allocation_table.append(
                {
                    **intent,
                    "allocated_label": int(allocation_by_key[key]),
                    "shared_allocation": True,
                    "allocation_scope": "discriminator_shared",
                }
            )
            continue
        label = BPF_MPLS_LABEL_BASE + index
        if label > BPF_MPLS_LABEL_MAX:
            raise RuntimeError(
                "bpf_mpls_label_allocation_exhausted:"
                f"requested_index={index}; max_label={BPF_MPLS_LABEL_MAX}"
            )
        if label in used_labels and used_labels[label] != key:
            raise RuntimeError(
                "bpf_mpls_label_allocator_internal_collision:"
                f"label={label}; first={used_labels[label]}; replacement={key}"
            )
        used_labels[label] = key
        allocation_by_key[key] = label
        allocation_table.append(
            {
                **intent,
                "allocated_label": label,
                "shared_allocation": False,
                "allocation_scope": (
                    "zone_local_delivery"
                    if str(intent.get("allocation_discriminator") or "")
                    else "fec_hop"
                ),
            }
        )
    return allocation_by_key, allocation_table


def _bpf_mpls_context_fec_ids(
    context: DirectGatewayExperimentContext,
    stable_u32_fn: Any,
) -> Tuple[str, str]:
    zone_id = str(context.zone_id or "")
    gateway_class_id = str(
        context.gateway_class_id
        or (
            f"{zone_id}:default-gateway:{context.selected_gateway_id}"
            if zone_id and context.selected_gateway_id
            else context.selected_gateway_id
        )
    )
    common_seed = {
        "model": "zone_class_anchor_fec_v1",
        "zone_id": zone_id,
        "gateway_class_id": gateway_class_id,
        "selected_gateway_id": str(context.selected_gateway_id),
        "selected_gateway_satellite_id": str(context.selected_gateway_satellite_id),
        "zone_anchor_satellite_id": str(context.serving_satellite_id),
        "traffic_scope": str(context.traffic_scope or "ordinary_internet_default"),
        "satellite_path": list(context.satellite_path),
    }
    forward_seed = {
        **common_seed,
        "direction": "g_to_z",
        "link_ids": [segment.link_id for segment in context.forward_segments],
    }
    reverse_seed = {
        **common_seed,
        "direction": "z_to_g",
        "link_ids": [segment.link_id for segment in context.reverse_segments],
        "return_prefixes": list(context.zone_return_prefixes),
    }
    fec_id = (
        "gz-fec-"
        f"{stable_u32_fn(json.dumps(forward_seed, sort_keys=True)) & 0xffffffff:08x}"
    )
    reverse_fec_id = (
        "gz-fec-"
        f"{stable_u32_fn(json.dumps(reverse_seed, sort_keys=True)) & 0xffffffff:08x}"
        ":return"
    )
    return fec_id, reverse_fec_id


def _bpf_owned_previous_attach_plan(readiness: Dict[str, Any]) -> Dict[str, List[str]]:
    prepare = dict(
        dict(readiness.get("prepare_steps") or {}).get("real_backend_prepare")
        or {}
    )
    raw_plan = dict(prepare.get("attach_plan") or readiness.get("attach_plan") or {})
    plan: Dict[str, List[str]] = {
        str(interface_ref): sorted({str(direction) for direction in directions})
        for interface_ref, directions in raw_plan.items()
        if str(interface_ref)
    }
    details = list(
        readiness.get("attached_interface_details")
        or prepare.get("attached_interface_details")
        or []
    )
    for detail_any in details:
        detail = dict(detail_any)
        interface_ref = str(
            detail.get("interface_ref") or detail.get("interface_name") or ""
        )
        if not interface_ref or interface_ref in plan:
            continue
        directions = [
            str(direction)
            for direction in list(detail.get("attach_directions") or [])
            if str(direction)
        ]
        if directions:
            plan[interface_ref] = sorted(set(directions))
    return plan


def _bpf_mpls_generation_map_capacities(
    *,
    current_counts: Dict[str, Any],
    active_counts: Optional[Dict[str, Any]] = None,
    slack_entries: int = 16,
) -> Dict[str, int]:
    target_counts = dict(current_counts or {})
    active_counts = dict(active_counts or target_counts)

    def count(name: str, source: Dict[str, Any]) -> int:
        return int(source.get(name) or 0)

    return {
        "classifiers": max(
            128,
            count("ingress_classifier_map", active_counts)
            + count("ingress_classifier_map", target_counts)
            + int(slack_entries),
        ),
        "lfib": max(
            256,
            count("core_lfib_map", active_counts)
            + count("core_lfib_map", target_counts)
            + int(slack_entries),
        ),
        "handoffs": max(
            128,
            count("egress_handoff_map", active_counts)
            + count("egress_handoff_map", target_counts)
            + int(slack_entries),
        ),
        "interface_counter_entries": 8192,
    }


def _bpf_owned_attach_reuse_plan(
    *,
    compiled: Dict[str, Any],
    previous_readiness: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    required_plan = {
        str(interface_ref): sorted({str(direction) for direction in directions})
        for interface_ref, directions in dict(compiled.get("attach_plan") or {}).items()
        if str(interface_ref)
    }
    if not previous_readiness:
        return {
            "reuse_eligible": False,
            "reason": "no_previous_bpf_owned_readiness",
            "missing_interfaces": [],
            "missing_directions": [],
            "required_interface_count": len(required_plan),
            "previous_interface_count": 0,
            "reused_attach_count": 0,
            "delta_attach_count": 0,
            "full_attach_count": len(required_plan),
            "reuse_rejected_reason": "no_previous_bpf_owned_readiness",
            "attach_strategy": "full_attach_required",
            "delta_attach_plan": {},
            "delta_attach_interfaces": [],
        }
    previous_plan = _bpf_owned_previous_attach_plan(dict(previous_readiness))
    if not previous_plan:
        return {
            "reuse_eligible": False,
            "reason": "previous_attach_plan_missing",
            "missing_interfaces": [],
            "missing_directions": [],
            "required_interface_count": len(required_plan),
            "previous_interface_count": 0,
            "reused_attach_count": 0,
            "delta_attach_count": 0,
            "full_attach_count": len(required_plan),
            "reuse_rejected_reason": "previous_attach_plan_missing",
            "attach_strategy": "full_attach_required",
            "delta_attach_plan": {},
            "delta_attach_interfaces": [],
        }
    previous_prepare = dict(
        dict(previous_readiness.get("prepare_steps") or {}).get("real_backend_prepare")
        or {}
    )
    previous_counts = dict(
        previous_prepare.get("compiled_mpls_map_counts")
        or previous_readiness.get("mpls_map_entry_counts")
        or {}
    )
    current_counts = dict(compiled.get("compiled_mpls_map_counts") or {})
    previous_capacities = dict(
        previous_prepare.get("map_capacities")
        or previous_readiness.get("map_capacities")
        or {}
    )
    if not previous_capacities and previous_counts:
        previous_capacities = _bpf_mpls_generation_map_capacities(
            current_counts=previous_counts,
        )
    capacity_mismatches: List[Dict[str, Any]] = []
    capacity_checks = (
        ("ingress_classifier_map", "classifiers"),
        ("core_lfib_map", "lfib"),
        ("egress_handoff_map", "handoffs"),
    )
    for count_key, capacity_key in capacity_checks:
        previous_capacity = previous_capacities.get(capacity_key)
        if previous_capacity is None:
            continue
        current_count = int(current_counts.get(count_key) or 0)
        previous_count = int(previous_counts.get(count_key) or 0)
        required_double_buffer_capacity = previous_count + current_count + 16
        if required_double_buffer_capacity > int(previous_capacity):
            capacity_mismatches.append(
                {
                    "map_name": count_key,
                    "current_count": current_count,
                    "previous_active_count": previous_count,
                    "required_double_buffer_capacity": required_double_buffer_capacity,
                    "previous_capacity": int(previous_capacity),
                }
            )
    previous_details = {
        str(detail.get("interface_ref") or detail.get("interface_name") or ""): dict(detail)
        for detail in list(
            previous_readiness.get("attached_interface_details")
            or previous_prepare.get("attached_interface_details")
            or []
        )
    }
    current_details = {
        str(detail.get("interface_ref") or detail.get("interface_name") or ""): dict(detail)
        for detail in list(compiled.get("attached_interface_details") or [])
    }
    ifindex_mismatches: List[Dict[str, Any]] = []
    role_mismatches: List[Dict[str, Any]] = []
    for interface_ref in sorted(set(previous_plan) & set(required_plan)):
        previous_detail = dict(previous_details.get(interface_ref) or {})
        current_detail = dict(current_details.get(interface_ref) or {})
        previous_ifindex = previous_detail.get("ifindex")
        current_ifindex = current_detail.get("ifindex")
        if (
            previous_ifindex not in (None, "")
            and current_ifindex not in (None, "")
            and int(previous_ifindex) != int(current_ifindex)
        ):
            ifindex_mismatches.append(
                {
                    "interface_ref": interface_ref,
                    "previous_ifindex": int(previous_ifindex),
                    "current_ifindex": int(current_ifindex),
                }
            )
        previous_roles = sorted(
            {str(role) for role in list(previous_detail.get("expected_roles") or [])}
        )
        current_roles = sorted(
            {str(role) for role in list(current_detail.get("expected_roles") or [])}
        )
        missing_current_roles = sorted(set(current_roles) - set(previous_roles))
        extra_previous_roles = sorted(set(previous_roles) - set(current_roles))
        if previous_roles and current_roles and missing_current_roles:
            role_mismatches.append(
                {
                    "interface_ref": interface_ref,
                    "previous_roles": previous_roles,
                    "current_roles": current_roles,
                    "missing_current_roles": missing_current_roles,
                    "extra_previous_roles": extra_previous_roles,
                    "role_sets_match": False,
                }
            )
    missing_interfaces = sorted(
        interface_ref
        for interface_ref in required_plan
        if interface_ref not in previous_plan
    )
    missing_directions: List[Dict[str, Any]] = []
    for interface_ref, required_directions in required_plan.items():
        previous_directions = set(previous_plan.get(interface_ref) or [])
        missing = sorted(
            direction
            for direction in required_directions
            if direction not in previous_directions
        )
        if missing:
            missing_directions.append(
                {
                    "interface_ref": interface_ref,
                    "missing_directions": missing,
                    "previous_directions": sorted(previous_directions),
                    "required_directions": list(required_directions),
                }
            )
    delta_attach_plan: Dict[str, List[str]] = {
        interface_ref: list(required_plan[interface_ref])
        for interface_ref in missing_interfaces
    }
    for item in missing_directions:
        interface_ref = str(item.get("interface_ref") or "")
        if interface_ref:
            delta_attach_plan[interface_ref] = sorted(
                set(delta_attach_plan.get(interface_ref, []))
                | {str(direction) for direction in item.get("missing_directions", [])}
            )
    extra_previous_interfaces = sorted(
        interface_ref
        for interface_ref in previous_plan
        if interface_ref not in required_plan
    )
    interface_set_changed = bool(missing_interfaces or missing_directions)
    reuse_rejected_reason = ""
    if capacity_mismatches:
        reuse_rejected_reason = "map_capacity_mismatch"
    elif ifindex_mismatches:
        reuse_rejected_reason = "ifindex_binding_mismatch"
    elif role_mismatches:
        reuse_rejected_reason = "interface_role_set_mismatch"
    reuse_eligible = not reuse_rejected_reason
    attach_strategy = (
        "full_attach_required"
        if not reuse_eligible
        else ("delta_attach" if delta_attach_plan else "full_reuse")
    )
    reused_attach_count = len(required_plan) - len(delta_attach_plan)
    outcome_labels: List[str] = []
    if reuse_eligible and not delta_attach_plan:
        outcome_labels.append("STEADY_ATTACH_REUSE_CONTRACT_PASS")
    elif reuse_eligible and delta_attach_plan:
        outcome_labels.append("EXPLICIT_DELTA_ATTACH_TRANSACTION_REQUIRED")
    else:
        outcome_labels.append("STEADY_ATTACH_REUSE_CONTRACT_STILL_BLOCKED")
        if reuse_rejected_reason == "interface_role_set_mismatch":
            outcome_labels.append("INTERFACE_ROLE_SET_MISMATCH_ROOT_CAUSE_IDENTIFIED")
    return {
        "reuse_eligible": reuse_eligible,
        "reason": reuse_rejected_reason,
        "required_interface_count": len(required_plan),
        "previous_interface_count": len(previous_plan),
        "missing_interfaces": missing_interfaces,
        "missing_directions": missing_directions,
        "required_attach_plan": required_plan,
        "previous_attach_plan": previous_plan,
        "extra_previous_interfaces": extra_previous_interfaces,
        "interface_set_changed": interface_set_changed,
        "steady_epoch_map_only": reuse_eligible and not delta_attach_plan,
        "delta_attach_plan": delta_attach_plan,
        "delta_attach_interfaces": sorted(delta_attach_plan),
        "reused_attach_count": max(0, reused_attach_count if reuse_eligible else 0),
        "delta_attach_count": len(delta_attach_plan) if reuse_eligible else 0,
        "full_attach_count": 0 if reuse_eligible else len(required_plan),
        "reuse_rejected_reason": reuse_rejected_reason,
        "attach_strategy": attach_strategy,
        "ifindex_mismatches": ifindex_mismatches,
        "interface_role_set_mismatches": role_mismatches,
        "map_capacity_mismatches": capacity_mismatches,
        "map_capacities": previous_capacities,
        "outcome_labels": outcome_labels,
    }


def _bpf_owned_existing_readiness_reuse_summary(
    readiness: Dict[str, Any],
) -> Dict[str, Any]:
    attached_interfaces = [
        str(interface_ref)
        for interface_ref in list(readiness.get("attached_interfaces") or [])
        if str(interface_ref)
    ]
    return {
        "reuse_eligible": True,
        "reason": "bpf_owned_readiness_already_matches_runtime_epoch",
        "reuse_rejected_reason": "",
        "attach_strategy": "existing_readiness_reused_no_prepare",
        "reused_attach_count": len(attached_interfaces),
        "delta_attach_count": 0,
        "full_attach_count": 0,
        "interface_set_changed": False,
        "steady_epoch_map_only": True,
        "required_interface_count": len(attached_interfaces),
        "previous_interface_count": len(attached_interfaces),
        "missing_interfaces": [],
        "missing_directions": [],
        "delta_attach_plan": {},
        "delta_attach_interfaces": [],
        "source": "proved_existing_bpf_owned_readiness",
    }


def _bpf_owned_existing_readiness_prepare_evidence(
    readiness: Dict[str, Any],
) -> Dict[str, Any]:
    attached_interfaces = list(readiness.get("attached_interfaces") or [])
    previous_prepare = dict(
        dict(readiness.get("prepare_steps") or {}).get("real_backend_prepare") or {}
    )
    timing_breakdown = dict(previous_prepare.get("timing_breakdown") or {})
    timing_breakdown.update(
        {
            "bpf_reuse_audit_seconds": 0.0,
            "bpf_map_compile_seconds": 0.0,
            "bpf_map_write_seconds": 0.0,
            "bpf_map_write_update_count": 0,
            "bpf_map_update_count": 0,
            "active_generation_flip_seconds": 0.0,
            "active_generation_update_count": 0,
            "bpf_attach_seconds": 0.0,
            "bpf_live_role_read_seconds": 0.0,
            "total_wall_seconds": 0.0,
        }
    )
    counts = dict(
        readiness.get("mpls_map_entry_counts")
        or previous_prepare.get("compiled_mpls_map_counts")
        or {}
    )
    return {
        "ok": True,
        "ready": True,
        "reason": "",
        "prepare_mode": "existing_readiness_reused_no_prepare",
        "attach_lifecycle_mode": "existing_readiness_reused",
        "attached_interfaces": attached_interfaces,
        "attached_interface_details": list(
            readiness.get("attached_interface_details") or []
        ),
        "resolved_mpls_flow_entries": list(
            readiness.get("resolved_mpls_flow_entries") or []
        ),
        "compiled_mpls_map_counts": counts,
        "map_capacities": dict(
            readiness.get("map_capacities")
            or previous_prepare.get("map_capacities")
            or {}
        ),
        "resident_attach_coverage": dict(
            readiness.get("resident_attach_coverage") or {}
        ),
        "attach_plan": dict(
            previous_prepare.get("attach_plan")
            or readiness.get("attach_plan")
            or {}
        ),
        "attach_reuse": _bpf_owned_existing_readiness_reuse_summary(readiness),
        "steps": {
            "compile": {
                "skipped": True,
                "reason": "existing_bpf_owned_readiness_proved",
            },
            "attach": {
                "skipped": True,
                "reason": "existing_bpf_owned_readiness_proved",
                "attached_interfaces": attached_interfaces,
            },
            "map_load": {
                "skipped": True,
                "reason": "existing_bpf_owned_readiness_proved",
            },
            "live_interface_roles_after_map_load": {
                "skipped": True,
                "reason": "existing_bpf_owned_readiness_proved",
            },
            "interface_role_completeness_after_map_load": {
                "skipped": True,
                "reason": "existing_bpf_owned_readiness_proved",
                "ok": True,
            },
        },
        "reused_attach_count": len(attached_interfaces),
        "delta_attach_count": 0,
        "full_attach_count": 0,
        "active_generation": readiness.get("active_generation"),
        "active_generation_switch_count": 0,
        "loaded_runtime_generation_count": 0,
        "loaded_gz_fec_count": int(readiness.get("map_entry_count") or 0),
        "loaded_dataplane_route_count": 0,
        "loaded_dataplane_parameter_entry_count": 0,
        "loaded_classifier_count": int(counts.get("ingress_classifier_map") or 0),
        "loaded_core_lfib_count": int(counts.get("core_lfib_map") or 0),
        "loaded_egress_handoff_count": int(counts.get("egress_handoff_map") or 0),
        "loaded_interface_role_count": 0,
        "timing_breakdown": timing_breakdown,
        "wall_seconds": 0.0,
        "evidence_source": "proved_existing_bpf_owned_readiness",
    }


def _bpf_owned_resident_attach_budget(proof: Dict[str, Any]) -> Dict[str, Any]:
    configured = dict(proof.get("resident_attach_coverage_budget") or {})
    if not configured:
        configured = dict(proof.get("bpf_attach_budget") or {})
    budget_source = "profile" if configured else "default"
    max_attach_interfaces = int(
        configured.get("max_attach_interfaces")
        or configured.get("max_resident_attach_interfaces")
        or BPF_OWNED_DEFAULT_MAX_ATTACH_INTERFACES
    )
    return {
        "schema_version": "skyseed-bpf-resident-attach-budget-v1",
        "budget_source": budget_source,
        "max_attach_interfaces": max_attach_interfaces,
    }


def _bpf_owned_future_interface_materialization_evidence(
    *,
    future_horizon_interfaces: Sequence[str],
    interface_ifindex: Callable[[str], int],
) -> Dict[str, Any]:
    unique_interfaces = sorted(
        {str(item) for item in future_horizon_interfaces if str(item)}
    )
    materialized: List[Dict[str, Any]] = []
    non_materialized: List[Dict[str, Any]] = []
    for interface_ref in unique_interfaces:
        try:
            ifindex = int(interface_ifindex(interface_ref))
            materialized.append(
                {
                    "interface_ref": interface_ref,
                    "ifindex": ifindex,
                    "classification": "FUTURE_MATERIALIZED_ATTACH_ELIGIBLE",
                    "attach_eligible": True,
                    "readiness_blocking": False,
                    "reason": "future_horizon_live_ifindex_resolved",
                }
            )
        except Exception as exc:
            non_materialized.append(
                {
                    "interface_ref": interface_ref,
                    "classification": "FUTURE_INTERFACE_NOT_MATERIALIZED",
                    "attach_eligible": False,
                    "readiness_blocking": False,
                    "reason": "future_horizon_ifindex_missing",
                    "error": str(exc),
                }
            )
    return {
        "schema_version": "skyseed-bpf-future-interface-materialization-v1",
        "future_horizon_interface_count": len(unique_interfaces),
        "future_materialized_attach_eligible_interface_count": len(materialized),
        "future_non_materialized_capacity_only_interface_count": len(
            non_materialized
        ),
        "materialized_interfaces": materialized,
        "non_materialized_interfaces": non_materialized,
    }


def _bpf_owned_merge_resident_attach_coverage(
    *,
    compiled: Dict[str, Any],
    resident_compiled: Optional[Dict[str, Any]],
    future_interface_materialization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    def _plan(value: Dict[str, Any]) -> Dict[str, set[str]]:
        return {
            str(interface_ref): {str(direction) for direction in directions}
            for interface_ref, directions in dict(value.get("attach_plan") or {}).items()
            if str(interface_ref)
        }

    def _details_by_ref(value: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        details: Dict[str, Dict[str, Any]] = {}
        for detail_any in list(value.get("attached_interface_details") or []):
            detail = dict(detail_any)
            interface_ref = str(
                detail.get("interface_ref") or detail.get("interface_name") or ""
            )
            if interface_ref:
                details[interface_ref] = detail
        return details

    def _max_counts(*values: Dict[str, Any]) -> Dict[str, int]:
        keys = {
            str(key)
            for value in values
            for key in dict(value or {})
            if str(key)
        }
        return {
            key: max(int(dict(value or {}).get(key) or 0) for value in values)
            for key in sorted(keys)
        }

    current_plan = _plan(compiled)
    current_details = _details_by_ref(compiled)
    active_materialized_interfaces = sorted(current_plan)
    active_details: List[Dict[str, Any]] = []
    for interface_ref in active_materialized_interfaces:
        detail = dict(current_details.get(interface_ref) or {})
        detail["interface_ref"] = interface_ref
        detail.setdefault("interface_name", interface_ref)
        detail["attach_directions"] = sorted(current_plan[interface_ref])
        active_details.append(detail)
    active_attach_plan = {
        interface_ref: sorted(directions)
        for interface_ref, directions in sorted(current_plan.items())
    }

    if not resident_compiled:
        merged = {
            **dict(compiled),
            "attach_plan": active_attach_plan,
            "attached_interfaces": active_materialized_interfaces,
            "attached_interface_details": active_details,
            "active_materialized_interfaces": active_materialized_interfaces,
            "resident_attach_eligible_interfaces": [],
            "future_horizon_interfaces": [],
            "future_materialized_attach_eligible_interfaces": [],
            "future_non_materialized_capacity_only_interfaces": [],
            "compile_capacity_interfaces": active_materialized_interfaces,
            "compile_capacity_mpls_map_counts": dict(
                compiled.get("compiled_mpls_map_counts") or {}
            ),
            "resident_attach_coverage": {
                "enabled": False,
                "current_interface_count": len(current_plan),
                "resident_interface_count": 0,
                "resident_attach_eligible_interface_count": 0,
                "future_horizon_interface_count": 0,
                "future_materialized_attach_eligible_interface_count": 0,
                "future_non_materialized_capacity_only_interface_count": 0,
                "compile_capacity_interface_count": len(current_plan),
                "attached_interface_count": len(current_plan),
                "added_interface_count": 0,
                "added_interfaces": [],
                "future_horizon_interfaces": [],
                "future_materialized_attach_eligible_interfaces": [],
                "future_non_materialized_capacity_only_interfaces": [],
                "resident_attach_eligible_interfaces": [],
                "compile_capacity_interfaces": active_materialized_interfaces,
                "readiness_blocking_future_interface_count": 0,
                "interface_classifications": [
                    {
                        "interface_ref": interface_ref,
                        "classification": "ACTIVE_MATERIALIZED_CURRENT_GENERATION",
                        "attach_eligible": True,
                        "readiness_blocking": True,
                        "reason": "active_current_generation_materialized_interface",
                    }
                    for interface_ref in active_materialized_interfaces
                ],
                "outcome_labels": [
                    "RESIDENT_ATTACH_COVERAGE_CONTRACT_ADDED",
                ],
            },
        }
        merged["interface_set_contract"] = {
            "schema_version": "skyseed-bpf-interface-set-contract-v2",
            "active_materialized_interfaces": active_materialized_interfaces,
            "resident_attach_eligible_interfaces": [],
            "future_horizon_interfaces": [],
            "future_materialized_attach_eligible_interfaces": [],
            "future_non_materialized_capacity_only_interfaces": [],
            "compile_capacity_interfaces": active_materialized_interfaces,
            "future_interface_classification": "",
            "future_interfaces_readiness_blocking": False,
            "resident_attach_bootstrap_scope": "active_materialized",
        }
        return merged

    merged = dict(compiled)
    resident_plan = _plan(resident_compiled)
    resident_details = _details_by_ref(resident_compiled)
    resident_interfaces = set(resident_plan)
    active_interfaces = set(current_plan)
    future_horizon_interfaces = sorted(resident_interfaces - active_interfaces)
    materialization = dict(future_interface_materialization or {})
    materialized_records = {
        str(item.get("interface_ref") or ""): dict(item)
        for item in list(materialization.get("materialized_interfaces") or [])
        if str(item.get("interface_ref") or "")
    }
    non_materialized_records = {
        str(item.get("interface_ref") or ""): dict(item)
        for item in list(materialization.get("non_materialized_interfaces") or [])
        if str(item.get("interface_ref") or "")
    }
    future_materialized_attach_eligible_interfaces = sorted(
        set(future_horizon_interfaces) & set(materialized_records)
    )
    future_non_materialized_capacity_only_interfaces = sorted(
        set(future_horizon_interfaces)
        - set(future_materialized_attach_eligible_interfaces)
    )
    attach_plan_sets: Dict[str, set[str]] = {
        interface_ref: set(directions)
        for interface_ref, directions in current_plan.items()
    }
    for interface_ref in future_materialized_attach_eligible_interfaces:
        attach_plan_sets[interface_ref] = set(resident_plan.get(interface_ref) or [])
    for interface_ref in sorted(active_interfaces & resident_interfaces):
        attach_plan_sets[interface_ref] = set(
            attach_plan_sets.get(interface_ref) or set()
        ) | set(resident_plan.get(interface_ref) or set())
    resident_attach_eligible_interfaces = sorted(attach_plan_sets)
    compile_capacity_interfaces = sorted(resident_interfaces | active_interfaces)
    added_interfaces = sorted(set(resident_plan) - set(current_plan))
    active_counts = dict(compiled.get("compiled_mpls_map_counts") or {})
    resident_counts = dict(resident_compiled.get("compiled_mpls_map_counts") or {})
    attach_plan = {
        interface_ref: sorted(directions)
        for interface_ref, directions in sorted(attach_plan_sets.items())
    }

    attached_details: List[Dict[str, Any]] = []
    interface_classifications: List[Dict[str, Any]] = []
    for interface_ref in sorted(attach_plan):
        current_detail = dict(current_details.get(interface_ref) or {})
        resident_detail = dict(resident_details.get(interface_ref) or {})
        detail = dict(current_detail or resident_detail)
        detail["interface_ref"] = interface_ref
        detail.setdefault("interface_name", interface_ref)
        detail["attach_directions"] = list(attach_plan[interface_ref])
        detail["expected_roles"] = sorted(
            {
                *[
                    str(role)
                    for role in list(current_detail.get("expected_roles") or [])
                    if str(role)
                ],
                *[
                    str(role)
                    for role in list(resident_detail.get("expected_roles") or [])
                    if str(role)
                ],
            }
        )
        classification = "ACTIVE_MATERIALIZED_CURRENT_GENERATION"
        reason = "active_current_generation_materialized_interface"
        if interface_ref in active_interfaces and interface_ref in resident_interfaces:
            classification = "ACTIVE_MATERIALIZED_CURRENT_AND_HORIZON"
            reason = "active_current_interface_with_resident_role_coverage"
        elif interface_ref in future_materialized_attach_eligible_interfaces:
            classification = "FUTURE_MATERIALIZED_ATTACH_ELIGIBLE"
            reason = "future_horizon_interface_live_ifindex_resolved"
            materialized = dict(materialized_records.get(interface_ref) or {})
            if materialized.get("ifindex") not in (None, ""):
                detail["ifindex"] = int(materialized.get("ifindex"))
                detail["ifindex_error"] = ""
        attached_details.append(detail)
        interface_classifications.append(
            {
                "interface_ref": interface_ref,
                "classification": classification,
                "attach_eligible": True,
                "readiness_blocking": interface_ref in active_interfaces,
                "reason": reason,
                "attach_directions": list(attach_plan[interface_ref]),
                "expected_roles": list(detail.get("expected_roles") or []),
            }
        )
    for interface_ref in future_non_materialized_capacity_only_interfaces:
        evidence = dict(non_materialized_records.get(interface_ref) or {})
        interface_classifications.append(
            {
                "interface_ref": interface_ref,
                "classification": "FUTURE_NON_MATERIALIZED_CAPACITY_ONLY",
                "attach_eligible": False,
                "readiness_blocking": False,
                "reason": str(
                    evidence.get("reason")
                    or "future_horizon_interface_not_materialized"
                ),
                "error": str(evidence.get("error") or ""),
            }
        )

    merged["attach_plan"] = attach_plan
    merged["attached_interfaces"] = sorted(attach_plan)
    merged["attached_interface_details"] = attached_details
    merged["active_materialized_interfaces"] = active_materialized_interfaces
    merged["resident_attach_eligible_interfaces"] = (
        resident_attach_eligible_interfaces
    )
    merged["future_horizon_interfaces"] = future_horizon_interfaces
    merged["future_materialized_attach_eligible_interfaces"] = (
        future_materialized_attach_eligible_interfaces
    )
    merged["future_non_materialized_capacity_only_interfaces"] = (
        future_non_materialized_capacity_only_interfaces
    )
    merged["compile_capacity_interfaces"] = compile_capacity_interfaces
    merged["compile_capacity_mpls_map_counts"] = _max_counts(
        active_counts,
        resident_counts,
    )
    merged["interface_set_contract"] = {
        "schema_version": "skyseed-bpf-interface-set-contract-v2",
        "active_materialized_interfaces": active_materialized_interfaces,
        "resident_attach_eligible_interfaces": resident_attach_eligible_interfaces,
        "future_horizon_interfaces": future_horizon_interfaces,
        "future_materialized_attach_eligible_interfaces": (
            future_materialized_attach_eligible_interfaces
        ),
        "future_non_materialized_capacity_only_interfaces": (
            future_non_materialized_capacity_only_interfaces
        ),
        "compile_capacity_interfaces": compile_capacity_interfaces,
        "future_interface_classification": (
            "FUTURE_MATERIALIZED_INTERFACES_PREATTACHED"
            if future_materialized_attach_eligible_interfaces
            else (
                "FUTURE_INTERFACE_NOT_MATERIALIZED"
                if future_horizon_interfaces
                else ""
            )
        ),
        "future_capacity_only_classification": (
            "FUTURE_INTERFACE_NOT_MATERIALIZED"
            if future_non_materialized_capacity_only_interfaces
            else ""
        ),
        "future_interfaces_readiness_blocking": False,
        "requires_prematerialized_future_attach_coverage": True,
        "resident_attach_bootstrap_scope": (
            "active_plus_future_materialized_attach_eligible"
        ),
        "outcome_labels": [
            "RESIDENT_ATTACH_COVERAGE_CONTRACT_ADDED",
            *(
                ["FUTURE_MATERIALIZED_INTERFACES_PREATTACHED"]
                if future_materialized_attach_eligible_interfaces
                else []
            ),
        ],
    }
    merged["resident_attach_coverage"] = {
        "enabled": True,
        "current_interface_count": len(current_plan),
        "resident_interface_count": len(resident_plan),
        "resident_attach_eligible_interface_count": len(
            resident_attach_eligible_interfaces
        ),
        "future_horizon_interface_count": len(future_horizon_interfaces),
        "future_materialized_attach_eligible_interface_count": len(
            future_materialized_attach_eligible_interfaces
        ),
        "future_non_materialized_capacity_only_interface_count": len(
            future_non_materialized_capacity_only_interfaces
        ),
        "compile_capacity_interface_count": len(compile_capacity_interfaces),
        "attached_interface_count": len(attach_plan),
        "merged_interface_count": len(attach_plan),
        "added_interface_count": len(added_interfaces),
        "added_interfaces": added_interfaces,
        "future_horizon_interfaces": future_horizon_interfaces,
        "future_materialized_attach_eligible_interfaces": (
            future_materialized_attach_eligible_interfaces
        ),
        "future_non_materialized_capacity_only_interfaces": (
            future_non_materialized_capacity_only_interfaces
        ),
        "resident_attach_eligible_interfaces": resident_attach_eligible_interfaces,
        "compile_capacity_interfaces": compile_capacity_interfaces,
        "future_interface_classification": (
            "FUTURE_MATERIALIZED_INTERFACES_PREATTACHED"
            if future_materialized_attach_eligible_interfaces
            else (
                "FUTURE_INTERFACE_NOT_MATERIALIZED"
                if future_horizon_interfaces
                else ""
            )
        ),
        "future_capacity_only_classification": (
            "FUTURE_INTERFACE_NOT_MATERIALIZED"
            if future_non_materialized_capacity_only_interfaces
            else ""
        ),
        "readiness_blocking_future_interface_count": 0,
        "interface_classifications": sorted(
            interface_classifications,
            key=lambda item: str(item.get("interface_ref") or ""),
        ),
        "outcome_labels": [
            "RESIDENT_ATTACH_COVERAGE_CONTRACT_ADDED",
            *(
                ["FUTURE_MATERIALIZED_INTERFACES_PREATTACHED"]
                if future_materialized_attach_eligible_interfaces
                else []
            ),
        ],
        "policy": (
            "active/materialized interfaces are live-bound for the current "
            "generation; resident future horizon interfaces that are already "
            "materialized are preattached for steady map-only reuse; non-"
            "materialized future interfaces are retained for capacity and "
            "coverage evidence but excluded from current readiness"
        ),
    }
    return merged


def _bpf_owned_reused_attach_clsact_guard(
    *,
    attach_reuse: Dict[str, Any],
    verify_clsact,
) -> Dict[str, Any]:
    guarded_reuse = dict(attach_reuse)
    if guarded_reuse.get("reuse_eligible") is not True:
        return {
            "ok": True,
            "skipped": True,
            "reason": "attach_reuse_not_eligible",
            "attach_reuse": guarded_reuse,
            "checked_interfaces": [],
            "results": [],
            "metrics": _bpf_clsact_lifecycle_metrics([]),
        }
    required_plan = {
        str(interface_ref): list(directions or [])
        for interface_ref, directions in dict(
            guarded_reuse.get("required_attach_plan") or {}
        ).items()
        if str(interface_ref)
    }
    delta_attach_plan = {
        str(interface_ref): list(directions or [])
        for interface_ref, directions in dict(
            guarded_reuse.get("delta_attach_plan") or {}
        ).items()
        if str(interface_ref)
    }
    reused_interfaces = sorted(
        interface_ref
        for interface_ref in required_plan
        if interface_ref not in delta_attach_plan
    )
    results: List[Dict[str, Any]] = []
    for interface_ref in reused_interfaces:
        results.extend(list(verify_clsact(interface_ref) or []))
    metrics = _bpf_clsact_lifecycle_metrics(results)
    missing_interfaces: List[str] = []
    for result in results:
        if str(result.get("clsact_lifecycle_event") or "") != "missing_on_expected_reuse":
            continue
        interface_ref = result.get("interface_ref")
        if isinstance(interface_ref, dict):
            raw_ref = str(
                interface_ref.get("interface_ref")
                or interface_ref.get("raw")
                or interface_ref.get("interface_name")
                or ""
            )
        else:
            raw_ref = str(interface_ref or "")
        if raw_ref:
            missing_interfaces.append(raw_ref)
    missing_interfaces = sorted(set(missing_interfaces))
    if missing_interfaces:
        guarded_reuse.update(
            {
                "reuse_eligible": False,
                "reason": "clsact_missing_on_expected_reuse",
                "reuse_rejected_reason": "clsact_missing_on_expected_reuse",
                "attach_strategy": "full_attach_required",
                "reused_attach_count": 0,
                "delta_attach_count": 0,
                "full_attach_count": len(required_plan),
                "clsact_missing_on_expected_reuse_interfaces": missing_interfaces,
            }
        )
    return {
        "ok": not missing_interfaces,
        "skipped": False,
        "reason": "" if not missing_interfaces else "clsact_missing_on_expected_reuse",
        "attach_reuse": guarded_reuse,
        "checked_interfaces": reused_interfaces,
        "missing_interfaces": missing_interfaces,
        "results": results,
        "metrics": metrics,
    }


def _bpf_mpls_values_equal(
    first: Dict[str, Any],
    second: Dict[str, Any],
) -> bool:
    return dict(first.get("value") or {}) == dict(second.get("value") or {})


def _bpf_mpls_conflict_type(
    map_name: str,
    first: Dict[str, Any],
    second: Dict[str, Any],
) -> str:
    first_value = dict(first.get("value") or {})
    second_value = dict(second.get("value") or {})
    if first_value == second_value:
        return "exact_duplicate_same_value" if first == second else "safe_aggregation"
    if map_name == "core_lfib_map":
        if int(first_value.get("out_ifindex") or 0) != int(
            second_value.get("out_ifindex") or 0
        ):
            return "unsafe_different_out_ifindex"
        if int(first_value.get("out_label") or 0) != int(
            second_value.get("out_label") or 0
        ):
            return "unsafe_different_next_label"
        return "unsafe_different_lfib_action"
    if map_name == "egress_handoff_map":
        return "unsafe_different_handoff_action"
    if map_name == "ingress_classifier_map":
        if int(first_value.get("initial_label") or 0) != int(
            second_value.get("initial_label") or 0
        ):
            return "label_allocation_collision"
        return "unsafe_different_classifier_action"
    return "unsafe_duplicate_key"


def _bpf_mpls_conflict_record(
    *,
    map_name: str,
    key: Dict[str, Any],
    first: Dict[str, Any],
    second: Dict[str, Any],
    conflict_type: str,
) -> Dict[str, Any]:
    first_meta = dict(first.get("metadata") or {})
    second_meta = dict(second.get("metadata") or {})
    return {
        "map_name": map_name,
        "map_type": map_name,
        "generation": int(key.get("generation") or 0),
        "key": {
            "generation": int(key.get("generation") or 0),
            "ingress_ifindex": int(key.get("ingress_ifindex") or 0),
            "logical_ingress": key.get("logical_ingress")
            or first_meta.get("ingress_interface")
            or second_meta.get("ingress_interface"),
            "label": key.get("label"),
            "fec_label": key.get("label")
            or dict(first.get("value") or {}).get("initial_label"),
            "direction": key.get("direction"),
            "edge_role": first_meta.get("edge_role")
            or first_meta.get("handoff")
            or second_meta.get("edge_role")
            or second_meta.get("handoff"),
        },
        "first_value": dict(first.get("value") or {}),
        "conflicting_value": dict(second.get("value") or {}),
        "first_context": {
            "fec_id": first_meta.get("fec_id"),
            "zone_id": first_meta.get("zone_id"),
            "gw_id": first_meta.get("selected_gateway_id"),
            "ut_id": first_meta.get("ut_id"),
            "flow_id": first_meta.get("flow_id"),
            "path_node_sequence": first_meta.get("path_node_sequence"),
            "hop_index": first_meta.get("hop_index"),
        },
        "conflicting_context": {
            "fec_id": second_meta.get("fec_id"),
            "zone_id": second_meta.get("zone_id"),
            "gw_id": second_meta.get("selected_gateway_id"),
            "ut_id": second_meta.get("ut_id"),
            "flow_id": second_meta.get("flow_id"),
            "path_node_sequence": second_meta.get("path_node_sequence"),
            "hop_index": second_meta.get("hop_index"),
        },
        "fec_id": first_meta.get("fec_id") or second_meta.get("fec_id"),
        "zone_id": first_meta.get("zone_id") or second_meta.get("zone_id"),
        "gw_id": first_meta.get("selected_gateway_id")
        or second_meta.get("selected_gateway_id"),
        "ut_or_flow_context": {
            "first_ut_id": first_meta.get("ut_id"),
            "first_flow_id": first_meta.get("flow_id"),
            "conflicting_ut_id": second_meta.get("ut_id"),
            "conflicting_flow_id": second_meta.get("flow_id"),
        },
        "path_node_sequence": first_meta.get("path_node_sequence")
        or second_meta.get("path_node_sequence"),
        "hop_index": first_meta.get("hop_index")
        if first_meta.get("hop_index") is not None
        else second_meta.get("hop_index"),
        "conflict_type": conflict_type,
        "unsafe": not conflict_type.startswith("safe_")
        and conflict_type != "exact_duplicate_same_value",
    }


def _bpf_owned_compile_mpls_maps_for_contexts(
    *,
    contexts: Sequence[DirectGatewayExperimentContext],
    generation_id: int,
    offline: bool = False,
) -> Dict[str, Any]:
    from satellite.bpf_mpls_live import (
        ETH_P_IP,
        G_TO_Z,
        Z_TO_G,
        ipv4_little_endian_u32,
        stable_u32,
    )
    if not offline:
        from satellite.bpf_mpls_live import interface_ifindex, interface_mac

    def ref(node_id: str, interface_name: str) -> str:
        return _bpf_owned_netns_interface_ref(
            node_id=str(node_id),
            interface_name=str(interface_name),
        )

    ifindex_cache: Dict[str, int] = {}
    mac_cache: Dict[str, str] = {}

    def ifindex(interface_ref: str) -> int:
        if interface_ref not in ifindex_cache:
            ifindex_cache[interface_ref] = (
                _bpf_mpls_synthetic_ifindex(interface_ref)
                if offline
                else int(interface_ifindex(interface_ref))
            )
        return ifindex_cache[interface_ref]

    def mac(interface_ref: str) -> str:
        if interface_ref not in mac_cache:
            mac_cache[interface_ref] = (
                _bpf_mpls_synthetic_mac(interface_ref)
                if offline
                else str(interface_mac(interface_ref))
            )
        return mac_cache[interface_ref]

    def endpoint_ref(node_id: str, interface_name: str) -> str:
        return ref(str(node_id), str(interface_name))

    def rewrite_to_segment_destination(segment: MplsPathSegment) -> Dict[str, str]:
        return {
            "dst_mac": mac(
                endpoint_ref(segment.destination_node_id, segment.destination_interface)
            ),
            "src_mac": mac(endpoint_ref(segment.source_node_id, segment.source_interface)),
        }

    def rewrite_between(
        *,
        dst_node_id: str,
        dst_interface: str,
        src_node_id: str,
        src_interface: str,
    ) -> Dict[str, str]:
        return {
            "dst_mac": mac(endpoint_ref(dst_node_id, dst_interface)),
            "src_mac": mac(endpoint_ref(src_node_id, src_interface)),
        }

    ingress_classifier_map: List[Dict[str, Any]] = []
    core_lfib_map: List[Dict[str, Any]] = []
    egress_handoff_map: List[Dict[str, Any]] = []
    resolved_flow_entries: List[Dict[str, Any]] = []
    attach_roles: Dict[str, set[str]] = {}
    map_conflicts: List[Dict[str, Any]] = []
    safe_duplicate_count = 0
    seen_classifier: Dict[Tuple[int, int, int, int], int] = {}
    seen_core: Dict[Tuple[int, int, int, str], int] = {}
    seen_handoff: Dict[Tuple[int, int, int, str], int] = {}
    label_by_key, label_allocation_table = _bpf_mpls_label_allocations(
        contexts=contexts,
        generation_id=generation_id,
        g_to_z_direction=G_TO_Z,
        z_to_g_direction=Z_TO_G,
        stable_u32_fn=stable_u32,
    )

    def allocated_label(
        direction: str,
        fec_id: str,
        hop_index: int,
        context: DirectGatewayExperimentContext,
    ) -> int:
        discriminator = _bpf_mpls_label_allocation_discriminator(
            direction=direction,
            context=context,
        )
        return int(
            label_by_key[
                (
                    str(direction),
                    str(fec_id),
                    int(hop_index),
                    str(discriminator),
                )
            ]
        )

    def register_duplicate(
        *,
        map_name: str,
        key: Dict[str, Any],
        first: Dict[str, Any],
        second: Dict[str, Any],
    ) -> None:
        nonlocal safe_duplicate_count
        conflict_type = _bpf_mpls_conflict_type(map_name, first, second)
        if conflict_type.startswith("safe_") or conflict_type == "exact_duplicate_same_value":
            safe_duplicate_count += 1
            return
        map_conflicts.append(
            _bpf_mpls_conflict_record(
                map_name=map_name,
                key=key,
                first=first,
                second=second,
                conflict_type=conflict_type,
            )
        )

    def add_attach(interface_ref: str, direction: str, role: str) -> None:
        if not interface_ref:
            return
        attach_roles.setdefault(interface_ref, set()).add(str(direction))
        resolved_flow_entries.append(
            {
                "map_type": "tc_attach",
                "interface_ref": interface_ref,
                "ifindex": ifindex(interface_ref),
                "attach_direction": direction,
                "role": role,
            }
        )

    def add_classifier(entry: Dict[str, Any]) -> None:
        key = dict(entry.get("key") or {})
        map_key = (
            int(key.get("generation") or 0),
            int(key.get("ingress_ifindex") or 0),
            int(key.get("dst_ipv4") or 0),
            int(key.get("service_class_u32") or 0),
        )
        existing_index = seen_classifier.get(map_key)
        if existing_index is None:
            seen_classifier[map_key] = len(ingress_classifier_map)
            ingress_classifier_map.append(entry)
            return
        register_duplicate(
            map_name="ingress_classifier_map",
            key=key,
            first=ingress_classifier_map[existing_index],
            second=entry,
        )

    def add_core(entry: Dict[str, Any]) -> None:
        key = dict(entry.get("key") or {})
        map_key = (
            int(key.get("generation") or 0),
            int(key.get("ingress_ifindex") or 0),
            int(key.get("label") or 0),
            str(key.get("direction") or ""),
        )
        existing_index = seen_core.get(map_key)
        if existing_index is None:
            seen_core[map_key] = len(core_lfib_map)
            core_lfib_map.append(entry)
            return
        register_duplicate(
            map_name="core_lfib_map",
            key=key,
            first=core_lfib_map[existing_index],
            second=entry,
        )

    def add_handoff(entry: Dict[str, Any]) -> None:
        key = dict(entry.get("key") or {})
        map_key = (
            int(key.get("generation") or 0),
            int(key.get("ingress_ifindex") or 0),
            int(key.get("label") or 0),
            str(key.get("direction") or ""),
        )
        existing_index = seen_handoff.get(map_key)
        if existing_index is None:
            seen_handoff[map_key] = len(egress_handoff_map)
            egress_handoff_map.append(entry)
            return
        register_duplicate(
            map_name="egress_handoff_map",
            key=key,
            first=egress_handoff_map[existing_index],
            second=entry,
        )

    for context in contexts:
        if not context.forward_segments:
            continue
        fec_id, reverse_fec_id = _bpf_mpls_context_fec_ids(context, stable_u32)
        service_class = 0
        first_forward = context.forward_segments[0]
        forward_path_node_sequence = _bpf_mpls_path_node_sequence(
            context.forward_segments
        )
        reverse_path_node_sequence = _bpf_mpls_path_node_sequence(
            context.reverse_segments
        )
        ut_ingress_ref = endpoint_ref(
            context.serving_satellite_id,
            context.ut_access_interface,
        )
        first_forward_out_ref = endpoint_ref(
            first_forward.source_node_id,
            first_forward.source_interface,
        )
        first_forward_label = allocated_label(G_TO_Z, fec_id, 0, context)
        add_attach(ut_ingress_ref, "ingress", "g_to_z_classifier")
        for target_ip in _bpf_owned_forward_targets_for_context(context):
            add_classifier(
                {
                    "key": {
                        "generation": int(generation_id),
                        "ingress_ifindex": ifindex(ut_ingress_ref),
                        "dst_ipv4": ipv4_little_endian_u32(target_ip),
                        "service_class_u32": service_class,
                    },
                    "value": {
                        "active": True,
                        "fec_id": fec_id,
                        "fec_id_u32": stable_u32(fec_id),
                        "direction": G_TO_Z,
                        "initial_label": int(first_forward_label),
                        "out_ifindex": ifindex(first_forward_out_ref),
                        "inner_ethertype": ETH_P_IP,
                        **rewrite_to_segment_destination(first_forward),
                    },
                    "metadata": {
                        "fec_id": fec_id,
                        "flow_id": context.flow_id,
                        "ut_id": context.ut_id,
                        "zone_id": context.zone_id,
                        "selected_gateway_id": context.selected_gateway_id,
                        "target_ip": target_ip,
                        "target_host_node_id": context.target_host_node_id,
                        "traffic_scope": context.traffic_scope,
                        "ingress_interface": ut_ingress_ref,
                        "edge_role": "g_to_z_classifier",
                        "path_node_sequence": forward_path_node_sequence,
                        "hop_index": 0,
                        "original_label": int(first_forward.label),
                        "allocated_label": int(first_forward_label),
                    },
                }
            )
            resolved_flow_entries.append(
                {
                    "map_type": "ingress_classifier_map",
                    "flow_id": context.flow_id,
                    "ut_id": context.ut_id,
                    "target_ip": target_ip,
                    "direction": G_TO_Z,
                    "generation": int(generation_id),
                    "ingress_interface": ut_ingress_ref,
                    "ingress_ifindex": ifindex(ut_ingress_ref),
                    "initial_label": int(first_forward_label),
                    "original_label": int(first_forward.label),
                    "fec_id": fec_id,
                    "out_interface": first_forward_out_ref,
                    "out_ifindex": ifindex(first_forward_out_ref),
                }
            )

        for index, segment in enumerate(context.forward_segments[:-1]):
            next_segment = context.forward_segments[index + 1]
            ingress_ref = endpoint_ref(
                segment.destination_node_id,
                segment.destination_interface,
            )
            out_ref = endpoint_ref(
                next_segment.source_node_id,
                next_segment.source_interface,
            )
            in_label = allocated_label(G_TO_Z, fec_id, index, context)
            out_label = allocated_label(G_TO_Z, fec_id, index + 1, context)
            add_attach(ingress_ref, "ingress", "g_to_z_core_lfib")
            add_core(
                {
                    "key": {
                        "generation": int(generation_id),
                        "ingress_ifindex": ifindex(ingress_ref),
                        "label": int(in_label),
                        "direction": G_TO_Z,
                        "logical_ingress": ingress_ref,
                    },
                    "value": {
                        "active": True,
                        "action": (
                            "swap"
                            if int(out_label) != int(in_label)
                            else "preserve"
                        ),
                        "out_label": int(out_label),
                        "out_ifindex": ifindex(out_ref),
                        **rewrite_to_segment_destination(next_segment),
                    },
                    "metadata": {
                        "fec_id": fec_id,
                        "flow_id": context.flow_id,
                        "ut_id": context.ut_id,
                        "zone_id": context.zone_id,
                        "selected_gateway_id": context.selected_gateway_id,
                        "segment_link_id": segment.link_id,
                        "next_segment_link_id": next_segment.link_id,
                        "ingress_interface": ingress_ref,
                        "path_node_sequence": forward_path_node_sequence,
                        "hop_index": index,
                        "original_label": int(segment.label),
                        "allocated_label": int(in_label),
                        "next_original_label": int(next_segment.label),
                        "next_allocated_label": int(out_label),
                    },
                }
            )
            resolved_flow_entries.append(
                {
                    "map_type": "core_lfib_map",
                    "flow_id": context.flow_id,
                    "ut_id": context.ut_id,
                    "direction": G_TO_Z,
                    "generation": int(generation_id),
                    "ingress_interface": ingress_ref,
                    "ingress_ifindex": ifindex(ingress_ref),
                    "label": int(in_label),
                    "original_label": int(segment.label),
                    "fec_id": fec_id,
                    "action": "swap"
                    if int(out_label) != int(in_label)
                    else "preserve",
                    "out_label": int(out_label),
                    "next_original_label": int(next_segment.label),
                    "out_interface": out_ref,
                    "out_ifindex": ifindex(out_ref),
                    "uses_dst_ip": False,
                }
            )

        final_forward = context.forward_segments[-1]
        forward_handoff_ref = endpoint_ref(
            final_forward.source_node_id,
            final_forward.source_interface,
        )
        final_forward_label = allocated_label(
            G_TO_Z,
            fec_id,
            len(context.forward_segments) - 1,
            context,
        )
        add_attach(forward_handoff_ref, "egress", "g_to_z_egress_handoff")
        add_handoff(
            {
                "key": {
                    "generation": int(generation_id),
                    "ingress_ifindex": ifindex(forward_handoff_ref),
                    "label": int(final_forward_label),
                    "direction": G_TO_Z,
                    "logical_ingress": forward_handoff_ref,
                },
                "value": {
                    "active": True,
                    "out_ifindex": 0,
                    "inner_ethertype": ETH_P_IP,
                    **rewrite_to_segment_destination(final_forward),
                },
                "metadata": {
                    "fec_id": fec_id,
                    "flow_id": context.flow_id,
                    "ut_id": context.ut_id,
                    "zone_id": context.zone_id,
                    "selected_gateway_id": context.selected_gateway_id,
                    "handoff": "gateway_or_mini_internet",
                    "ingress_interface": forward_handoff_ref,
                    "edge_role": "g_to_z_egress_handoff",
                    "path_node_sequence": forward_path_node_sequence,
                    "hop_index": len(context.forward_segments) - 1,
                    "original_label": int(final_forward.label),
                    "allocated_label": int(final_forward_label),
                },
            }
        )
        resolved_flow_entries.append(
            {
                "map_type": "egress_handoff_map",
                "flow_id": context.flow_id,
                "ut_id": context.ut_id,
                "direction": G_TO_Z,
                "generation": int(generation_id),
                "ingress_interface": forward_handoff_ref,
                "ingress_ifindex": ifindex(forward_handoff_ref),
                "label": int(final_forward_label),
                "original_label": int(final_forward.label),
                "fec_id": fec_id,
                "handoff": "gateway_or_mini_internet",
                "uses_dst_ip": False,
            }
        )

        if context.reverse_segments and context.gateway_nat_interface:
            first_reverse = context.reverse_segments[0]
            nat_ingress_ref = endpoint_ref(
                context.selected_gateway_id,
                context.gateway_nat_interface,
            )
            first_reverse_out_ref = endpoint_ref(
                first_reverse.source_node_id,
                first_reverse.source_interface,
            )
            first_reverse_label = allocated_label(Z_TO_G, reverse_fec_id, 0, context)
            add_attach(nat_ingress_ref, "ingress", "z_to_g_classifier")
            for return_target_ip in sorted(
                {
                    str(context.gateway_nat_source_ip),
                    str(context.ut_access_ip),
                }
            ):
                if not return_target_ip:
                    continue
                add_classifier(
                    {
                        "key": {
                            "generation": int(generation_id),
                            "ingress_ifindex": ifindex(nat_ingress_ref),
                            "dst_ipv4": ipv4_little_endian_u32(return_target_ip),
                            "service_class_u32": service_class,
                        },
                        "value": {
                            "active": True,
                            "fec_id": reverse_fec_id,
                            "fec_id_u32": stable_u32(reverse_fec_id),
                            "direction": Z_TO_G,
                            "initial_label": int(first_reverse_label),
                            "out_ifindex": ifindex(first_reverse_out_ref),
                            "inner_ethertype": ETH_P_IP,
                            **rewrite_to_segment_destination(first_reverse),
                        },
                        "metadata": {
                            "fec_id": reverse_fec_id,
                            "flow_id": context.flow_id,
                            "ut_id": context.ut_id,
                            "zone_id": context.zone_id,
                            "selected_gateway_id": context.selected_gateway_id,
                            "target_ip": return_target_ip,
                            "traffic_scope": context.traffic_scope,
                            "ingress_interface": nat_ingress_ref,
                            "edge_role": "z_to_g_classifier",
                            "path_node_sequence": reverse_path_node_sequence,
                            "hop_index": 0,
                            "original_label": int(first_reverse.label),
                            "allocated_label": int(first_reverse_label),
                        },
                    }
                )
                resolved_flow_entries.append(
                    {
                        "map_type": "ingress_classifier_map",
                        "flow_id": context.flow_id,
                        "ut_id": context.ut_id,
                        "target_ip": return_target_ip,
                        "direction": Z_TO_G,
                        "generation": int(generation_id),
                        "ingress_interface": nat_ingress_ref,
                        "ingress_ifindex": ifindex(nat_ingress_ref),
                        "initial_label": int(first_reverse_label),
                        "original_label": int(first_reverse.label),
                        "fec_id": reverse_fec_id,
                        "out_interface": first_reverse_out_ref,
                        "out_ifindex": ifindex(first_reverse_out_ref),
                    }
                )

            for index, segment in enumerate(context.reverse_segments[:-1]):
                next_segment = context.reverse_segments[index + 1]
                ingress_ref = endpoint_ref(
                    segment.destination_node_id,
                    segment.destination_interface,
                )
                out_ref = endpoint_ref(
                    next_segment.source_node_id,
                    next_segment.source_interface,
                )
                in_label = allocated_label(Z_TO_G, reverse_fec_id, index, context)
                out_label = allocated_label(
                    Z_TO_G,
                    reverse_fec_id,
                    index + 1,
                    context,
                )
                add_attach(ingress_ref, "ingress", "z_to_g_core_lfib")
                add_core(
                    {
                        "key": {
                            "generation": int(generation_id),
                            "ingress_ifindex": ifindex(ingress_ref),
                            "label": int(in_label),
                            "direction": Z_TO_G,
                            "logical_ingress": ingress_ref,
                        },
                        "value": {
                            "active": True,
                            "action": (
                                "swap"
                                if int(out_label) != int(in_label)
                                else "preserve"
                            ),
                            "out_label": int(out_label),
                            "out_ifindex": ifindex(out_ref),
                            **rewrite_to_segment_destination(next_segment),
                        },
                        "metadata": {
                            "fec_id": reverse_fec_id,
                            "flow_id": context.flow_id,
                            "ut_id": context.ut_id,
                            "zone_id": context.zone_id,
                            "selected_gateway_id": context.selected_gateway_id,
                            "segment_link_id": segment.link_id,
                            "next_segment_link_id": next_segment.link_id,
                            "ingress_interface": ingress_ref,
                            "path_node_sequence": reverse_path_node_sequence,
                            "hop_index": index,
                            "original_label": int(segment.label),
                            "allocated_label": int(in_label),
                            "next_original_label": int(next_segment.label),
                            "next_allocated_label": int(out_label),
                        },
                    }
                )
                resolved_flow_entries.append(
                    {
                        "map_type": "core_lfib_map",
                        "flow_id": context.flow_id,
                        "ut_id": context.ut_id,
                        "direction": Z_TO_G,
                        "generation": int(generation_id),
                        "ingress_interface": ingress_ref,
                        "ingress_ifindex": ifindex(ingress_ref),
                        "label": int(in_label),
                        "original_label": int(segment.label),
                        "fec_id": reverse_fec_id,
                        "action": "swap"
                        if int(out_label) != int(in_label)
                        else "preserve",
                        "out_label": int(out_label),
                        "next_original_label": int(next_segment.label),
                        "out_interface": out_ref,
                        "out_ifindex": ifindex(out_ref),
                        "uses_dst_ip": False,
                    }
                )

            final_reverse = context.reverse_segments[-1]
            final_reverse_ingress_ref = endpoint_ref(
                final_reverse.destination_node_id,
                final_reverse.destination_interface,
            )
            ut_delivery_ref = endpoint_ref(
                context.serving_satellite_id,
                context.ut_access_interface,
            )
            final_reverse_label = allocated_label(
                Z_TO_G,
                reverse_fec_id,
                len(context.reverse_segments) - 1,
                context,
            )
            add_attach(final_reverse_ingress_ref, "ingress", "z_to_g_zone_handoff")
            add_handoff(
                {
                    "key": {
                        "generation": int(generation_id),
                        "ingress_ifindex": ifindex(final_reverse_ingress_ref),
                        "label": int(final_reverse_label),
                        "direction": Z_TO_G,
                        "logical_ingress": final_reverse_ingress_ref,
                    },
                    "value": {
                        "active": True,
                        "out_ifindex": ifindex(ut_delivery_ref),
                        "inner_ethertype": ETH_P_IP,
                        "new_dst_ip": str(context.ut_access_ip),
                        "handoff": "zone_local_delivery",
                        **rewrite_between(
                            dst_node_id=str(context.ut_id),
                            dst_interface=str(context.ut_access_interface),
                            src_node_id=str(context.serving_satellite_id),
                            src_interface=str(context.ut_access_interface),
                        ),
                    },
                    "metadata": {
                        "fec_id": reverse_fec_id,
                        "flow_id": context.flow_id,
                        "ut_id": context.ut_id,
                        "zone_id": context.zone_id,
                        "selected_gateway_id": context.selected_gateway_id,
                        "handoff": "zone_local_delivery",
                        "ingress_interface": final_reverse_ingress_ref,
                        "edge_role": "z_to_g_zone_handoff",
                        "path_node_sequence": reverse_path_node_sequence,
                        "hop_index": len(context.reverse_segments) - 1,
                        "original_label": int(final_reverse.label),
                        "allocated_label": int(final_reverse_label),
                        "out_interface": ut_delivery_ref,
                        "out_ifindex": ifindex(ut_delivery_ref),
                    },
                }
            )
            resolved_flow_entries.append(
                {
                    "map_type": "egress_handoff_map",
                    "flow_id": context.flow_id,
                    "ut_id": context.ut_id,
                    "direction": Z_TO_G,
                    "generation": int(generation_id),
                    "ingress_interface": final_reverse_ingress_ref,
                    "ingress_ifindex": ifindex(final_reverse_ingress_ref),
                    "label": int(final_reverse_label),
                    "original_label": int(final_reverse.label),
                    "fec_id": reverse_fec_id,
                    "handoff": "zone_local_delivery",
                    "out_interface": ut_delivery_ref,
                    "out_ifindex": ifindex(ut_delivery_ref),
                    "dst_interface": endpoint_ref(
                        str(context.ut_id),
                        str(context.ut_access_interface),
                    ),
                    "new_dst_ip": str(context.ut_access_ip),
                    "uses_dst_ip": False,
                }
            )

    attach_plan = {
        interface_ref: sorted(directions)
        for interface_ref, directions in sorted(attach_roles.items())
    }
    attached_interface_details = [
        {
            "interface_name": interface_ref,
            "interface_ref": interface_ref,
            "container_interface_name": interface_ref.rsplit(":", 1)[-1],
            "ifindex": ifindex(interface_ref),
            "netns": interface_ref.split(":", 2)[1]
            if interface_ref.startswith("netns:")
            else "",
            "attach_directions": list(directions),
            "expected_roles": sorted(
                str(entry.get("role") or "")
                for entry in resolved_flow_entries
                if entry.get("map_type") == "tc_attach"
                and entry.get("interface_ref") == interface_ref
            ),
            "ifindex_error": "",
        }
        for interface_ref, directions in attach_plan.items()
    ]
    map_counts = {
        "ingress_classifier_map": len(ingress_classifier_map),
        "core_lfib_map": len(core_lfib_map),
        "egress_handoff_map": len(egress_handoff_map),
        "active_generation": 1,
    }
    return {
        "schema_version": "skyseed-runtime-bpf-owned-mpls-maps-v1",
        "active_generation": {"value": int(generation_id)},
        "ingress_classifier_map": ingress_classifier_map,
        "core_lfib_map": core_lfib_map,
        "egress_handoff_map": egress_handoff_map,
        "attach_plan": attach_plan,
        "attached_interfaces": list(attach_plan.keys()),
        "attached_interface_details": attached_interface_details,
        "resolved_mpls_flow_entries": [
            entry for entry in resolved_flow_entries if entry.get("map_type") != "tc_attach"
        ],
        "compiled_mpls_map_counts": map_counts,
        "map_conflicts": map_conflicts,
        "map_conflict_summary": {
            "safe_duplicate_count": int(safe_duplicate_count),
            "unsafe_conflict_count": len(map_conflicts),
            "conflict_count": len(map_conflicts),
        },
        "label_allocation": {
            "mode": "deterministic_bpf_fec_hop_labels",
            "base_label": BPF_MPLS_LABEL_BASE,
            "max_label": BPF_MPLS_LABEL_MAX,
            "entry_count": len(label_allocation_table),
            "entries": label_allocation_table,
        },
        "intermediate_core_lookup_uses_dst_ip": False,
    }


def _bpf_owned_compiled_map_from_proof(proof: Dict[str, Any]) -> Dict[str, Any]:
    entries = []
    for entry in [
        *list(proof.get("g_to_z_entries", []) or []),
        *list(proof.get("z_to_g_entries", []) or []),
    ]:
        fec_id = str(entry.get("fec_id") or "")
        if not fec_id:
            continue
        entries.append(
            {
                **dict(entry),
                "active": True,
                "fec_key_u32": _bpf_owned_stable_u32(fec_id),
            }
        )
    return {
        "bpf_maps": {
            "runtime": {
                "active_generation": {
                    "slot": 0,
                    "value": int(proof.get("generation_id") or 0),
                },
                "generation_status": [
                    {
                        "generation_id": int(proof.get("generation_id") or 0),
                        "installed": True,
                        "active": True,
                    }
                ],
                "fec_forwarding": entries,
            },
            "parameter": {
                "parameter_generation": {"slot": 0, "value": 0},
                "param_status": [],
                "parameter_values": [],
            },
        }
    }


def _bpf_owned_prepare_real_backend(
    *,
    proof: Dict[str, Any],
    attached_interfaces: Sequence[str],
    dataplane_entries: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    try:
        from satellite.bpf_dataplane_canary import (
            DEFAULT_SOURCE,
            SELECTOR_MODE_FULL_ELIGIBLE,
            attach_blockers,
            attach_canary,
            bpf_map_capacities_for_scope,
            collect_environment,
            command_ok,
            compile_bpf,
            interface_ifindex,
            interface_ref,
            load_canary_maps,
            rollback_canary,
        )
    except Exception as exc:  # pragma: no cover - import posture is environment-specific.
        return {
            "ok": False,
            "reason": "bpf_prepare_import_failed",
            "error": str(exc),
            "attached_interfaces": list(attached_interfaces),
        }

    started_at = time.perf_counter()
    compiled = _bpf_owned_compiled_map_from_proof(proof)

    def container_interface_mac(node_id: str, interface_name: str) -> str:
        result = subprocess.run(
            [
                "docker",
                "exec",
                str(node_id),
                "cat",
                f"/sys/class/net/{interface_name}/address",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=4.0,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"could not read MAC for {node_id}:{interface_name}: "
                f"{result.stderr.strip()}"
            )
        return result.stdout.strip()

    def docker_host_peer_interface(node_id: str, interface_name: str) -> str:
        iflink_result = subprocess.run(
            [
                "docker",
                "exec",
                str(node_id),
                "cat",
                f"/sys/class/net/{interface_name}/iflink",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=4.0,
        )
        if iflink_result.returncode != 0:
            raise RuntimeError(
                f"could not read iflink for {node_id}:{interface_name}: "
                f"{iflink_result.stderr.strip()}"
            )
        iflink = int(iflink_result.stdout.strip())
        link_result = subprocess.run(
            ["ip", "-o", "link", "show"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=4.0,
        )
        if link_result.returncode != 0:
            raise RuntimeError(
                f"could not list host links for {node_id}:{interface_name}: "
                f"{link_result.stderr.strip()}"
            )
        prefix = f"{iflink}: "
        for line in link_result.stdout.splitlines():
            if not line.startswith(prefix):
                continue
            name = line.split(":", 2)[1].strip().split("@", 1)[0]
            if name:
                return name
        raise RuntimeError(
            f"could not find host peer for {node_id}:{interface_name} iflink {iflink}"
        )

    def resolve_interface_marker(interface_name: str) -> str:
        if not interface_name.startswith("docker-peer:"):
            return interface_name
        _prefix, node_id, container_interface = interface_name.split(":", 2)
        return docker_host_peer_interface(node_id, container_interface)

    resolved_dataplane_entries: List[Dict[str, Any]] = []
    for entry in dataplane_entries:
        ingress_interface = resolve_interface_marker(
            str(entry.get("ingress_interface") or "")
        )
        egress_interface = resolve_interface_marker(
            str(entry.get("egress_interface") or "")
        )
        if not ingress_interface or not egress_interface:
            continue
        segment = dict(entry.get("segment") or {})
        l2_rewrite = dict(entry.get("l2_rewrite") or {})
        dst_mac_node_id = str(
            l2_rewrite.get("destination_node_id")
            or segment.get("destination_node_id")
            or ""
        )
        dst_mac_interface = str(
            l2_rewrite.get("destination_interface")
            or segment.get("destination_interface")
            or ""
        )
        src_mac_node_id = str(
            l2_rewrite.get("source_node_id")
            or segment.get("source_node_id")
            or ""
        )
        src_mac_interface = str(
            l2_rewrite.get("source_interface")
            or segment.get("source_interface")
            or ""
        )
        try:
            resolved_dataplane_entries.append(
                {
                    **dict(entry),
                    "ingress_interface": ingress_interface,
                    "egress_interface": egress_interface,
                    "ut_host_interface": ingress_interface,
                    "satellite_host_interface": egress_interface,
                    "ingress_ifindex": interface_ifindex(ingress_interface),
                    "egress_ifindex": interface_ifindex(egress_interface),
                    "satellite_ifindex": interface_ifindex(egress_interface),
                    "dst_mac": container_interface_mac(
                        dst_mac_node_id,
                        dst_mac_interface,
                    ),
                    "src_mac": container_interface_mac(
                        src_mac_node_id,
                        src_mac_interface,
                    ),
                    "l2_rewrite": {
                        "source_node_id": src_mac_node_id,
                        "source_interface": src_mac_interface,
                        "destination_node_id": dst_mac_node_id,
                        "destination_interface": dst_mac_interface,
                    },
                }
            )
        except Exception as exc:
            return {
                "ok": False,
                "reason": "bpf_prepare_ifindex_resolution_failed",
                "error": str(exc),
                "attached_interfaces": list(attached_interfaces),
                "dataplane_entry_count": len(list(dataplane_entries)),
                "resolved_dataplane_entry_count": len(resolved_dataplane_entries),
            }
    effective_attached_interfaces = sorted(
        {
            *[str(interface_name) for interface_name in attached_interfaces],
            *[
                str(entry.get("ingress_interface") or "")
                for entry in resolved_dataplane_entries
                if str(entry.get("ingress_interface") or "")
            ],
            *[
                str(entry.get("egress_interface") or "")
                for entry in resolved_dataplane_entries
                if str(entry.get("egress_interface") or "")
            ],
        }
    )
    attached_interface_roles: Dict[str, set[str]] = {
        str(interface_name): set() for interface_name in effective_attached_interfaces
    }
    for entry in resolved_dataplane_entries:
        ingress = str(entry.get("ingress_interface") or "")
        egress = str(entry.get("egress_interface") or "")
        direction = str(entry.get("direction") or "unknown")
        if ingress:
            attached_interface_roles.setdefault(ingress, set()).add(
                f"{direction}:ingress"
            )
        if egress:
            attached_interface_roles.setdefault(egress, set()).add(
                f"{direction}:egress"
            )
    attached_interface_details: List[Dict[str, Any]] = []
    for interface_name in effective_attached_interfaces:
        try:
            ifindex: Optional[int] = interface_ifindex(interface_name)
            ifindex_error = ""
        except Exception as exc:
            ifindex = None
            ifindex_error = str(exc)
        ref = interface_ref(interface_name)
        attached_interface_details.append(
            {
                "interface_name": interface_name,
                "interface_ref": ref.get("interface_ref"),
                "container_interface_name": ref.get("interface_name"),
                "ifindex": ifindex,
                "netns": ref.get("netns"),
                "attach_directions": ["ingress", "egress"],
                "expected_roles": sorted(
                    attached_interface_roles.get(interface_name, set())
                ),
                "ifindex_error": ifindex_error,
            }
        )
    scope = {
        "scope": SELECTOR_MODE_FULL_ELIGIBLE,
        "selector": {
            "mode": SELECTOR_MODE_FULL_ELIGIBLE,
            "safety_limits": {},
        },
        "entries": resolved_dataplane_entries,
        "attached_interfaces": list(effective_attached_interfaces),
        "compiled_full_scope": {
            "runtime_generation_count": 1,
            "gz_fec_entry_count": int(proof.get("map_entry_count") or 0),
            "parameter_generation_count": 0,
            "parameter_link_count": 0,
            "parameter_entry_count": 0,
        },
    }
    environment = collect_environment()
    blockers = attach_blockers(environment)
    report: Dict[str, Any] = {
        "ok": False,
        "backend_mode": "experimental_bpf_backend",
        "bpf_scope": SELECTOR_MODE_FULL_ELIGIBLE,
        "source": str(DEFAULT_SOURCE),
        "environment": environment,
        "blockers": blockers,
        "attached_interfaces": list(effective_attached_interfaces),
        "attached_interface_details": attached_interface_details,
        "dataplane_entry_count": len(list(dataplane_entries)),
        "resolved_dataplane_entry_count": len(resolved_dataplane_entries),
        "resolved_dataplane_entries": resolved_dataplane_entries,
        "steps": {},
    }
    if blockers:
        report["reason"] = "toolchain_or_privilege_blocked"
        report["wall_seconds"] = round(time.perf_counter() - started_at, 6)
        return report

    attached: List[str] = []
    with tempfile.TemporaryDirectory(prefix="skyseed-bpf-owned-") as tmp:
        object_path = Path(tmp) / "skyseed_tc_dataplane_canary.o"
        try:
            capacities = bpf_map_capacities_for_scope(compiled=compiled, scope=scope)
            report["bpf_map_capacities"] = capacities
            report["steps"]["compile"] = compile_bpf(
                DEFAULT_SOURCE,
                object_path,
                capacities=capacities,
            )
            if not command_ok(report["steps"]["compile"]):
                report["reason"] = "compile_failed"
                return report
            report["steps"]["cleanup_before_attach"] = rollback_canary(
                attached_interfaces=effective_attached_interfaces,
                remove_maps=True,
            )
            attach_results = []
            for interface_name in effective_attached_interfaces:
                attach_results.extend(attach_canary(str(interface_name), object_path))
                attached.append(str(interface_name))
            report["steps"]["attach"] = attach_results
            if not attach_results or not all(
                command_ok(result)
                or (
                    "tc" in [str(part) for part in result.get("args", [])]
                    and "qdisc" in [str(part) for part in result.get("args", [])]
                    and "del" in [str(part) for part in result.get("args", [])]
                )
                for result in attach_results
            ):
                report["reason"] = "tc_bpf_attach_failed"
                return report
            map_load = load_canary_maps(
                compiled=compiled,
                scope=scope,
                active_runtime_generation=int(proof.get("generation_id") or 0),
                active_parameter_generation=0,
            )
            report["steps"]["map_load"] = map_load
            if not map_load.get("ok"):
                report["reason"] = "bpf_map_load_failed"
                return report
            report["ok"] = True
            report["reason"] = ""
            report["loaded_runtime_generation_count"] = int(
                map_load.get("loaded_generation_count") or 0
            )
            report["loaded_gz_fec_count"] = int(map_load.get("loaded_fec_count") or 0)
            report["loaded_dataplane_route_count"] = int(
                map_load.get("loaded_dataplane_route_count") or 0
            )
            report["loaded_dataplane_parameter_entry_count"] = int(
                map_load.get("loaded_dataplane_parameter_entry_count") or 0
            )
            report["active_generation_switch_count"] = int(
                map_load.get("active_generation_switch_count") or 0
            )
            return report
        finally:
            if report.get("ok") is not True and attached:
                report["rollback_after_failure"] = rollback_canary(
                    attached_interfaces=attached,
                    remove_maps=True,
                )
            report["wall_seconds"] = round(time.perf_counter() - started_at, 6)


def _bpf_owned_prepare_mpls_backend(
    *,
    proof: Dict[str, Any],
    contexts: Sequence[DirectGatewayExperimentContext],
    resident_attach_contexts: Optional[Sequence[DirectGatewayExperimentContext]] = None,
    reuse_attached_readiness: Optional[Dict[str, Any]] = None,
    fail_closed_on_reuse_miss: bool = False,
) -> Dict[str, Any]:
    try:
        from satellite.bpf_dataplane_canary import (
            attach_blockers,
            collect_environment,
            command_ok,
        )
        from satellite.bpf_mpls_live import (
            DEFAULT_MPLS_SOURCE,
            attach_mpls,
            compile_mpls_bpf,
            interface_ifindex,
            interface_ipv4_addresses,
            load_mpls_maps,
            read_mpls_tc_filter_stats,
            rollback_mpls,
            verify_mpls_clsact,
        )
    except Exception as exc:  # pragma: no cover - import posture is environment-specific.
        return {
            "ok": False,
            "reason": "bpf_mpls_prepare_import_failed",
            "error": str(exc),
            "attached_interfaces": [],
        }

    started_at = time.perf_counter()
    generation_id = int(proof.get("generation_id") or 0)
    timing_breakdown: Dict[str, float] = {
        "bpf_reuse_audit_seconds": 0.0,
        "bpf_map_compile_seconds": 0.0,
        "bpf_map_write_seconds": 0.0,
        "active_generation_flip_seconds": 0.0,
        "bpf_attach_seconds": 0.0,
        "bpf_live_role_read_seconds": 0.0,
    }
    logical_compile_started_at = time.perf_counter()
    try:
        compiled = _bpf_owned_compile_mpls_maps_for_contexts(
            contexts=contexts,
            generation_id=generation_id,
        )
    except Exception as exc:
        error = str(exc)
        active_binding_missing = (
            "missing canary interface" in error
            or ("Device " in error and " does not exist" in error)
        )
        return {
            "ok": False,
            "reason": (
                "ACTIVE_INTERFACE_BINDING_MISSING"
                if active_binding_missing
                else "bpf_mpls_compile_plan_failed"
            ),
            "classification": (
                "ACTIVE_INTERFACE_BINDING_MISSING"
                if active_binding_missing
                else "bpf_mpls_compile_plan_failed"
            ),
            "error": error,
            "attached_interfaces": [],
            "wall_seconds": round(time.perf_counter() - started_at, 6),
            "timing_breakdown": timing_breakdown,
        }
    try:
        resident_compiled: Dict[str, Any] | None = None
        future_interface_materialization: Dict[str, Any] | None = None
        if resident_attach_contexts is not None:
            resident_compiled = _bpf_owned_compile_mpls_maps_for_contexts(
                contexts=resident_attach_contexts,
                generation_id=generation_id,
                offline=True,
            )
            active_plan_refs = {
                str(interface_ref)
                for interface_ref in dict(compiled.get("attach_plan") or {})
                if str(interface_ref)
            }
            resident_plan_refs = {
                str(interface_ref)
                for interface_ref in dict(resident_compiled.get("attach_plan") or {})
                if str(interface_ref)
            }
            materialization_started_at = time.perf_counter()
            future_interface_materialization = (
                _bpf_owned_future_interface_materialization_evidence(
                    future_horizon_interfaces=sorted(
                        resident_plan_refs - active_plan_refs
                    ),
                    interface_ifindex=interface_ifindex,
                )
            )
            timing_breakdown[
                "future_interface_materialization_wall_seconds"
            ] = round(time.perf_counter() - materialization_started_at, 6)
        compiled = _bpf_owned_merge_resident_attach_coverage(
            compiled=compiled,
            resident_compiled=resident_compiled,
            future_interface_materialization=future_interface_materialization,
        )
        timing_breakdown["interface_discovery_logical_binding_wall_seconds"] = round(
            time.perf_counter() - logical_compile_started_at,
            6,
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": "bpf_mpls_compile_plan_failed",
            "classification": "BPF_COMPILE_CARDINALITY_FAILURE",
            "error": str(exc),
            "attached_interfaces": [],
            "wall_seconds": round(time.perf_counter() - started_at, 6),
            "timing_breakdown": timing_breakdown,
        }

    attach_cardinality_budget = _bpf_owned_resident_attach_budget(proof)
    resident_attach_coverage = dict(compiled.get("resident_attach_coverage") or {})
    attach_scope_count = len(dict(compiled.get("attach_plan") or {}))
    active_scope_count = len(list(compiled.get("active_materialized_interfaces") or []))
    future_materialized_count = len(
        list(compiled.get("future_materialized_attach_eligible_interfaces") or [])
    )
    future_non_materialized_count = len(
        list(compiled.get("future_non_materialized_capacity_only_interfaces") or [])
    )
    attach_cardinality_budget.update(
        {
            "attach_eligible_count": attach_scope_count,
            "attached_count": attach_scope_count,
            "active_materialized_interface_count": active_scope_count,
            "future_materialized_attach_eligible_interface_count": (
                future_materialized_count
            ),
            "future_non_materialized_capacity_only_interface_count": (
                future_non_materialized_count
            ),
            "budget_exceeded": attach_scope_count
            > int(attach_cardinality_budget.get("max_attach_interfaces") or 0),
        }
    )
    resident_attach_coverage["attach_cardinality_budget"] = dict(
        attach_cardinality_budget
    )
    compiled["resident_attach_coverage"] = resident_attach_coverage
    compiled["attach_cardinality_budget"] = dict(attach_cardinality_budget)
    if attach_cardinality_budget.get("budget_exceeded"):
        return {
            "ok": False,
            "reason": "bpf_resident_attach_coverage_budget_exceeded",
            "classification": "BPF_ATTACH_CARDINALITY_BUDGET_EXCEEDED",
            "attached_interfaces": list(compiled.get("attached_interfaces") or []),
            "attached_interface_details": list(
                compiled.get("attached_interface_details") or []
            ),
            "attach_plan": dict(compiled.get("attach_plan") or {}),
            "active_materialized_interfaces": list(
                compiled.get("active_materialized_interfaces") or []
            ),
            "resident_attach_eligible_interfaces": list(
                compiled.get("resident_attach_eligible_interfaces") or []
            ),
            "future_horizon_interfaces": list(
                compiled.get("future_horizon_interfaces") or []
            ),
            "future_materialized_attach_eligible_interfaces": list(
                compiled.get("future_materialized_attach_eligible_interfaces") or []
            ),
            "future_non_materialized_capacity_only_interfaces": list(
                compiled.get("future_non_materialized_capacity_only_interfaces")
                or []
            ),
            "compile_capacity_interfaces": list(
                compiled.get("compile_capacity_interfaces") or []
            ),
            "resident_attach_coverage": dict(
                compiled.get("resident_attach_coverage") or {}
            ),
            "interface_set_contract": dict(
                compiled.get("interface_set_contract") or {}
            ),
            "attach_cardinality_budget": dict(attach_cardinality_budget),
            "wall_seconds": round(time.perf_counter() - started_at, 6),
            "timing_breakdown": timing_breakdown,
        }

    effective_attached_interfaces = list(compiled.get("attached_interfaces") or [])
    environment_started_at = time.perf_counter()
    environment = collect_environment()
    blockers = attach_blockers(environment)
    timing_breakdown["bpf_environment_check_wall_seconds"] = round(
        time.perf_counter() - environment_started_at,
        6,
    )
    reuse_audit_started_at = time.perf_counter()
    attach_reuse = _bpf_owned_attach_reuse_plan(
        compiled=compiled,
        previous_readiness=reuse_attached_readiness,
    )
    timing_breakdown["bpf_reuse_audit_seconds"] = round(
        time.perf_counter() - reuse_audit_started_at,
        6,
    )
    report: Dict[str, Any] = {
        "ok": False,
        "backend_mode": "strict_bpf_owned_mpls_lfib",
        "source": str(DEFAULT_MPLS_SOURCE),
        "environment": environment,
        "blockers": blockers,
        "attached_interfaces": effective_attached_interfaces,
        "attached_interface_details": list(
            compiled.get("attached_interface_details") or []
        ),
        "resolved_mpls_flow_entries": list(
            compiled.get("resolved_mpls_flow_entries") or []
        ),
        "compiled_mpls_map_counts": dict(
            compiled.get("compiled_mpls_map_counts") or {}
        ),
        "compile_capacity_mpls_map_counts": dict(
            compiled.get("compile_capacity_mpls_map_counts")
            or compiled.get("compiled_mpls_map_counts")
            or {}
        ),
        "map_conflicts": list(compiled.get("map_conflicts") or []),
        "map_conflict_summary": dict(compiled.get("map_conflict_summary") or {}),
        "label_allocation": dict(compiled.get("label_allocation") or {}),
        "intermediate_core_lookup_uses_dst_ip": False,
        "attach_plan": dict(compiled.get("attach_plan") or {}),
        "active_materialized_interfaces": list(
            compiled.get("active_materialized_interfaces") or []
        ),
        "resident_attach_eligible_interfaces": list(
            compiled.get("resident_attach_eligible_interfaces") or []
        ),
        "future_horizon_interfaces": list(
            compiled.get("future_horizon_interfaces") or []
        ),
        "future_materialized_attach_eligible_interfaces": list(
            compiled.get("future_materialized_attach_eligible_interfaces") or []
        ),
        "future_non_materialized_capacity_only_interfaces": list(
            compiled.get("future_non_materialized_capacity_only_interfaces") or []
        ),
        "compile_capacity_interfaces": list(
            compiled.get("compile_capacity_interfaces") or []
        ),
        "attach_cardinality_budget": dict(
            compiled.get("attach_cardinality_budget") or {}
        ),
        "interface_set_contract": dict(
            compiled.get("interface_set_contract") or {}
        ),
        "resident_attach_coverage": dict(
            compiled.get("resident_attach_coverage") or {}
        ),
        "attach_reuse": attach_reuse,
        "steps": {},
        "timing_breakdown": timing_breakdown,
    }
    if blockers:
        report["reason"] = "toolchain_or_privilege_blocked"
        report["wall_seconds"] = round(time.perf_counter() - started_at, 6)
        return report
    if report["map_conflicts"]:
        report["reason"] = "bpf_mpls_map_key_conflict"
        report["wall_seconds"] = round(time.perf_counter() - started_at, 6)
        return report

    counts = dict(compiled.get("compiled_mpls_map_counts") or {})
    compile_capacity_counts = dict(
        compiled.get("compile_capacity_mpls_map_counts") or counts
    )
    previous_prepare_for_capacity = dict(
        dict((reuse_attached_readiness or {}).get("prepare_steps") or {}).get(
            "real_backend_prepare"
        )
        or {}
    )
    previous_counts_for_capacity = dict(
        previous_prepare_for_capacity.get("compiled_mpls_map_counts")
        or dict(reuse_attached_readiness or {}).get("mpls_map_entry_counts")
        or {}
    )
    effective_active_counts_for_capacity = dict(
        previous_counts_for_capacity or counts
    )
    capacities = _bpf_mpls_generation_map_capacities(
        current_counts=compile_capacity_counts,
        active_counts=previous_counts_for_capacity,
    )
    report["map_capacities"] = dict(capacities)
    report["map_capacity_contract"] = {
        "schema_version": "skyseed-bpf-map-generation-capacity-source-v1",
        "generation_keyed_maps_require_active_plus_target_plus_slack": True,
        "bootstrap_reserves_target_sized_inactive_generation": True,
        "active_generation_counts": previous_counts_for_capacity,
        "effective_active_generation_counts_for_capacity": effective_active_counts_for_capacity,
        "target_generation_counts": counts,
        "compile_capacity_generation_counts": compile_capacity_counts,
        "slack_entries": 16,
        "capacities": dict(capacities),
    }

    def map_reload_root_cause(map_load: Dict[str, Any]) -> Dict[str, Any]:
        first_failed = dict(map_load.get("first_failed_update") or {})
        if not first_failed:
            return {
                "schema_version": "skyseed-bpf-map-reload-root-cause-v1",
                "available": False,
                "reason": "map_load_failure_without_structured_first_failed_update",
                "active_generation": generation_id,
            }
        direct_error = dict(first_failed.get("direct_error_evidence") or {})
        map_state = dict(first_failed.get("map_state") or {})
        return {
            **first_failed,
            "available": True,
            "exact_bpf_map": first_failed.get("failed_map"),
            "failed_operation": first_failed.get("failed_operation"),
            "generation_id": first_failed.get("generation"),
            "active_generation": first_failed.get("active_generation"),
            "inactive_generation": first_failed.get("target_generation"),
            "target_generation": first_failed.get("target_generation"),
            "direct_error_evidence": direct_error,
            "failure_analysis": dict(first_failed.get("failure_analysis") or {}),
            "map_state": map_state,
            "failed_key_byte_length": first_failed.get("failed_key_byte_length"),
            "failed_value_byte_length": first_failed.get("failed_value_byte_length"),
            "failed_key_decoded": dict(
                first_failed.get("failed_key_decoded") or {}
            ),
            "failed_value_decoded": dict(
                first_failed.get("failed_value_decoded") or {}
            ),
            "bootstrap_and_steady_reload_use_same_schema": bool(
                dict(map_load.get("schema_consistency") or {}).get(
                    "bootstrap_and_steady_reload_share_schema"
                )
            ),
            "inactive_generation_written_before_active_generation_flip": bool(
                dict(map_load.get("generation_update_order") or {}).get(
                    "inactive_generation_written_before_active_generation_flip"
                )
            ),
            "active_generation_flip_attempted": bool(
                first_failed.get("active_generation_flip_attempted")
            ),
            "active_generation_left_unchanged_by_loader": bool(
                first_failed.get("active_generation_left_unchanged_by_loader")
                or map_load.get("active_generation_left_unchanged_by_loader")
            ),
            "map_cleanup_update_order": dict(
                map_load.get("generation_update_order") or {}
            ),
            "direct_error_summary": {
                "returncode": direct_error.get("returncode"),
                "errno": direct_error.get("errno"),
                "stderr": direct_error.get("stderr"),
            },
            "map_state_summary": {
                "pinned_path": map_state.get("pinned_path"),
                "fd_state": map_state.get("fd_state"),
                "map_ids": list(map_state.get("map_ids") or []),
                "selected_map_id": map_state.get("selected_map_id"),
                "map_fd": map_state.get("map_fd"),
                "expected_type": map_state.get("expected_type"),
                "live_type": map_state.get("live_type"),
                "max_entries": map_state.get("max_entries"),
                "expected_max_entries": map_state.get("expected_max_entries"),
                "live_max_entries": map_state.get("live_max_entries"),
                "expected_bytes_key": map_state.get("expected_bytes_key"),
                "expected_bytes_value": map_state.get("expected_bytes_value"),
                "live_bytes_key": map_state.get("live_bytes_key"),
                "live_bytes_value": map_state.get("live_bytes_value"),
                "schema_matches_expected": map_state.get(
                    "schema_matches_expected"
                ),
                "type_key_value_matches_expected": map_state.get(
                    "type_key_value_matches_expected"
                ),
                "capacity_matches_static_expected_max": map_state.get(
                    "capacity_matches_static_expected_max"
                ),
                "current_entries": map_state.get("current_entries"),
                "expected_entries": map_state.get("expected_entries"),
                "generation_entry_counts": dict(
                    map_state.get("generation_entry_counts") or {}
                ),
                "active_generation_entry_count": map_state.get(
                    "active_generation_entry_count"
                ),
                "target_generation_entry_count": map_state.get(
                    "target_generation_entry_count"
                ),
                "generation_capacity_contract": dict(
                    map_state.get("generation_capacity_contract") or {}
                ),
                "stale_entries": map_state.get("stale_entries"),
            },
        }

    attach_reuse = dict(report.get("attach_reuse") or {})
    if (
        fail_closed_on_reuse_miss
        and reuse_attached_readiness
        and attach_reuse.get("reuse_eligible") is not True
    ):
        report["reason"] = (
            "operational_clean_bpf_attach_reuse_not_eligible:"
            + str(
                attach_reuse.get("reuse_rejected_reason")
                or attach_reuse.get("reason")
                or "unknown"
            )
        )
        report["fail_closed"] = True
        report["blocked_stage"] = "bpf_hotpath_attach_reuse"
        report["wall_seconds"] = round(time.perf_counter() - started_at, 6)
        timing_breakdown["total_wall_seconds"] = float(report["wall_seconds"])
        return report
    if attach_reuse.get("reuse_eligible") is True:
        reused_clsact_guard = _bpf_owned_reused_attach_clsact_guard(
            attach_reuse=attach_reuse,
            verify_clsact=verify_mpls_clsact,
        )
        report["steps"]["reused_attach_clsact_guard"] = {
            "ok": bool(reused_clsact_guard.get("ok")),
            "skipped": bool(reused_clsact_guard.get("skipped")),
            "reason": str(reused_clsact_guard.get("reason") or ""),
            "checked_interfaces": list(
                reused_clsact_guard.get("checked_interfaces") or []
            ),
            "missing_interfaces": list(
                reused_clsact_guard.get("missing_interfaces") or []
            ),
            "results": list(reused_clsact_guard.get("results") or []),
        }
        report["reused_attach_clsact_guard_metrics"] = dict(
            reused_clsact_guard.get("metrics") or {}
        )
        report["steps"]["reused_attach_clsact_guard_metrics"] = dict(
            report["reused_attach_clsact_guard_metrics"]
        )
        attach_reuse = dict(reused_clsact_guard.get("attach_reuse") or attach_reuse)
        report["attach_reuse"] = dict(attach_reuse)
    if attach_reuse.get("reuse_eligible") is True:
        delta_attach_plan = dict(attach_reuse.get("delta_attach_plan") or {})
        delta_new_interfaces = {
            str(interface_ref)
            for interface_ref in list(attach_reuse.get("missing_interfaces") or [])
        }
        report["steps"]["cleanup_before_attach"] = {
            "skipped": True,
            "reason": "normal_epoch_update_preserves_existing_tc_bpf_attach",
        }
        report["attach_lifecycle_mode"] = "normal_epoch_update"
        report["reused_attach_count"] = int(attach_reuse.get("reused_attach_count") or 0)
        report["delta_attach_count"] = int(attach_reuse.get("delta_attach_count") or 0)
        report["full_attach_count"] = 0
        report["delta_attach_interfaces"] = list(
            attach_reuse.get("delta_attach_interfaces") or []
        )
        if delta_attach_plan:
            with tempfile.TemporaryDirectory(prefix="skyseed-bpf-owned-mpls-delta-") as tmp:
                object_path = Path(tmp) / "skyseed_tc_mpls_dataplane.o"
                object_compile_started_at = time.perf_counter()
                report["steps"]["compile"] = compile_mpls_bpf(
                    DEFAULT_MPLS_SOURCE,
                    object_path,
                    capacities=capacities,
                )
                timing_breakdown["bpf_object_compile_or_cache_wall_seconds"] = round(
                    time.perf_counter() - object_compile_started_at,
                    6,
                )
                timing_breakdown["bpf_map_compile_seconds"] = timing_breakdown[
                    "bpf_object_compile_or_cache_wall_seconds"
                ]
                if not command_ok(report["steps"]["compile"]):
                    report["reason"] = "compile_failed_for_delta_attach"
                    report["attach_reuse"] = {
                        **attach_reuse,
                        "reuse_eligible": False,
                        "reason": "delta_attach_compile_failed",
                        "reuse_rejected_reason": "delta_attach_compile_failed",
                    }
                    report["wall_seconds"] = round(time.perf_counter() - started_at, 6)
                    timing_breakdown["total_wall_seconds"] = float(report["wall_seconds"])
                    return report
                attach_started_at = time.perf_counter()
                attach_results: List[Dict[str, Any]] = []
                attached_details_by_ref = {
                    str(detail.get("interface_ref") or detail.get("interface_name") or ""): dict(detail)
                    for detail in list(compiled.get("attached_interface_details") or [])
                }
                control_plane_bypass_by_interface: Dict[str, List[str]] = {}
                delta_attached: List[str] = []
                for interface_ref, directions in delta_attach_plan.items():
                    detail = dict(attached_details_by_ref.get(str(interface_ref)) or {})
                    expected_roles = [
                        str(role)
                        for role in list(detail.get("expected_roles") or [])
                    ]
                    control_plane_ipv4_dsts: List[str] = []
                    if any("classifier" in role for role in expected_roles):
                        control_plane_ipv4_dsts = interface_ipv4_addresses(
                            str(interface_ref)
                        )
                        control_plane_bypass_by_interface[str(interface_ref)] = list(
                            control_plane_ipv4_dsts
                        )
                    attach_results.extend(
                        attach_mpls(
                            str(interface_ref),
                            object_path,
                            directions=list(directions or ["ingress"]),
                            control_plane_ipv4_addrs=control_plane_ipv4_dsts,
                            clsact_setup_mode=(
                                "ensure_new_delta"
                                if str(interface_ref) in delta_new_interfaces
                                else "expect_existing"
                            ),
                        )
                    )
                    delta_attached.append(str(interface_ref))
                report["steps"]["attach"] = attach_results
                report["clsact_lifecycle_metrics"] = _bpf_clsact_lifecycle_metrics(
                    attach_results,
                    delta_attach_interfaces=delta_attached,
                )
                report["steps"]["clsact_lifecycle_metrics"] = dict(
                    report["clsact_lifecycle_metrics"]
                )
                report["steps"]["classifier_control_plane_bypass"] = {
                    "mode": "tc_flower_dst_ip_local_interface_before_bpf",
                    "interface_ipv4_dsts": control_plane_bypass_by_interface,
                    "interface_count": len(control_plane_bypass_by_interface),
                    "address_count": sum(
                        len(addresses)
                        for addresses in control_plane_bypass_by_interface.values()
                    ),
                }
                report["steps"]["classifier_control_plane_bypass_stats"] = {
                    interface_ref: read_mpls_tc_filter_stats(
                        interface_ref,
                        directions=list(delta_attach_plan.get(interface_ref) or ["ingress"]),
                    )
                    for interface_ref in sorted(control_plane_bypass_by_interface)
                }
                timing_breakdown["bpf_attach_tc_qdisc_setup_wall_seconds"] = round(
                    time.perf_counter() - attach_started_at,
                    6,
                )
                timing_breakdown["bpf_attach_seconds"] = timing_breakdown[
                    "bpf_attach_tc_qdisc_setup_wall_seconds"
                ]
                if not attach_results or not all(command_ok(result) for result in attach_results):
                    report["reason"] = "tc_bpf_mpls_delta_attach_failed"
                    report["attach_reuse"] = {
                        **attach_reuse,
                        "reuse_eligible": False,
                        "reason": "delta_attach_failed",
                        "reuse_rejected_reason": "delta_attach_failed",
                    }
                    report["rollback_after_failure"] = rollback_mpls(
                        attached_interfaces=effective_attached_interfaces,
                        remove_maps=True,
                    )
                    report["wall_seconds"] = round(time.perf_counter() - started_at, 6)
                    timing_breakdown["total_wall_seconds"] = float(report["wall_seconds"])
                    return report
        else:
            report["steps"]["compile"] = {
                "skipped": True,
                "reason": "existing_tc_bpf_object_attach_reused",
            }
            report["steps"]["attach"] = {
                "skipped": True,
                "reason": "existing_tc_bpf_attach_reused",
                "attached_interfaces": list(compiled.get("attached_interfaces") or []),
            }
        map_load_started_at = time.perf_counter()
        compiled_for_map_load = {
            **dict(compiled),
            "bpf_runtime_update_phase": (
                "steady_delta_attach" if delta_attach_plan else "steady_map_only"
            ),
        }
        map_load = load_mpls_maps(compiled_for_map_load)
        report["steps"]["map_load"] = map_load
        map_load_total_wall_seconds = round(
            time.perf_counter() - map_load_started_at,
            6,
        )
        timing_breakdown["bpf_map_load_total_wall_seconds"] = map_load_total_wall_seconds
        timing_breakdown["bpf_map_population_wall_seconds"] = round(
            float(
                dict(map_load or {}).get(
                    "map_write_wall_seconds",
                    map_load_total_wall_seconds,
                )
            ),
            6,
        )
        timing_breakdown["bpf_map_write_seconds"] = timing_breakdown[
            "bpf_map_population_wall_seconds"
        ]
        timing_breakdown["bpf_map_write_update_count"] = int(
            dict(map_load or {}).get("map_write_update_count") or 0
        )
        timing_breakdown["active_generation_update_count"] = int(
            dict(map_load or {}).get("active_generation_update_count")
            or dict(map_load or {}).get("active_generation_switch_count")
            or 0
        )
        timing_breakdown["bpf_map_update_count"] = int(
            dict(map_load or {}).get("total_update_count")
            or (
                int(timing_breakdown["bpf_map_write_update_count"])
                + int(timing_breakdown["active_generation_update_count"])
            )
        )
        timing_breakdown["active_generation_flip_seconds"] = float(
            dict(map_load or {}).get("active_generation_flip_wall_seconds") or 0.0
        )
        if map_load.get("ok"):
            if not delta_attach_plan:
                report["steps"]["live_interface_roles_after_map_load"] = {
                    "skipped": True,
                    "reason": "steady_epoch_map_only_reuses_existing_interface_roles",
                }
                report["steps"]["interface_role_completeness_after_map_load"] = {
                    "skipped": True,
                    "reason": "steady_epoch_map_only_reuses_previous_role_completeness",
                    "ok": True,
                }
                report["ok"] = True
                report["reason"] = ""
                report["prepare_mode"] = "reuse_attached_bpf_map_reload"
                report["active_generation"] = int(map_load.get("active_generation") or 0)
                report["loaded_runtime_generation_count"] = 1
                report["loaded_gz_fec_count"] = int(proof.get("map_entry_count") or 0)
                report["loaded_dataplane_route_count"] = 0
                report["loaded_dataplane_parameter_entry_count"] = 0
                report["loaded_classifier_count"] = int(
                    map_load.get("loaded_classifier_count") or 0
                )
                report["loaded_core_lfib_count"] = int(
                    map_load.get("loaded_core_lfib_count") or 0
                )
                report["loaded_egress_handoff_count"] = int(
                    map_load.get("loaded_egress_handoff_count") or 0
                )
                report["loaded_interface_role_count"] = int(
                    map_load.get("loaded_interface_role_count") or 0
                )
                report["active_generation_switch_count"] = int(
                    map_load.get("active_generation_switch_count", 1) or 0
                )
                report["wall_seconds"] = round(time.perf_counter() - started_at, 6)
                timing_breakdown["total_wall_seconds"] = float(report["wall_seconds"])
                return report
            live_role_started_at = time.perf_counter()
            live_interface_roles = _read_bpf_owned_interface_roles()
            report["steps"]["live_interface_roles_after_map_load"] = (
                live_interface_roles
            )
            timing_breakdown["bpf_interface_role_live_read_wall_seconds"] = round(
                time.perf_counter() - live_role_started_at,
                6,
            )
            timing_breakdown["bpf_live_role_read_seconds"] = timing_breakdown[
                "bpf_interface_role_live_read_wall_seconds"
            ]
            live_role_readable = bool(
                live_interface_roles.get("ok") is True
                and _command_succeeded(
                    dict(live_interface_roles.get("command") or {})
                )
            )
            role_completeness = _bpf_owned_interface_role_completeness(
                {
                    "attached_interface_details": list(
                        compiled.get("attached_interface_details") or []
                    ),
                    "prepare_steps": {
                        "real_backend_prepare": {
                            "steps": {"map_load": map_load},
                        }
                    },
                    "live_interface_roles": live_interface_roles,
                }
            )
            report["steps"]["interface_role_completeness_after_map_load"] = (
                role_completeness
            )
            if live_role_readable and role_completeness.get("ok") is True:
                report["ok"] = True
                report["reason"] = ""
                report["prepare_mode"] = (
                    "delta_attach_and_map_reload"
                    if delta_attach_plan
                    else "reuse_attached_bpf_map_reload"
                )
                report["active_generation"] = int(map_load.get("active_generation") or 0)
                report["loaded_runtime_generation_count"] = 1
                report["loaded_gz_fec_count"] = int(proof.get("map_entry_count") or 0)
                report["loaded_dataplane_route_count"] = 0
                report["loaded_dataplane_parameter_entry_count"] = 0
                report["loaded_classifier_count"] = int(
                    map_load.get("loaded_classifier_count") or 0
                )
                report["loaded_core_lfib_count"] = int(
                    map_load.get("loaded_core_lfib_count") or 0
                )
                report["loaded_egress_handoff_count"] = int(
                    map_load.get("loaded_egress_handoff_count") or 0
                )
                report["loaded_interface_role_count"] = int(
                    map_load.get("loaded_interface_role_count") or 0
                )
                report["active_generation_switch_count"] = int(
                    map_load.get("active_generation_switch_count", 1) or 0
                )
                report["wall_seconds"] = round(time.perf_counter() - started_at, 6)
                timing_breakdown["total_wall_seconds"] = float(report["wall_seconds"])
                return report
            report["attach_reuse"] = {
                **attach_reuse,
                "reuse_eligible": False,
                "reason": "map_reload_role_completeness_failed",
            }
        else:
            report["attach_reuse"] = {
                **attach_reuse,
                "reuse_eligible": False,
                "reason": "map_reload_failed_before_full_prepare",
            }
            report["steps"]["reuse_map_load_attempt"] = map_load
            report["map_reload_root_cause"] = map_reload_root_cause(map_load)
            report["steps"]["map_reload_root_cause"] = dict(
                report["map_reload_root_cause"]
            )
            if fail_closed_on_reuse_miss:
                report["reason"] = "operational_clean_bpf_map_reload_failed"
                report["fail_closed"] = True
                report["blocked_stage"] = "bpf_hotpath_map_reload"
                report["wall_seconds"] = round(time.perf_counter() - started_at, 6)
                timing_breakdown["total_wall_seconds"] = float(report["wall_seconds"])
                return report

    attached: List[str] = []
    with tempfile.TemporaryDirectory(prefix="skyseed-bpf-owned-mpls-") as tmp:
        object_path = Path(tmp) / "skyseed_tc_mpls_dataplane.o"
        try:
            object_compile_started_at = time.perf_counter()
            report["steps"]["compile"] = compile_mpls_bpf(
                DEFAULT_MPLS_SOURCE,
                object_path,
                capacities=capacities,
            )
            timing_breakdown["bpf_object_compile_or_cache_wall_seconds"] = round(
                time.perf_counter() - object_compile_started_at,
                6,
            )
            timing_breakdown["bpf_map_compile_seconds"] = timing_breakdown[
                "bpf_object_compile_or_cache_wall_seconds"
            ]
            if not command_ok(report["steps"]["compile"]):
                report["reason"] = "compile_failed"
                return report
            cleanup_started_at = time.perf_counter()
            report["steps"]["cleanup_before_attach"] = rollback_mpls(
                attached_interfaces=effective_attached_interfaces,
                remove_maps=True,
            )
            report["attach_lifecycle_mode"] = "dirty_recovery_reset"
            timing_breakdown["tc_qdisc_cleanup_before_attach_wall_seconds"] = round(
                time.perf_counter() - cleanup_started_at,
                6,
            )
            attach_started_at = time.perf_counter()
            attach_results: List[Dict[str, Any]] = []
            attach_plan = dict(compiled.get("attach_plan") or {})
            attached_details_by_ref = {
                str(detail.get("interface_ref") or detail.get("interface_name") or ""): dict(detail)
                for detail in list(compiled.get("attached_interface_details") or [])
            }
            control_plane_bypass_by_interface: Dict[str, List[str]] = {}
            for interface_ref, directions in attach_plan.items():
                detail = dict(attached_details_by_ref.get(str(interface_ref)) or {})
                expected_roles = [
                    str(role)
                    for role in list(detail.get("expected_roles") or [])
                ]
                control_plane_ipv4_dsts: List[str] = []
                if any("classifier" in role for role in expected_roles):
                    control_plane_ipv4_dsts = interface_ipv4_addresses(
                        str(interface_ref)
                    )
                    control_plane_bypass_by_interface[str(interface_ref)] = list(
                        control_plane_ipv4_dsts
                    )
                attach_results.extend(
                    attach_mpls(
                        str(interface_ref),
                        object_path,
                        directions=list(directions or ["ingress"]),
                        control_plane_ipv4_addrs=control_plane_ipv4_dsts,
                        clsact_setup_mode="reset",
                    )
                )
                attached.append(str(interface_ref))
            report["steps"]["attach"] = attach_results
            report["clsact_lifecycle_metrics"] = _bpf_clsact_lifecycle_metrics(
                attach_results
            )
            report["steps"]["clsact_lifecycle_metrics"] = dict(
                report["clsact_lifecycle_metrics"]
            )
            report["steps"]["classifier_control_plane_bypass"] = {
                "mode": "tc_flower_dst_ip_local_interface_before_bpf",
                "interface_ipv4_dsts": control_plane_bypass_by_interface,
                "interface_count": len(control_plane_bypass_by_interface),
                "address_count": sum(
                    len(addresses)
                    for addresses in control_plane_bypass_by_interface.values()
                ),
            }
            report["steps"]["classifier_control_plane_bypass_stats"] = {
                interface_ref: read_mpls_tc_filter_stats(
                    interface_ref,
                    directions=list(attach_plan.get(interface_ref) or ["ingress"]),
                )
                for interface_ref in sorted(control_plane_bypass_by_interface)
            }
            timing_breakdown["bpf_attach_tc_qdisc_setup_wall_seconds"] = round(
                time.perf_counter() - attach_started_at,
                6,
            )
            timing_breakdown["bpf_attach_seconds"] = timing_breakdown[
                "bpf_attach_tc_qdisc_setup_wall_seconds"
            ]
            if not attach_results or not all(
                command_ok(result)
                or (
                    "tc" in [str(part) for part in result.get("args", [])]
                    and "qdisc" in [str(part) for part in result.get("args", [])]
                    and "del" in [str(part) for part in result.get("args", [])]
                )
                for result in attach_results
            ):
                report["reason"] = "tc_bpf_mpls_attach_failed"
                return report
            map_load_started_at = time.perf_counter()
            compiled_for_map_load = {
                **dict(compiled),
                "bpf_runtime_update_phase": "bootstrap",
            }
            map_load = load_mpls_maps(compiled_for_map_load)
            report["steps"]["map_load"] = map_load
            map_load_total_wall_seconds = round(
                time.perf_counter() - map_load_started_at,
                6,
            )
            timing_breakdown["bpf_map_load_total_wall_seconds"] = map_load_total_wall_seconds
            timing_breakdown["bpf_map_population_wall_seconds"] = round(
                float(
                    dict(map_load or {}).get(
                        "map_write_wall_seconds",
                        map_load_total_wall_seconds,
                    )
                ),
                6,
            )
            timing_breakdown["bpf_map_write_seconds"] = timing_breakdown[
                "bpf_map_population_wall_seconds"
            ]
            timing_breakdown["bpf_map_write_update_count"] = int(
                dict(map_load or {}).get("map_write_update_count") or 0
            )
            timing_breakdown["active_generation_update_count"] = int(
                dict(map_load or {}).get("active_generation_update_count")
                or dict(map_load or {}).get("active_generation_switch_count")
                or 0
            )
            timing_breakdown["bpf_map_update_count"] = int(
                dict(map_load or {}).get("total_update_count")
                or (
                    int(timing_breakdown["bpf_map_write_update_count"])
                    + int(timing_breakdown["active_generation_update_count"])
                )
            )
            timing_breakdown["active_generation_flip_seconds"] = float(
                dict(map_load or {}).get("active_generation_flip_wall_seconds") or 0.0
            )
            if not map_load.get("ok"):
                report["reason"] = "bpf_mpls_map_load_failed"
                return report
            live_role_started_at = time.perf_counter()
            live_interface_roles = _read_bpf_owned_interface_roles()
            report["steps"]["live_interface_roles_after_map_load"] = (
                live_interface_roles
            )
            timing_breakdown["bpf_interface_role_live_read_wall_seconds"] = round(
                time.perf_counter() - live_role_started_at,
                6,
            )
            timing_breakdown["bpf_live_role_read_seconds"] = timing_breakdown[
                "bpf_interface_role_live_read_wall_seconds"
            ]
            live_role_readable = bool(
                live_interface_roles.get("ok") is True
                and _command_succeeded(
                    dict(live_interface_roles.get("command") or {})
                )
            )
            role_completeness = _bpf_owned_interface_role_completeness(
                {
                    "attached_interface_details": list(
                        compiled.get("attached_interface_details") or []
                    ),
                    "prepare_steps": {
                        "real_backend_prepare": {
                            "steps": {"map_load": map_load},
                        }
                    },
                    "live_interface_roles": live_interface_roles,
                }
            )
            report["steps"]["interface_role_completeness_after_map_load"] = (
                role_completeness
            )
            if not live_role_readable:
                report["reason"] = "bpf_mpls_interface_role_map_read_failed"
                return report
            if role_completeness.get("ok") is not True:
                report["reason"] = "bpf_mpls_interface_role_map_incomplete"
                return report
            report["ok"] = True
            report["reason"] = ""
            report["prepare_mode"] = "full_attach_and_map_load"
            report["reused_attach_count"] = 0
            report["delta_attach_count"] = 0
            report["full_attach_count"] = len(attach_plan)
            report["delta_attach_interfaces"] = []
            report["active_generation"] = int(map_load.get("active_generation") or 0)
            report["loaded_runtime_generation_count"] = 1
            report["loaded_gz_fec_count"] = int(proof.get("map_entry_count") or 0)
            report["loaded_dataplane_route_count"] = 0
            report["loaded_dataplane_parameter_entry_count"] = 0
            report["loaded_classifier_count"] = int(
                map_load.get("loaded_classifier_count") or 0
            )
            report["loaded_core_lfib_count"] = int(
                map_load.get("loaded_core_lfib_count") or 0
            )
            report["loaded_egress_handoff_count"] = int(
                map_load.get("loaded_egress_handoff_count") or 0
            )
            report["loaded_interface_role_count"] = int(
                map_load.get("loaded_interface_role_count") or 0
            )
            report["active_generation_switch_count"] = int(
                map_load.get("active_generation_switch_count", 1) or 0
            )
            return report
        finally:
            if report.get("ok") is not True and attached:
                rollback_started_at = time.perf_counter()
                report["rollback_after_failure"] = rollback_mpls(
                    attached_interfaces=attached,
                    remove_maps=True,
                )
                timing_breakdown["bpf_prepare_failure_rollback_wall_seconds"] = round(
                    time.perf_counter() - rollback_started_at,
                    6,
                )
            report["wall_seconds"] = round(time.perf_counter() - started_at, 6)
            timing_breakdown["total_wall_seconds"] = float(report["wall_seconds"])


def _prepare_bpf_owned_readiness(
    *,
    runtime_update_plan: RuntimeUpdatePlan,
    topology_path: Path,
    contexts: Sequence[DirectGatewayExperimentContext],
    resident_attach_contexts: Optional[Sequence[DirectGatewayExperimentContext]] = None,
    bpf_owned_readiness: Optional[Dict[str, Any]],
    fail_closed_on_reuse_miss: bool = False,
) -> Dict[str, Any]:
    previous_readiness_mismatch_reason = ""
    if bpf_owned_readiness:
        checked = _prove_bpf_owned_readiness(
            runtime_update_plan=runtime_update_plan,
            bpf_owned_readiness=bpf_owned_readiness,
        )
        if checked.get("ready") is True:
            prepare_steps = dict(checked.get("prepare_steps") or {})
            prepare_steps["real_backend_prepare"] = (
                _bpf_owned_existing_readiness_prepare_evidence(checked)
            )
            return {
                **checked,
                "prepare_steps": prepare_steps,
            }
        previous_readiness_mismatch_reason = str(
            checked.get("readiness_mismatch_reason") or checked.get("reason") or ""
        )
    proof = _derive_bpf_owned_readiness_proof(
        runtime_update_plan=runtime_update_plan,
    )
    if proof.get("logical_intent_ready") is not True:
        return proof
    host_interfaces = _bpf_owned_host_interfaces_for_contexts(
        topology_path=topology_path,
        contexts=tuple(contexts),
    )
    if not host_interfaces:
        return {
            **proof,
            "ready": False,
            "ok": False,
            "reason": "bpf_owned_no_host_attach_interfaces",
            "fallback_reason": "bpf_owned_no_host_attach_interfaces",
            "fallback_to_linux": True,
            "attached_interfaces": [],
            "logical_attached_interfaces": list(proof.get("attached_interfaces") or []),
        }
    prepare = _bpf_owned_prepare_mpls_backend(
        proof=proof,
        contexts=contexts,
        resident_attach_contexts=resident_attach_contexts,
        reuse_attached_readiness=bpf_owned_readiness,
        fail_closed_on_reuse_miss=fail_closed_on_reuse_miss,
    )
    mpls_counts = dict(prepare.get("compiled_mpls_map_counts") or {})
    prepared = {
        **proof,
        "ready": bool(prepare.get("ok")),
        "ok": bool(prepare.get("ok")),
        "reason": str(prepare.get("reason") or ""),
        "fallback_reason": "" if prepare.get("ok") else str(prepare.get("reason") or "bpf_prepare_failed"),
        "fallback_to_linux": prepare.get("ok") is not True,
        "attached_interfaces": list(prepare.get("attached_interfaces") or host_interfaces),
        "logical_attached_interfaces": list(proof.get("attached_interfaces") or []),
        "packet_route_entry_count": 0,
        "packet_route_entries": [],
        "resolved_packet_route_entries": [],
        "packet_route_entries_disabled_by_mpls_lfib": True,
        "mpls_map_entry_counts": mpls_counts,
        "previous_readiness_mismatch_reason": previous_readiness_mismatch_reason,
        "blocked_stage": str(prepare.get("blocked_stage") or ""),
        "fail_closed": bool(prepare.get("fail_closed")),
        "map_reload_root_cause": dict(prepare.get("map_reload_root_cause") or {}),
        "mpls_map_conflict_summary": dict(
            prepare.get("map_conflict_summary") or {}
        ),
        "mpls_map_conflicts": list(prepare.get("map_conflicts") or []),
        "mpls_label_allocation": dict(prepare.get("label_allocation") or {}),
        "resolved_mpls_flow_entries": list(
            prepare.get("resolved_mpls_flow_entries") or []
        ),
        "intermediate_core_lookup_uses_dst_ip": False,
        "attached_interface_details": list(
            prepare.get("attached_interface_details") or []
        ),
        "active_materialized_interfaces": list(
            prepare.get("active_materialized_interfaces") or []
        ),
        "resident_attach_eligible_interfaces": list(
            prepare.get("resident_attach_eligible_interfaces") or []
        ),
        "future_horizon_interfaces": list(
            prepare.get("future_horizon_interfaces") or []
        ),
        "future_materialized_attach_eligible_interfaces": list(
            prepare.get("future_materialized_attach_eligible_interfaces") or []
        ),
        "future_non_materialized_capacity_only_interfaces": list(
            prepare.get("future_non_materialized_capacity_only_interfaces") or []
        ),
        "compile_capacity_interfaces": list(
            prepare.get("compile_capacity_interfaces") or []
        ),
        "attach_cardinality_budget": dict(
            prepare.get("attach_cardinality_budget") or {}
        ),
        "interface_set_contract": dict(
            prepare.get("interface_set_contract") or {}
        ),
        "resident_attach_coverage": dict(
            prepare.get("resident_attach_coverage") or {}
        ),
        "prepare_steps": {
            **dict(proof.get("prepare_steps") or {}),
            "real_backend_prepare": prepare,
            "compile": dict(prepare.get("steps", {}).get("compile") or {}),
            "load": dict(prepare.get("steps", {}).get("map_load") or {}),
            "attach": {
                "ok": bool(prepare.get("ok")),
                "attached_interfaces": list(
                    prepare.get("attached_interfaces") or host_interfaces
                ),
                "results": list(prepare.get("steps", {}).get("attach") or []),
            },
        },
        "runtime_generation_loaded": bool(prepare.get("ok")),
        "active_generation_set": bool(prepare.get("ok")),
        "map_ready": bool(prepare.get("ok")),
        "map_entry_count": int(prepare.get("loaded_gz_fec_count") or proof.get("map_entry_count") or 0),
        "real_backend_prepared": bool(prepare.get("ok")),
        "linux_gz_fallback_enabled": False,
        "eligible_core_fallback_to_linux": 0,
        "skipped_by_bpf_backend": [],
    }
    mismatch = _bpf_owned_readiness_mismatch_reason(
        runtime_update_plan=runtime_update_plan,
        readiness=prepared,
    )
    if mismatch:
        return {
            **prepared,
            "ready": False,
            "ok": False,
            "reason": mismatch,
            "fallback_reason": mismatch,
            "fallback_to_linux": True,
            "readiness_mismatch_reason": mismatch,
        }
    return prepared


def _bpf_owned_runtime_readiness_failure_message(
    readiness: Dict[str, Any],
) -> str:
    reason = str(readiness.get("reason") or "bpf_owned_readiness_not_ready")
    blocked_stage = str(readiness.get("blocked_stage") or "")
    prepare_steps = dict(readiness.get("prepare_steps") or {})
    real_prepare = dict(prepare_steps.get("real_backend_prepare") or {})
    if not blocked_stage:
        blocked_stage = str(real_prepare.get("blocked_stage") or "")
    fail_closed = bool(readiness.get("fail_closed") or real_prepare.get("fail_closed"))
    fallback_reason = str(readiness.get("fallback_reason") or "")
    root_cause = dict(
        readiness.get("map_reload_root_cause")
        or real_prepare.get("map_reload_root_cause")
        or {}
    )
    parts = [
        f"reason={reason}",
        f"blocked_stage={blocked_stage or 'bpf_owned_runtime_readiness'}",
        f"fail_closed={fail_closed}",
    ]
    if fallback_reason:
        parts.append(f"fallback_reason={fallback_reason}")
    if root_cause:
        if root_cause.get("exact_bpf_map") or root_cause.get("failed_map"):
            parts.append(
                "failed_map="
                + str(root_cause.get("exact_bpf_map") or root_cause.get("failed_map"))
            )
        if root_cause.get("failed_operation"):
            parts.append(f"failed_operation={root_cause.get('failed_operation')}")
        if root_cause.get("generation_id") is not None:
            parts.append(f"generation_id={root_cause.get('generation_id')}")
        error_summary = dict(root_cause.get("direct_error_summary") or {})
        if error_summary.get("returncode") is not None:
            parts.append(f"returncode={error_summary.get('returncode')}")
    return "bpf_owned_runtime_readiness_failed:" + "; ".join(parts)


def _rollback_bpf_owned_prepare(readiness: Dict[str, Any]) -> Dict[str, Any]:
    attached_interfaces = [
        str(interface_name)
        for interface_name in list(readiness.get("attached_interfaces") or [])
        if str(interface_name)
    ]
    if not attached_interfaces:
        return {
            "attempted": False,
            "reason": "no_bpf_owned_attached_interfaces",
            "attach_lifecycle_mode": "final_cleanup",
            "rollback_completed": True,
            "attached_interfaces": [],
        }
    try:
        from satellite.bpf_mpls_live import rollback_mpls
    except Exception as exc:  # pragma: no cover - import posture is environment-specific.
        return {
            "attempted": False,
            "reason": "bpf_rollback_import_failed",
            "error": str(exc),
            "attach_lifecycle_mode": "failure_rollback_cleanup",
            "rollback_completed": False,
            "attached_interfaces": attached_interfaces,
        }
    return {
        "attempted": True,
        "attach_lifecycle_mode": "final_cleanup",
        "rollback_reason": "bpf_owned_cleanup_removes_tc_clsact_and_pinned_maps",
        **rollback_mpls(
            attached_interfaces=attached_interfaces,
            remove_maps=True,
        ),
        "attached_interfaces": attached_interfaces,
    }


def _read_bpf_owned_packet_counters() -> Dict[str, Any]:
    try:
        from satellite.bpf_mpls_live import read_mpls_packet_counters
    except Exception as exc:  # pragma: no cover - import posture is environment-specific.
        return {
            "ok": False,
            "reason": "bpf_counter_import_failed",
            "error": str(exc),
            "counters": {},
        }
    try:
        counters = read_mpls_packet_counters()
    except Exception as exc:  # pragma: no cover - host state dependent.
        return {
            "ok": False,
            "reason": "bpf_counter_read_failed",
            "error": str(exc),
            "counters": {},
        }
    return {
        "ok": True,
        **dict(counters),
    }


def _read_bpf_owned_miss_samples() -> Dict[str, Any]:
    try:
        from satellite.bpf_mpls_live import read_mpls_miss_samples
    except Exception as exc:  # pragma: no cover - import posture is environment-specific.
        return {
            "ok": False,
            "reason": "bpf_miss_sample_import_failed",
            "error": str(exc),
            "samples": [],
            "sample_count": 0,
        }
    try:
        samples = read_mpls_miss_samples()
    except Exception as exc:  # pragma: no cover - host state dependent.
        return {
            "ok": False,
            "reason": "bpf_miss_sample_read_failed",
            "error": str(exc),
            "samples": [],
            "sample_count": 0,
        }
    return {
        "ok": True,
        **dict(samples),
    }


def _bpf_owned_packet_counter_delta(
    before: Dict[str, Any],
    after: Dict[str, Any],
) -> Dict[str, int]:
    before_counters = dict(before.get("counters") or {})
    after_counters = dict(after.get("counters") or {})
    delta: Dict[str, int] = {}
    for key in sorted(set(before_counters) | set(after_counters)):
        delta[str(key)] = _as_int(after_counters.get(key)) - _as_int(
            before_counters.get(key)
        )
    return delta


def _read_bpf_owned_interface_counters() -> Dict[str, Any]:
    try:
        from satellite.bpf_mpls_live import read_mpls_interface_counters
    except Exception as exc:  # pragma: no cover - import posture is environment-specific.
        return {
            "ok": False,
            "reason": "bpf_interface_counter_import_failed",
            "error": str(exc),
            "counters_by_ifindex": {},
        }
    try:
        counters = read_mpls_interface_counters()
    except Exception as exc:  # pragma: no cover - host state dependent.
        return {
            "ok": False,
            "reason": "bpf_interface_counter_read_failed",
            "error": str(exc),
            "counters_by_ifindex": {},
        }
    return {
        "ok": True,
        **dict(counters),
    }


def _read_bpf_owned_interface_roles() -> Dict[str, Any]:
    try:
        from satellite.bpf_mpls_live import read_mpls_interface_roles
    except Exception as exc:  # pragma: no cover - import posture is environment-specific.
        return {
            "ok": False,
            "reason": "bpf_interface_role_import_failed",
            "error": str(exc),
            "entries": [],
        }
    try:
        roles = read_mpls_interface_roles()
    except Exception as exc:  # pragma: no cover - host state dependent.
        return {
            "ok": False,
            "reason": "bpf_interface_role_read_failed",
            "error": str(exc),
            "entries": [],
        }
    return {
        "ok": True,
        **dict(roles),
    }


def _read_bpf_owned_active_generation() -> Dict[str, Any]:
    try:
        from satellite.bpf_mpls_live import read_mpls_active_generation
    except Exception as exc:  # pragma: no cover - import posture is environment-specific.
        return {
            "ok": False,
            "reason": "bpf_active_generation_import_failed",
            "error": str(exc),
            "active_generation": None,
        }
    try:
        return dict(read_mpls_active_generation())
    except Exception as exc:  # pragma: no cover - host state dependent.
        return {
            "ok": False,
            "reason": "bpf_active_generation_read_failed",
            "error": str(exc),
            "active_generation": None,
        }


def _bpf_owned_validation_targets_for_context(
    context: DirectGatewayExperimentContext,
    *,
    primary_only: bool,
) -> List[Dict[str, str]]:
    if primary_only:
        return [
            {
                "node_id": str(context.target_host_node_id),
                "ip_address": str(context.ping_target_ip),
            }
        ]
    targets = [
        {
            "node_id": str(target.get("node_id")),
            "ip_address": str(target.get("ip_address")),
        }
        for target in context.validation_targets
        if target.get("node_id") and target.get("ip_address")
    ]
    return targets or [
        {
            "node_id": str(context.target_host_node_id),
            "ip_address": str(context.ping_target_ip),
        }
    ]


def _command_succeeded(command: Dict[str, Any]) -> bool:
    return int(command.get("returncode") or 0) == 0


def _bpf_clsact_lifecycle_metrics(
    attach_results: Sequence[Dict[str, Any]],
    *,
    delta_attach_interfaces: Sequence[str] = (),
) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {
        "clsact_delete_attempted": 0,
        "clsact_delete_skipped_missing_on_new_delta": 0,
        "clsact_add_count": 0,
        "clsact_replace_count": 0,
        "clsact_missing_on_expected_reuse_count": 0,
        "delta_attach_success_count": 0,
        "delta_attach_failure_count": 0,
        "delta_attach_interfaces": list(delta_attach_interfaces),
    }
    failed_delta_interfaces: set[str] = set()
    delta_interface_set = {str(interface) for interface in delta_attach_interfaces}
    for result in attach_results:
        event = str(result.get("clsact_lifecycle_event") or "")
        if event in {"delete_attempted", "delete_missing_ok_for_reset"}:
            metrics["clsact_delete_attempted"] += 1
        elif event == "delete_skipped_missing_on_new_delta":
            metrics["clsact_delete_skipped_missing_on_new_delta"] += 1
        elif event == "add" or event == "add_existing_ok_for_new_delta":
            metrics["clsact_add_count"] += 1
        elif event == "replace":
            metrics["clsact_replace_count"] += 1
        elif event == "missing_on_expected_reuse":
            metrics["clsact_missing_on_expected_reuse_count"] += 1
        if int(result.get("returncode") or 0) != 0:
            interface_ref = result.get("interface_ref")
            if isinstance(interface_ref, dict):
                raw_ref = str(
                    interface_ref.get("interface_ref")
                    or interface_ref.get("raw")
                    or interface_ref.get("interface_name")
                    or ""
                )
            else:
                raw_ref = str(interface_ref or "")
            if raw_ref in delta_interface_set:
                failed_delta_interfaces.add(raw_ref)
    if delta_interface_set:
        metrics["delta_attach_failure_count"] = len(failed_delta_interfaces)
        metrics["delta_attach_success_count"] = max(
            0,
            len(delta_interface_set) - len(failed_delta_interfaces),
        )
    return metrics


def _bpf_owned_attached_interface_guard(
    readiness: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        from satellite.bpf_dataplane_canary import run_interface_command
    except Exception as exc:  # pragma: no cover - import posture is environment-specific.
        return {
            "attached_count": 0,
            "details": [],
            "stale_ifindex_count": 0,
            "reason": "bpf_attach_guard_import_failed",
            "error": str(exc),
        }
    attached_details = list(readiness.get("attached_interface_details") or [])
    if not attached_details:
        prepare = dict(
            dict(readiness.get("prepare_steps") or {}).get("real_backend_prepare")
            or {}
        )
        attached_details = list(prepare.get("attached_interface_details") or [])
    reports: List[Dict[str, Any]] = []
    attached_count = 0
    stale_ifindex_count = 0
    for detail in attached_details:
        interface_name = str(
            dict(detail).get("interface_name")
            or dict(detail).get("interface_ref")
            or ""
        )
        if not interface_name:
            continue
        qdisc = run_interface_command(
            interface_name,
            ["tc", "qdisc", "show", "dev", "{iface}"],
            timeout=4.0,
        )
        has_clsact = "clsact" in (
            str(qdisc.get("stdout") or "") + str(qdisc.get("stderr") or "")
        )
        current_ifindex = _current_host_ifindex(interface_name)
        expected_ifindex = dict(detail).get("ifindex")
        stale = bool(
            expected_ifindex
            and current_ifindex
            and int(expected_ifindex) != int(current_ifindex)
        )
        stale_ifindex_count += 1 if stale else 0
        attached_count += 1 if has_clsact else 0
        reports.append(
            {
                "interface_name": interface_name,
                "expected_ifindex": expected_ifindex,
                "current_ifindex": current_ifindex,
                "ifindex_matches": not stale,
                "clsact_attached": has_clsact,
                "qdisc_command": qdisc,
            }
        )
    return {
        "attached_count": attached_count,
        "details": reports,
        "stale_ifindex_count": stale_ifindex_count,
    }


def _bpf_owned_final_validation_guard(
    session: "ActiveRuntimeSession",
    *,
    primary_only: bool,
) -> Dict[str, Any]:
    backend = dict(session.backend or {})
    readiness = dict(backend.get("bpf_owned_readiness") or {})
    expected_generation = _as_int(
        readiness.get("active_generation", readiness.get("generation_id")),
        default=0,
    )
    packet_counters = _read_bpf_owned_packet_counters()
    packet_counter_command = dict(packet_counters.get("command") or {})
    packet_counters_readable = bool(
        packet_counters.get("ok") is True
        and _command_succeeded(packet_counter_command)
    )
    active_generation_read = _read_bpf_owned_active_generation()
    active_generation_command = dict(active_generation_read.get("command") or {})
    active_generation = active_generation_read.get("active_generation")
    active_generation_readable = bool(
        active_generation_read.get("ok") is True
        and _command_succeeded(active_generation_command)
    )
    attach_guard = _bpf_owned_attached_interface_guard(readiness)
    live_interface_roles = _read_bpf_owned_interface_roles()
    interface_roles_command = dict(live_interface_roles.get("command") or {})
    interface_roles_readable = bool(
        live_interface_roles.get("ok") is True
        and _command_succeeded(interface_roles_command)
    )
    interface_role_completeness = _bpf_owned_interface_role_completeness(
        {
            **readiness,
            "live_interface_roles": live_interface_roles,
        }
    )

    resolved_entries = list(readiness.get("resolved_mpls_flow_entries") or [])
    if not resolved_entries:
        prepare = dict(
            dict(readiness.get("prepare_steps") or {}).get("real_backend_prepare")
            or {}
        )
        resolved_entries = list(prepare.get("resolved_mpls_flow_entries") or [])
    expected_flows: List[Dict[str, Any]] = []
    missing_forward_entries: List[Dict[str, Any]] = []
    missing_return_entries: List[Dict[str, Any]] = []
    core_entries_using_dst_ip: List[Dict[str, Any]] = []
    for context in session.contexts:
        for target in _bpf_owned_validation_targets_for_context(
            context,
            primary_only=primary_only,
        ):
            target_ip = str(target["ip_address"])
            flow_id = str(context.flow_id)
            forward_entries = [
                dict(entry)
                for entry in resolved_entries
                if str(entry.get("flow_id") or "") == flow_id
                and str(entry.get("target_ip") or "") == target_ip
                and str(entry.get("direction") or "") == "g_to_z"
                and str(entry.get("map_type") or "") == "ingress_classifier_map"
            ]
            forward_core_entries = [
                dict(entry)
                for entry in resolved_entries
                if str(entry.get("flow_id") or "") == flow_id
                and str(entry.get("direction") or "") == "g_to_z"
                and str(entry.get("map_type") or "") == "core_lfib_map"
            ]
            forward_handoff_entries = [
                dict(entry)
                for entry in resolved_entries
                if str(entry.get("flow_id") or "") == flow_id
                and str(entry.get("direction") or "") == "g_to_z"
                and str(entry.get("map_type") or "") == "egress_handoff_map"
            ]
            return_entries = [
                dict(entry)
                for entry in resolved_entries
                if str(entry.get("flow_id") or "") == flow_id
                and str(entry.get("direction") or "") == "z_to_g"
                and str(entry.get("map_type") or "")
                in {"ingress_classifier_map", "core_lfib_map", "egress_handoff_map"}
            ]
            return_handoff_entries = [
                dict(entry)
                for entry in return_entries
                if str(entry.get("map_type") or "") == "egress_handoff_map"
            ]
            core_entries_using_dst_ip.extend(
                [
                    dict(entry)
                    for entry in [*forward_core_entries, *return_entries]
                    if str(entry.get("map_type") or "") == "core_lfib_map"
                    and (
                        "target_ip" in entry
                        or "dst_ip" in entry
                        or entry.get("uses_dst_ip") is True
                    )
                ]
            )
            flow_summary = {
                "flow_id": flow_id,
                "ut_id": context.ut_id,
                "target_ip": target_ip,
                "target_host_node_id": target["node_id"],
                "forward_entry_count": len(forward_entries)
                + len(forward_core_entries)
                + len(forward_handoff_entries),
                "forward_classifier_entry_count": len(forward_entries),
                "forward_core_lfib_entry_count": len(forward_core_entries),
                "forward_handoff_entry_count": len(forward_handoff_entries),
                "return_entry_count": len(return_entries),
                "return_handoff_entry_count": len(return_handoff_entries),
                "forward_map_keys": [
                    {
                        "map_type": entry.get("map_type"),
                        "generation": entry.get("generation"),
                        "ingress_ifindex": entry.get("ingress_ifindex"),
                        "target_ip": entry.get("target_ip"),
                        "label": entry.get("label"),
                        "direction": entry.get("direction"),
                    }
                    for entry in [
                        *forward_entries,
                        *forward_core_entries,
                        *forward_handoff_entries,
                    ]
                ],
                "return_map_keys": [
                    {
                        "map_type": entry.get("map_type"),
                        "generation": entry.get("generation"),
                        "ingress_ifindex": entry.get("ingress_ifindex"),
                        "target_ip": entry.get("target_ip"),
                        "label": entry.get("label"),
                        "direction": entry.get("direction"),
                    }
                    for entry in return_entries[:6]
                ],
                "intermediate_core_lookup_uses_dst_ip": False,
            }
            expected_flows.append(flow_summary)
            if (
                not forward_entries
                or not forward_handoff_entries
                or (
                    len(context.forward_segments) > 1
                    and not forward_core_entries
                )
            ):
                missing_forward_entries.append(flow_summary)
            if not return_entries or not return_handoff_entries:
                missing_return_entries.append(flow_summary)

    maps_present = bool(
        dict(packet_counters.get("map_reference") or {}).get("kind") != "missing"
        and dict(active_generation_read.get("map_reference") or {}).get("kind")
        != "missing"
    )
    blockers: List[str] = []
    if int(attach_guard.get("attached_count") or 0) <= 0:
        blockers.append("bpf_programs_not_attached")
    if not maps_present:
        blockers.append("bpf_maps_missing")
    if not packet_counters_readable:
        blockers.append("bpf_packet_counters_not_readable")
    if not active_generation_readable:
        blockers.append("bpf_active_generation_not_readable")
    elif int(active_generation) != int(expected_generation):
        blockers.append("bpf_active_generation_mismatch")
    if not interface_roles_readable:
        blockers.append("bpf_interface_role_map_not_readable")
    elif (
        int(interface_role_completeness.get("expected_interface_count") or 0) > 0
        and interface_role_completeness.get("ok") is not True
    ):
        blockers.append("bpf_interface_role_map_incomplete")
    if missing_forward_entries:
        blockers.append("bpf_expected_forward_mpls_entries_missing")
    if missing_return_entries:
        blockers.append("bpf_expected_return_mpls_entries_missing")
    if core_entries_using_dst_ip:
        blockers.append("bpf_core_lfib_lookup_uses_dst_ip")
    if int(attach_guard.get("stale_ifindex_count") or 0) > 0:
        blockers.append("bpf_attached_ifindex_mismatch")

    return {
        "ok": not blockers,
        "failure_reason": "bpf_lifecycle_not_ready_for_validation"
        if blockers
        else "",
        "blockers": blockers,
        "bpf_attached_before_final_validation": int(
            attach_guard.get("attached_count") or 0
        ),
        "bpf_maps_present_before_final_validation": maps_present,
        "bpf_packet_counters_readable_before_final_validation": packet_counters_readable,
        "bpf_interface_roles_readable_before_final_validation": interface_roles_readable,
        "bpf_active_generation_before_final_validation": active_generation,
        "expected_final_generation": expected_generation,
        "bpf_cleanup_started_after_final_validation": False,
        "bpf_validation_lifecycle_order": [
            "incremental_cleanup_before_bpf_prepare",
            "bpf_prepare_attach_pin_populate_active_generation",
            "runtime_transaction_apply",
            "parameter_update_batch_apply",
            "canary_or_epoch_validation",
            "final_pre_rollback_lifecycle_guard",
            "final_pre_rollback_connectivity_validation",
            "bpf_cleanup_or_rollback_after_final_validation",
        ],
        "bpf_owned_packet_counters_before_validation": packet_counters,
        "bpf_active_generation_read_before_validation": active_generation_read,
        "bpf_interface_roles_before_validation": live_interface_roles,
        "interface_role_completeness_before_validation": interface_role_completeness,
        "bpf_attach_guard_before_final_validation": attach_guard,
        "expected_validation_flow_count": len(expected_flows),
        "missing_forward_entry_count": len(missing_forward_entries),
        "missing_return_entry_count": len(missing_return_entries),
        "missing_forward_entries": missing_forward_entries[:20],
        "missing_return_entries": missing_return_entries[:20],
        "core_entries_using_dst_ip": core_entries_using_dst_ip[:20],
        "intermediate_core_lookup_uses_dst_ip": bool(core_entries_using_dst_ip),
        "expected_validation_flows": expected_flows[:50],
    }


def _current_host_ifindex(interface_name: str) -> Optional[int]:
    if str(interface_name).startswith("netns:"):
        parts = str(interface_name).split(":", 2)
        if len(parts) != 3:
            return None
        _prefix, node_id, netns_interface = parts
        try:
            pid = str(_container_pid(node_id))
        except Exception:
            return None
        command = [
            "sudo",
            "-n",
            "nsenter",
            "-t",
            pid,
            "-n",
            "--",
            "ip",
            "-o",
            "link",
            "show",
            "dev",
            netns_interface,
        ]
        result = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2.0,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        prefix = result.stdout.split(":", 1)[0].strip()
        return _as_int(prefix, default=0) or None
    result = subprocess.run(
        ["ip", "-o", "link", "show", "dev", str(interface_name)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=2.0,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    prefix = result.stdout.split(":", 1)[0].strip()
    return _as_int(prefix, default=0) or None


def _bpf_interface_counters_for_ifindex(
    interface_counters: Dict[str, Any],
    ifindex: Any,
) -> Dict[str, int]:
    counters_by_ifindex = dict(interface_counters.get("counters_by_ifindex") or {})
    counters = dict(counters_by_ifindex.get(str(ifindex)) or {})
    return {str(key): int(value or 0) for key, value in counters.items()}


def _direction_order(direction: str) -> int:
    order = {
        "g_to_z_access_ingress": 0,
        "g_to_z": 1,
        "z_to_g_nat_ingress": 2,
        "z_to_g": 3,
    }
    return order.get(str(direction), 99)


def _bpf_owned_path_observability_summary(
    *,
    readiness: Dict[str, Any],
    packet_observation: Dict[str, Any],
    interface_counters: Dict[str, Any],
    failed_selectors: Sequence[str],
) -> Dict[str, Any]:
    attached_details = list(readiness.get("attached_interface_details") or [])
    if not attached_details:
        prepare = dict(
            dict(readiness.get("prepare_steps") or {}).get("real_backend_prepare")
            or {}
        )
        attached_details = list(prepare.get("attached_interface_details") or [])
    attached_by_name = {
        str(detail.get("interface_name")): dict(detail)
        for detail in attached_details
        if str(detail.get("interface_name") or "")
    }
    resolved_entries = list(readiness.get("resolved_mpls_flow_entries") or [])
    if not resolved_entries:
        prepare = dict(
            dict(readiness.get("prepare_steps") or {}).get("real_backend_prepare")
            or {}
        )
        resolved_entries = list(prepare.get("resolved_mpls_flow_entries") or [])
    if not resolved_entries:
        resolved_entries = list(readiness.get("resolved_packet_route_entries") or [])
    if not resolved_entries:
        resolved_entries = list(readiness.get("packet_route_entries") or [])

    observations = [
        dict(item)
        for item in list(packet_observation.get("observations") or [])
        if item.get("matched") is True
    ]
    observation_by_selector = {
        str(observation.get("selector")): observation for observation in observations
    }
    selector_summaries: List[Dict[str, Any]] = []
    total_expected_interfaces: set[str] = set()
    total_attached_expected_interfaces: set[str] = set()
    total_missing_interfaces: set[str] = set()
    stale_ifindex_detected = False
    first_missing_observation_point: Optional[Dict[str, Any]] = None
    per_interface_seen: Dict[str, int] = {}
    per_interface_redirect: Dict[str, int] = {}

    for selector in failed_selectors:
        if ":" not in selector:
            continue
        ut_id, target_ip = selector.split(":", 1)
        observation = observation_by_selector.get(selector, {})
        observed_flow_id = str(observation.get("flow_id") or "")
        target_entries = [
            dict(entry)
            for entry in resolved_entries
            if str(entry.get("ut_id") or "") == ut_id
            and (
                str(entry.get("target_ip") or "") == target_ip
                or (
                    observed_flow_id
                    and str(entry.get("flow_id") or "") == observed_flow_id
                    and str(entry.get("direction") or "").startswith("z_to_g")
                )
            )
            and (
                not observed_flow_id
                or str(entry.get("flow_id") or "") == observed_flow_id
            )
        ]
        target_entries.sort(key=lambda item: _direction_order(str(item.get("direction"))))
        expected_interfaces: Dict[str, Dict[str, Any]] = {}
        expected_path: List[Dict[str, Any]] = []
        for entry in target_entries:
            ingress_interface = str(entry.get("ingress_interface") or "")
            egress_interface = str(
                entry.get("egress_interface") or entry.get("out_interface") or ""
            )
            ingress_ifindex = entry.get("ingress_ifindex")
            egress_ifindex = entry.get("egress_ifindex", entry.get("out_ifindex"))
            path_entry = {
                "map_type": entry.get("map_type"),
                "direction": entry.get("direction"),
                "flow_id": entry.get("flow_id"),
                "ut_id": entry.get("ut_id"),
                "target_ip": entry.get("target_ip"),
                "selected_gateway_id": entry.get("selected_gateway_id"),
                "serving_satellite_id": entry.get("serving_satellite_id"),
                "slot_name": entry.get("slot_name"),
                "ingress_interface": ingress_interface,
                "ingress_ifindex": ingress_ifindex,
                "egress_interface": egress_interface,
                "egress_ifindex": egress_ifindex,
                "label": entry.get("label"),
                "initial_label": entry.get("initial_label"),
                "out_label": entry.get("out_label"),
                "new_dst_ip": entry.get("new_dst_ip"),
                "l2_rewrite": dict(entry.get("l2_rewrite") or {}),
                "segment": dict(entry.get("segment") or {}),
            }
            expected_path.append(path_entry)
            if ingress_interface:
                expected_interfaces.setdefault(
                    ingress_interface,
                    {
                        "interface_name": ingress_interface,
                        "ifindex": ingress_ifindex,
                        "expected_roles": [],
                    },
                )["expected_roles"].append(f"{entry.get('direction')}:ingress")
            if egress_interface:
                expected_interfaces.setdefault(
                    egress_interface,
                    {
                        "interface_name": egress_interface,
                        "ifindex": egress_ifindex,
                        "expected_roles": [],
                    },
                )["expected_roles"].append(f"{entry.get('direction')}:egress")

        interface_reports: List[Dict[str, Any]] = []
        for interface_name, expected in sorted(expected_interfaces.items()):
            total_expected_interfaces.add(interface_name)
            attached = interface_name in attached_by_name
            if attached:
                total_attached_expected_interfaces.add(interface_name)
            else:
                total_missing_interfaces.add(interface_name)
            attached_detail = dict(attached_by_name.get(interface_name) or {})
            expected_ifindex = (
                expected.get("ifindex")
                if expected.get("ifindex") is not None
                else attached_detail.get("ifindex")
            )
            current_ifindex = _current_host_ifindex(interface_name)
            stale = bool(
                expected_ifindex
                and current_ifindex
                and int(expected_ifindex) != int(current_ifindex)
            )
            stale_ifindex_detected = stale_ifindex_detected or stale
            counters = _bpf_interface_counters_for_ifindex(
                interface_counters,
                expected_ifindex,
            )
            seen_count = int(counters.get("seen") or 0)
            redirect_count = int(counters.get("redirect") or 0)
            per_interface_seen[interface_name] = seen_count
            per_interface_redirect[interface_name] = redirect_count
            interface_reports.append(
                {
                    "interface_name": interface_name,
                    "interface_ref": attached_detail.get("interface_ref")
                    or interface_name,
                    "container_interface_name": attached_detail.get(
                        "container_interface_name"
                    ),
                    "netns": attached_detail.get("netns") or "host",
                    "attached": attached,
                    "attach_directions": list(
                        attached_detail.get("attach_directions") or []
                    ),
                    "expected_roles": sorted(
                        {
                            *list(expected.get("expected_roles") or []),
                            *list(attached_detail.get("expected_roles") or []),
                        }
                    ),
                    "expected_ifindex": expected_ifindex,
                    "current_ifindex": current_ifindex,
                    "stale_ifindex": stale,
                    "bpf_seen_count": seen_count,
                    "bpf_redirect_count": redirect_count,
                    "bpf_counters": counters,
                }
            )
        if first_missing_observation_point is None:
            for path_entry in expected_path:
                interface_name = str(path_entry.get("ingress_interface") or "")
                if not interface_name:
                    continue
                if per_interface_seen.get(interface_name, 0) == 0:
                    first_missing_observation_point = {
                        "selector": selector,
                        "direction": path_entry.get("direction"),
                        "flow_id": path_entry.get("flow_id"),
                        "interface_name": interface_name,
                        "ifindex": path_entry.get("ingress_ifindex"),
                        "reason": (
                            "interface_not_attached"
                            if interface_name not in attached_by_name
                            else "attached_interface_no_bpf_seen_packets"
                        ),
                    }
                    break
        selector_summaries.append(
            {
                "selector": selector,
                "observed_flow_id": observed_flow_id or None,
                "actual_packet_path": {
                    "source_ut_netns": ut_id,
                    "path_string": observation.get("path_string"),
                    "selected_gateway_id": observation.get("selected_gateway_id"),
                    "serving_satellite_id": observation.get("serving_satellite_id"),
                    "target_ip_address": observation.get("target_ip_address", target_ip),
                    "interface_counter_deltas": list(
                        observation.get("interface_counter_deltas") or []
                    ),
                },
                "expected_bpf_path": expected_path,
                "expected_path_interfaces": interface_reports,
            }
        )

    return {
        "schema_version": "skyseed-bpf-owned-path-observability-v1",
        "expected_path_interface_count": len(total_expected_interfaces),
        "attached_expected_path_interface_count": len(
            total_attached_expected_interfaces
        ),
        "missing_attach_interfaces": sorted(total_missing_interfaces),
        "per_interface_bpf_seen_count": dict(sorted(per_interface_seen.items())),
        "per_interface_redirect_count": dict(sorted(per_interface_redirect.items())),
        "first_missing_observation_point": first_missing_observation_point,
        "stale_ifindex_detected": stale_ifindex_detected,
        "selector_summaries": selector_summaries,
    }


def _bpf_owned_map_load_summary(readiness: Dict[str, Any]) -> Dict[str, Any]:
    prepare = dict(
        dict(readiness.get("prepare_steps") or {}).get("real_backend_prepare") or {}
    )
    map_load = dict(dict(prepare.get("steps") or {}).get("map_load") or {})
    mpls_counts = dict(readiness.get("mpls_map_entry_counts") or {})
    return {
        "loaded_fec_count": int(map_load.get("loaded_fec_count") or 0),
        "loaded_generation_count": int(
            map_load.get("loaded_generation_count")
            or prepare.get("loaded_runtime_generation_count")
            or 0
        ),
        "loaded_dataplane_route_count": int(
            map_load.get("loaded_dataplane_route_count") or 0
        ),
        "loaded_dataplane_parameter_entry_count": int(
            map_load.get("loaded_dataplane_parameter_entry_count") or 0
        ),
        "loaded_parameter_entry_count": int(
            map_load.get("loaded_parameter_entry_count") or 0
        ),
        "target_ips": list(map_load.get("target_ips") or []),
        "loaded_dataplane_route_keys": list(
            map_load.get("loaded_dataplane_route_keys") or []
        ),
        "loaded_classifier_count": int(
            map_load.get("loaded_classifier_count")
            or prepare.get("loaded_classifier_count")
            or mpls_counts.get("ingress_classifier_map")
            or 0
        ),
        "loaded_core_lfib_count": int(
            map_load.get("loaded_core_lfib_count")
            or prepare.get("loaded_core_lfib_count")
            or mpls_counts.get("core_lfib_map")
            or 0
        ),
        "loaded_egress_handoff_count": int(
            map_load.get("loaded_egress_handoff_count")
            or prepare.get("loaded_egress_handoff_count")
            or mpls_counts.get("egress_handoff_map")
            or 0
        ),
        "loaded_interface_role_count": int(
            map_load.get("loaded_interface_role_count")
            or prepare.get("loaded_interface_role_count")
            or 0
        ),
        "loaded_classifier_keys": list(map_load.get("loaded_classifier_keys") or []),
        "loaded_core_lfib_keys": list(map_load.get("loaded_core_lfib_keys") or []),
        "loaded_egress_handoff_keys": list(
            map_load.get("loaded_egress_handoff_keys") or []
        ),
        "loaded_interface_roles": list(
            map_load.get("loaded_interface_roles") or []
        ),
        "loaded_interface_role_sources": list(
            map_load.get("loaded_interface_role_sources") or []
        ),
        "interface_role_completeness": _bpf_owned_interface_role_completeness(
            readiness
        ),
        "active_generation": map_load.get(
            "active_generation",
            prepare.get("active_generation", readiness.get("active_generation")),
        ),
        "intermediate_core_lookup_uses_dst_ip": bool(
            map_load.get(
                "intermediate_core_lookup_uses_dst_ip",
                readiness.get("intermediate_core_lookup_uses_dst_ip", False),
            )
        ),
        "linux_gz_fallback_enabled": bool(
            readiness.get("linux_gz_fallback_enabled", False)
        ),
        "eligible_core_fallback_to_linux": int(
            readiness.get("eligible_core_fallback_to_linux") or 0
        ),
        "skipped_by_bpf_backend": list(readiness.get("skipped_by_bpf_backend") or []),
        "mpls_map_entry_counts": mpls_counts,
        "capacity_errors": list(map_load.get("capacity_errors") or []),
    }


def _bpf_owned_expected_role_bits(expected_roles: Sequence[Any]) -> int:
    bits = 0
    for raw_role in list(expected_roles or []):
        role_name = str(raw_role or "").lower()
        if "classifier" in role_name:
            bits |= BPF_MPLS_INTERFACE_ROLE_CLASSIFIER
        if "mpls" in role_name or "lfib" in role_name or "handoff" in role_name:
            bits |= BPF_MPLS_INTERFACE_ROLE_MPLS
    return bits


def _bpf_owned_role_names(bits: int) -> List[str]:
    names: List[str] = []
    if bits & BPF_MPLS_INTERFACE_ROLE_CLASSIFIER:
        names.append("classifier")
    if bits & BPF_MPLS_INTERFACE_ROLE_MPLS:
        names.append("mpls")
    unknown_bits = bits & ~(BPF_MPLS_INTERFACE_ROLE_CLASSIFIER | BPF_MPLS_INTERFACE_ROLE_MPLS)
    if unknown_bits:
        names.append(f"unknown_bits:{unknown_bits}")
    return names


def _bpf_owned_interface_role_completeness(readiness: Dict[str, Any]) -> Dict[str, Any]:
    prepare = dict(
        dict(readiness.get("prepare_steps") or {}).get("real_backend_prepare") or {}
    )
    map_load = dict(dict(prepare.get("steps") or {}).get("map_load") or {})
    live_interface_roles = dict(readiness.get("live_interface_roles") or {})
    if not live_interface_roles:
        live_interface_roles = dict(
            dict(prepare.get("steps") or {}).get("live_interface_roles_after_map_load")
            or {}
        )
    loaded_roles = list(live_interface_roles.get("entries") or [])
    role_source = "live_interface_role_map"
    if not loaded_roles:
        loaded_roles = list(map_load.get("loaded_interface_roles") or [])
        role_source = "map_load_reported_roles"
    if not loaded_roles:
        loaded_roles = list(prepare.get("loaded_interface_roles") or [])
        role_source = "prepare_reported_roles"
    attached_details = list(readiness.get("attached_interface_details") or [])
    if not attached_details:
        attached_details = list(prepare.get("attached_interface_details") or [])
    loaded_by_ifindex: Dict[int, int] = {
        int(role.get("ifindex") or 0): int(role.get("role") or 0)
        for role in loaded_roles
        if int(role.get("ifindex") or 0) > 0
    }
    expected_rows: List[Dict[str, Any]] = []
    incomplete_rows: List[Dict[str, Any]] = []
    for detail in attached_details:
        attached = dict(detail or {})
        expected_bits = _bpf_owned_expected_role_bits(
            list(attached.get("expected_roles") or [])
        )
        if expected_bits == 0:
            continue
        ifindex = int(attached.get("ifindex") or 0)
        loaded_bits = int(loaded_by_ifindex.get(ifindex, 0)) if ifindex > 0 else 0
        missing_bits = expected_bits & ~loaded_bits
        row = {
            "interface_ref": attached.get("interface_ref"),
            "namespace": attached.get("namespace"),
            "interface": attached.get("interface"),
            "ifindex": ifindex,
            "expected_role_bits": expected_bits,
            "expected_roles": list(attached.get("expected_roles") or []),
            "expected_role_names": _bpf_owned_role_names(expected_bits),
            "loaded_role_bits": loaded_bits,
            "loaded_role_names": _bpf_owned_role_names(loaded_bits),
            "missing_role_bits": missing_bits,
            "missing_role_names": _bpf_owned_role_names(missing_bits),
        }
        expected_rows.append(row)
        if ifindex <= 0 or missing_bits:
            incomplete_rows.append(row)
    return {
        "ok": len(incomplete_rows) == 0,
        "role_source": role_source,
        "live_role_read_ok": live_interface_roles.get("ok"),
        "expected_interface_count": len(expected_rows),
        "loaded_interface_role_count": len(loaded_by_ifindex),
        "incomplete_interface_count": len(incomplete_rows),
        "incomplete_interfaces": incomplete_rows,
    }


def _bpf_owned_sample_key_matches_path(
    sample: Dict[str, Any],
    path_entry: Dict[str, Any],
) -> bool:
    lookup_key = dict(sample.get("lookup_key") or {})
    map_name = str(sample.get("map_that_missed") or "")
    path_map = str(path_entry.get("map_type") or "")
    if map_name not in {"unknown", path_map} and not (
        map_name in {"core_lfib_map", "egress_handoff_map"}
        and path_map in {"core_lfib_map", "egress_handoff_map"}
    ):
        return False
    if int(lookup_key.get("ingress_ifindex") or 0) != int(
        path_entry.get("ingress_ifindex") or 0
    ):
        return False
    if path_map == "ingress_classifier_map":
        return str(lookup_key.get("dst_ip") or "") == str(
            path_entry.get("target_ip") or ""
        )
    if path_map in {"core_lfib_map", "egress_handoff_map"}:
        expected_label = path_entry.get("label")
        if expected_label is None:
            expected_label = path_entry.get("initial_label")
        return int(lookup_key.get("label") or 0) == int(expected_label or 0)
    return False


def _bpf_owned_miss_sample_attribution(
    *,
    readiness: Dict[str, Any],
    path_observability: Dict[str, Any],
    miss_samples: Dict[str, Any],
) -> Dict[str, Any]:
    attached_details = list(readiness.get("attached_interface_details") or [])
    if not attached_details:
        prepare = dict(
            dict(readiness.get("prepare_steps") or {}).get("real_backend_prepare")
            or {}
        )
        attached_details = list(prepare.get("attached_interface_details") or [])
    attached_by_ifindex = {
        int(detail.get("ifindex")): dict(detail)
        for detail in attached_details
        if detail.get("ifindex") is not None
    }
    selector_summaries = list(path_observability.get("selector_summaries") or [])
    rows: List[Dict[str, Any]] = []
    for sample in list(miss_samples.get("samples") or []):
        sample_dict = dict(sample)
        ifindex = int(sample_dict.get("ifindex") or 0)
        interface_detail = dict(attached_by_ifindex.get(ifindex) or {})
        matching_selectors: List[str] = []
        for selector_summary in selector_summaries:
            summary = dict(selector_summary or {})
            for path_entry in list(summary.get("expected_bpf_path") or []):
                if _bpf_owned_sample_key_matches_path(sample_dict, dict(path_entry)):
                    matching_selectors.append(str(summary.get("selector")))
                    break
        role_bits = int(sample_dict.get("interface_role_bits") or 0)
        reason = str(sample_dict.get("reason") or "")
        if matching_selectors:
            traffic_class = "selected_acceptance_probe"
        elif reason in {"missing_interface_role", "unknown_interface_role"}:
            traffic_class = "role_binding_blocker"
        elif bool(sample_dict.get("gz_scope")):
            traffic_class = "non_selected_validation_or_gz_scope_traffic"
        else:
            traffic_class = "unrelated_control_or_background_traffic"
        strict_blocker = bool(
            reason in {"missing_interface_role", "unknown_interface_role"}
            or (
                sample_dict.get("map_that_missed") == "ingress_classifier_map"
                and role_bits & 1
            )
            or (
                sample_dict.get("has_mpls_shim")
                and sample_dict.get("map_that_missed")
                in {"core_lfib_map", "egress_handoff_map"}
            )
        )
        rows.append(
            {
                **sample_dict,
                "interface_name": interface_detail.get("interface_name"),
                "container_interface_name": interface_detail.get(
                    "container_interface_name"
                ),
                "interface_roles_expected": list(
                    interface_detail.get("expected_roles") or []
                ),
                "attach_directions": list(
                    interface_detail.get("attach_directions") or []
                ),
                "matching_selected_acceptance_selectors": matching_selectors,
                "traffic_class": traffic_class,
                "strict_blocker": strict_blocker,
            }
        )
    return {
        "schema_version": "skyseed-bpf-owned-miss-sample-attribution-v1",
        "sample_count": len(rows),
        "samples": rows,
        "strict_blocker_count": sum(1 for row in rows if row.get("strict_blocker")),
        "traffic_classes": {
            traffic_class: sum(
                1 for row in rows if row.get("traffic_class") == traffic_class
            )
            for traffic_class in sorted(
                {str(row.get("traffic_class")) for row in rows}
            )
        },
    }


def _bpf_owned_counter_artifact_fields(
    bpf_counters: Dict[str, Any],
) -> Dict[str, Any]:
    counters = dict(bpf_counters.get("counters") or {})
    classifier_miss_count = (
        int(counters.get("miss") or 0)
        + int(counters.get("drop_missing_label") or 0)
        + int(counters.get("drop_missing_generation") or 0)
        + int(counters.get("drop_bad_packet") or 0)
        + int(counters.get("drop") or 0)
    )
    return {
        "bpf_classifier_miss_count": classifier_miss_count,
        "bpf_ping_path_hit_count": int(counters.get("core_lfib_hit") or 0)
        + int(counters.get("redirect") or 0),
        "bpf_return_handoff_hit_count": int(
            counters.get("egress_handoff_hit") or 0
        ),
        "classifier_hit": int(counters.get("classifier_hit") or 0),
        "label_push": int(counters.get("label_push") or 0),
        "core_lfib_hit": int(counters.get("core_lfib_hit") or 0),
        "label_swap": int(counters.get("label_swap") or 0),
        "egress_handoff_hit": int(counters.get("egress_handoff_hit") or 0),
        "label_pop": int(counters.get("label_pop") or 0),
        "miss_drop_counters": {
            "miss": int(counters.get("miss") or 0),
            "drop_missing_label": int(counters.get("drop_missing_label") or 0),
            "drop_missing_generation": int(
                counters.get("drop_missing_generation") or 0
            ),
            "drop_bad_packet": int(counters.get("drop_bad_packet") or 0),
            "drop": int(counters.get("drop") or 0),
        },
    }


def _classify_bpf_owned_packet_path_blocker(
    *,
    readiness: Dict[str, Any],
    bpf_counters: Dict[str, Any],
) -> str:
    map_summary = _bpf_owned_map_load_summary(readiness)
    counters = dict(bpf_counters.get("counters") or {})
    if int(map_summary["loaded_classifier_count"]) == 0:
        return "bpf_mpls_classifier_map_empty"
    if int(map_summary["loaded_core_lfib_count"]) == 0:
        return "bpf_mpls_core_lfib_map_empty"
    if int(map_summary["loaded_egress_handoff_count"]) == 0:
        return "bpf_mpls_egress_handoff_map_empty"
    if sum(int(value or 0) for value in counters.values()) == 0:
        return "packet_not_observed_by_attached_bpf_programs"
    if int(counters.get("miss") or 0) > 0:
        return "bpf_mpls_map_miss"
    if int(counters.get("drop_missing_label") or 0) > 0:
        return "bpf_mpls_missing_label_strict_drop"
    if int(counters.get("drop_missing_generation") or 0) > 0:
        return "bpf_mpls_missing_generation_strict_drop"
    if int(counters.get("drop_bad_packet") or 0) > 0:
        return "bpf_mpls_bad_packet_strict_drop"
    if int(counters.get("redirect") or 0) > 0 and int(counters.get("drop") or 0) == 0:
        return "redirect_happened_but_downstream_path_failed"
    return "bpf_owned_mpls_packet_path_unclassified"


def _bpf_owned_packet_path_diagnostics(
    *,
    installer: "MinimalRealLinuxMplsInstaller",
    session: ActiveRuntimeSession,
    failed_ping_results: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    readiness = dict(dict(session.backend or {}).get("bpf_owned_readiness") or {})
    before_counters = _read_bpf_owned_packet_counters()
    before_miss_samples = _read_bpf_owned_miss_samples()
    failed_selectors = []
    for result in failed_ping_results:
        if result.get("success") is True:
            continue
        selector = f"{result.get('ut_id')}:{result.get('ping_target_ip')}"
        if selector not in failed_selectors:
            failed_selectors.append(selector)
    packet_observation = {
        "enabled": False,
        "reason": "no_failed_ping_selector",
        "observations": [],
    }
    if failed_selectors:
        try:
            packet_observation = installer.observe_packet_path(
                session,
                observe_selectors=failed_selectors[:1],
            )
        except Exception as exc:  # pragma: no cover - diagnostic best effort.
            packet_observation = {
                "enabled": False,
                "reason": "packet_observation_failed",
                "error": str(exc),
                "observations": [],
            }
    after_counters = _read_bpf_owned_packet_counters()
    interface_counters = _read_bpf_owned_interface_counters()
    miss_samples = _read_bpf_owned_miss_samples()
    path_observability = _bpf_owned_path_observability_summary(
        readiness=readiness,
        packet_observation=packet_observation,
        interface_counters=interface_counters,
        failed_selectors=failed_selectors,
    )
    pre_observation_miss_sample_attribution = _bpf_owned_miss_sample_attribution(
        readiness=readiness,
        path_observability=path_observability,
        miss_samples=before_miss_samples,
    )
    miss_sample_attribution = _bpf_owned_miss_sample_attribution(
        readiness=readiness,
        path_observability=path_observability,
        miss_samples=miss_samples,
    )
    counter_fields = _bpf_owned_counter_artifact_fields(
        after_counters if after_counters.get("ok") else before_counters
    )
    return {
        "schema_version": "skyseed-bpf-owned-packet-path-diagnostics-v1",
        "map_load_summary": _bpf_owned_map_load_summary(readiness),
        "bpf_counters_before_observation": before_counters,
        "bpf_miss_samples_before_observation": before_miss_samples,
        "bpf_counters_after_observation": after_counters,
        "bpf_interface_counters_after_observation": interface_counters,
        "bpf_miss_samples_after_observation": miss_samples,
        "pre_observation_miss_drop_sample_attribution": (
            pre_observation_miss_sample_attribution
        ),
        "miss_drop_sample_attribution": miss_sample_attribution,
        **counter_fields,
        "packet_observation": packet_observation,
        "path_observability": path_observability,
        "expected_path_interface_count": path_observability[
            "expected_path_interface_count"
        ],
        "attached_expected_path_interface_count": path_observability[
            "attached_expected_path_interface_count"
        ],
        "missing_attach_interfaces": path_observability["missing_attach_interfaces"],
        "per_interface_bpf_seen_count": path_observability[
            "per_interface_bpf_seen_count"
        ],
        "per_interface_redirect_count": path_observability[
            "per_interface_redirect_count"
        ],
        "first_missing_observation_point": path_observability[
            "first_missing_observation_point"
        ],
        "stale_ifindex_detected": path_observability["stale_ifindex_detected"],
        "failed_selectors": failed_selectors,
        "blocker": _classify_bpf_owned_packet_path_blocker(
            readiness=readiness,
            bpf_counters=after_counters if after_counters.get("ok") else before_counters,
        ),
    }


def _derive_bpf_owned_readiness_proof(
    *,
    runtime_update_plan: RuntimeUpdatePlan,
) -> Dict[str, Any]:
    intent = _bpf_owned_intent_payload(runtime_update_plan)
    gz_intent = intent["gz_fec_intent"]
    generation_id, generation_source = _bpf_owned_runtime_generation_id(
        runtime_update_plan
    )
    g_to_z_entries: List[Dict[str, Any]] = []
    z_to_g_entries: List[Dict[str, Any]] = []
    for fec in gz_intent.get("forward_fecs", []) or []:
        fec_id = str(fec.get("fec_id") or "")
        if not fec_id:
            continue
        g_to_z_entries.append(
            {
                "generation_id": generation_id,
                "fec_id": fec_id,
                "fec_kind": "G",
                "gateway_class_id": fec.get("gateway_class_id"),
                "selected_gateway_id": fec.get("selected_gateway_id"),
                "zone_id": fec.get("zone_id"),
                "responsible_satellite_id": (
                    fec.get("serving_satellite_id")
                    or fec.get("selected_gateway_satellite_id")
                ),
                "satellite_path": list(fec.get("satellite_path", []) or []),
                "link_ids": list(fec.get("forward_link_ids", []) or []),
                "flow_ids": list(fec.get("flow_ids", []) or []),
                "ut_ids": list(fec.get("ut_ids", []) or []),
                "next_hop": {
                    "logical_node": fec.get("selected_gateway_id"),
                    "logical_edge": (
                        list(fec.get("forward_link_ids", []) or [None])[0]
                    ),
                    "basis": "forward_link_ids and selected gateway",
                },
            }
        )
    for fec in gz_intent.get("return_fecs", []) or []:
        fec_id = str(fec.get("fec_id") or "")
        if not fec_id:
            continue
        z_to_g_entries.append(
            {
                "generation_id": generation_id,
                "fec_id": fec_id,
                "fec_kind": "GZ",
                "gateway_class_id": fec.get("gateway_class_id"),
                "selected_gateway_id": fec.get("selected_gateway_id"),
                "zone_id": fec.get("zone_id"),
                "responsible_satellite_id": (
                    fec.get("zone_serving_satellite_id")
                    or fec.get("pop_point")
                    or fec.get("selected_gateway_satellite_id")
                ),
                "satellite_path": list(fec.get("satellite_path", []) or []),
                "link_ids": list(fec.get("reverse_link_ids", []) or []),
                "return_prefixes": list(fec.get("return_prefixes", []) or []),
                "flow_ids": list(fec.get("flow_ids", []) or []),
                "ut_ids": list(fec.get("ut_ids", []) or []),
                "next_hop": {
                    "logical_node": (
                        fec.get("zone_serving_satellite_id")
                        or fec.get("pop_point")
                        or fec.get("selected_gateway_satellite_id")
                    ),
                    "logical_edge": (
                        list(fec.get("reverse_link_ids", []) or [None])[0]
                    ),
                    "basis": "reverse_link_ids and zone responsible satellite",
                },
            }
        )
    entries = [*g_to_z_entries, *z_to_g_entries]
    map_entry_count = len(entries)
    attached_interfaces = _bpf_owned_attached_interfaces_from_entries(entries)
    intent_hash = _sha256(intent)
    map_entry_hash = _bpf_owned_entry_hash(entries)
    fallback_reason = ""
    if not g_to_z_entries:
        fallback_reason = "bpf_owned_no_g_to_z_fec_entries"
    elif not z_to_g_entries:
        fallback_reason = "bpf_owned_no_z_to_g_fec_entries"
    elif not attached_interfaces:
        fallback_reason = "bpf_owned_no_attachable_core_interfaces"

    logical_intent_ready = fallback_reason == ""
    return {
        "schema_version": BPF_OWNED_PROOF_SCHEMA_VERSION,
        "provider": "linux_mpls_runtime.pre_apply_bpf_owned_provider",
        "logical_intent_ready": logical_intent_ready,
        "ready": False,
        "ok": False,
        "reason": (
            "bpf_owned_prepare_not_run"
            if logical_intent_ready
            else fallback_reason
        ),
        "fallback_reason": (
            "bpf_owned_prepare_not_run"
            if logical_intent_ready
            else fallback_reason
        ),
        "fallback_to_linux": True,
        "runtime_epoch_intent": {
            **dict(intent["runtime_epoch_intent"]),
            "intent_hash": intent_hash,
        },
        "intent_hash": intent_hash,
        "generation_id": generation_id,
        "generation_source": generation_source,
        "active_generation": generation_id,
        "runtime_generation_loaded": False,
        "active_generation_set": False,
        "map_ready": False,
        "real_backend_prepared": False,
        "counter_ready_required": False,
        "eligible_satellite_core_gz_scope": {
            "scope": "satellite_core_gz_forwarding",
            "eligible_command_scopes": list(BPF_OWNED_ELIGIBLE_SCOPES),
            "g_to_z_entry_count": len(g_to_z_entries),
            "z_to_g_entry_count": len(z_to_g_entries),
            "entry_count": map_entry_count,
        },
        "g_to_z_entries": g_to_z_entries,
        "z_to_g_entries": z_to_g_entries,
        "map_entry_count": map_entry_count,
        "map_entry_hash": map_entry_hash,
        "attached_interfaces": attached_interfaces,
        "prepare_steps": {
            "compile": {
                "ok": False,
                "mode": "deterministic_intent_compile",
                "pending": logical_intent_ready,
                "source": "RuntimeUpdatePlan -> temporal_planning.gz_fec_plan",
            },
            "load": {
                "ok": False,
                "mode": "pre_apply_map_population",
                "pending": logical_intent_ready,
                "active_generation": generation_id,
                "map_entry_count": map_entry_count,
                "map_entry_hash": map_entry_hash,
            },
            "attach": {
                "ok": False,
                "mode": "pre_apply_core_interface_attachment",
                "pending": logical_intent_ready,
                "attached_interfaces": attached_interfaces,
            },
        },
    }


def _bpf_owned_readiness_mismatch_reason(
    *,
    runtime_update_plan: RuntimeUpdatePlan,
    readiness: Dict[str, Any],
) -> str:
    if readiness.get("ready") is not True:
        return str(readiness.get("reason") or "bpf_owned_readiness_not_proven")
    if readiness.get("schema_version") != BPF_OWNED_PROOF_SCHEMA_VERSION:
        return "bpf_owned_readiness_schema_mismatch"
    expected_intent = _bpf_owned_intent_payload(runtime_update_plan)
    expected_intent_hash = _sha256(expected_intent)
    if str(readiness.get("intent_hash") or "") != expected_intent_hash:
        return "bpf_owned_readiness_intent_hash_mismatch"
    runtime_epoch_intent = dict(readiness.get("runtime_epoch_intent") or {})
    if str(runtime_epoch_intent.get("intent_hash") or "") != expected_intent_hash:
        return "bpf_owned_readiness_runtime_epoch_intent_mismatch"
    expected_gz = expected_intent["gz_fec_intent"]
    expected_g_to_z = len(list(expected_gz.get("forward_fecs", []) or []))
    expected_z_to_g = len(list(expected_gz.get("return_fecs", []) or []))
    scope = dict(readiness.get("eligible_satellite_core_gz_scope") or {})
    if int(scope.get("g_to_z_entry_count") or -1) != expected_g_to_z:
        return "bpf_owned_readiness_g_to_z_scope_mismatch"
    if int(scope.get("z_to_g_entry_count") or -1) != expected_z_to_g:
        return "bpf_owned_readiness_z_to_g_scope_mismatch"
    entries = [
        *list(readiness.get("g_to_z_entries", []) or []),
        *list(readiness.get("z_to_g_entries", []) or []),
    ]
    if int(readiness.get("map_entry_count") or -1) != len(entries):
        return "bpf_owned_readiness_map_entry_count_mismatch"
    if str(readiness.get("map_entry_hash") or "") != _bpf_owned_entry_hash(entries):
        return "bpf_owned_readiness_map_entry_hash_mismatch"
    if int(readiness.get("active_generation", -1)) != int(
        readiness.get("generation_id", -2)
    ):
        return "bpf_owned_readiness_active_generation_mismatch"
    if not readiness.get("runtime_generation_loaded"):
        return "bpf_owned_readiness_generation_not_loaded"
    if not readiness.get("map_ready"):
        return "bpf_owned_readiness_map_not_ready"
    if not readiness.get("active_generation_set"):
        return "bpf_owned_readiness_active_generation_not_set"
    interface_role_completeness = _bpf_owned_interface_role_completeness(readiness)
    if (
        int(interface_role_completeness.get("expected_interface_count") or 0) > 0
        and interface_role_completeness.get("ok") is not True
    ):
        return "bpf_owned_interface_role_map_incomplete"
    if len(list(readiness.get("attached_interfaces") or [])) == 0:
        return "bpf_owned_readiness_no_attached_interfaces"
    prepare_steps = dict(readiness.get("prepare_steps") or {})
    real_backend_prepare = dict(prepare_steps.get("real_backend_prepare") or {})
    if (
        readiness.get("real_backend_prepared") is not True
        or real_backend_prepare.get("ok") is not True
    ):
        return "bpf_owned_real_prepare_not_proven"
    return ""


def _prove_bpf_owned_readiness(
    *,
    runtime_update_plan: RuntimeUpdatePlan,
    bpf_owned_readiness: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if bpf_owned_readiness:
        readiness = dict(bpf_owned_readiness)
        mismatch = _bpf_owned_readiness_mismatch_reason(
            runtime_update_plan=runtime_update_plan,
            readiness={**readiness, "ready": bool(readiness.get("ready") or readiness.get("ok"))},
        )
        if mismatch:
            return {
                **readiness,
                "ready": False,
                "ok": False,
                "reason": str(readiness.get("reason") or mismatch),
                "fallback_reason": str(readiness.get("fallback_reason") or mismatch),
                "fallback_to_linux": True,
                "readiness_mismatch_reason": mismatch,
            }
        return {
            **readiness,
            "ready": True,
            "ok": True,
            "reason": str(readiness.get("reason") or ""),
            "fallback_reason": "",
            "fallback_to_linux": False,
        }
    proof = _derive_bpf_owned_readiness_proof(
        runtime_update_plan=runtime_update_plan,
    )
    mismatch = _bpf_owned_readiness_mismatch_reason(
        runtime_update_plan=runtime_update_plan,
        readiness=proof,
    )
    if mismatch:
        return {
            **proof,
            "ready": False,
            "ok": False,
            "reason": mismatch,
            "fallback_reason": mismatch,
            "fallback_to_linux": True,
            "readiness_mismatch_reason": mismatch,
        }
    return proof


def _normalize_forwarding_backend_config(
    *,
    forwarding_backend: str = "linux_mpls",
    bpf_owned_readiness: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    backend_mode = str(forwarding_backend or "linux_mpls")
    if backend_mode not in FORWARDING_BACKEND_MODES:
        raise RuntimeError(
            "unsupported forwarding backend "
            f"{backend_mode!r}; expected one of {', '.join(FORWARDING_BACKEND_MODES)}"
        )
    readiness = dict(bpf_owned_readiness or {})
    has_structured_proof = (
        readiness.get("schema_version") == BPF_OWNED_PROOF_SCHEMA_VERSION
    )
    ready = bool(
        (readiness.get("ok") is True or readiness.get("ready") is True)
        and (backend_mode != "bpf_owned" or has_structured_proof)
        and not readiness.get("readiness_mismatch_reason")
        and not readiness.get("fallback_reason")
    )
    reason = str(
        readiness.get("reason")
        or readiness.get("failure_reason")
        or (
            "bpf_owned_readiness_proof_missing"
            if backend_mode == "bpf_owned" and not has_structured_proof
            else ""
        )
        or (
            "bpf_owned_readiness_not_proven"
            if backend_mode == "bpf_owned" and not ready
            else ""
        )
    )
    return {
        "backend_mode": backend_mode,
        "linux_backend_mode": "linux_mpls",
        "bpf_owned_readiness": {
            **readiness,
            "ready": ready,
            "reason": reason,
        },
        "fail_closed": backend_mode == "bpf_owned",
        "eligible_scopes": list(BPF_OWNED_ELIGIBLE_SCOPES),
    }


def _core_direction_for_spec(spec: RuntimeCommandSpec) -> str:
    text = f"{spec.description} {' '.join(spec.command)}".lower()
    if "reverse" in text or "zone-return" in text or "zone return" in text:
        return "z_to_g_core"
    if "forward" in text or "policy-table default" in text or "encap mpls" in text:
        return "g_to_z_core"
    if "mpls" in text:
        return "core_mpls_readiness"
    return "not_satellite_core_forwarding"


def _classify_backend_ownership(
    spec: RuntimeCommandSpec,
    *,
    backend_config: Dict[str, Any],
) -> Dict[str, Any]:
    scope_info = classify_node_local_command_scope(
        spec.command,
        description=spec.description,
        phase=spec.phase,
    )
    scope = str(scope_info.get("scope") or "other_unclassified")
    backend_mode = str(backend_config.get("backend_mode") or "linux_mpls")
    eligible = scope in set(BPF_OWNED_ELIGIBLE_SCOPES)
    readiness = dict(backend_config.get("bpf_owned_readiness") or {})
    readiness_ok = bool(readiness.get("ready") is True)
    readiness_reason = str(readiness.get("reason") or "")
    direction = _core_direction_for_spec(spec) if eligible else "not_satellite_core_forwarding"

    if not eligible:
        return {
            "scope": scope,
            "eligible_for_bpf_owned": False,
            "core_direction": direction,
            "ownership_status": "host_required",
            "apply_action": "retained_host_required",
            "skip": False,
            "reason": "outside_bpf_owned_satellite_core_scope",
        }
    if backend_mode == "linux_mpls":
        return {
            "scope": scope,
            "eligible_for_bpf_owned": True,
            "core_direction": direction,
            "ownership_status": "linux_owned",
            "apply_action": "applied_by_linux",
            "skip": False,
            "reason": "",
        }
    if backend_mode in {"bpf_shadow", "bpf_canary"}:
        return {
            "scope": scope,
            "eligible_for_bpf_owned": True,
            "core_direction": direction,
            "ownership_status": "shadow_only",
            "apply_action": "applied_by_linux_shadow_or_canary_only",
            "skip": False,
            "reason": f"{backend_mode}_does_not_replace_host_apply",
        }
    if backend_mode == "bpf_owned" and readiness_ok:
        return {
            "scope": scope,
            "eligible_for_bpf_owned": True,
            "core_direction": direction,
            "ownership_status": "bpf_owned",
            "apply_action": "skipped_by_bpf_backend",
            "skip": True,
            "reason": "bpf_owned_readiness_proven_strict_no_linux_gz_fallback",
        }
    return {
        "scope": scope,
        "eligible_for_bpf_owned": True,
        "core_direction": direction,
        "ownership_status": (
            "bpf_owned_planner_error"
            if backend_mode == "bpf_owned"
            else "fallback_to_linux"
        ),
        "apply_action": (
            "blocked_before_runtime"
            if backend_mode == "bpf_owned"
            else "fallback_to_linux"
        ),
        "skip": backend_mode == "bpf_owned",
        "reason": readiness_reason or "bpf_owned_readiness_not_proven",
    }


def _plan_backend_apply_contract(
    specs: Sequence[RuntimeCommandSpec],
    *,
    backend_config: Dict[str, Any],
    contract_phase: str,
) -> Tuple[List[RuntimeCommandSpec], Dict[str, Any]]:
    kept_specs: List[RuntimeCommandSpec] = []
    skipped_specs: List[RuntimeCommandSpec] = []
    counts_by_status: Dict[str, int] = {}
    counts_by_action: Dict[str, int] = {}
    counts_by_scope: Dict[str, int] = {}
    core_direction_counts: Dict[str, int] = {}
    fallback_reasons: Dict[str, int] = {}
    planner_error_reasons: Dict[str, int] = {}
    command_reports: List[Dict[str, Any]] = []

    for spec in specs:
        decision = _classify_backend_ownership(spec, backend_config=backend_config)
        annotated = replace(
            spec,
            ownership_status=str(decision["ownership_status"]),
            apply_action=str(decision["apply_action"]),
            backend_scope=str(decision["scope"]),
            skip_reason=str(decision["reason"]),
        )
        status = annotated.ownership_status
        action = annotated.apply_action
        scope = annotated.backend_scope
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        counts_by_action[action] = counts_by_action.get(action, 0) + 1
        counts_by_scope[scope] = counts_by_scope.get(scope, 0) + 1
        if bool(decision.get("eligible_for_bpf_owned")):
            direction = str(decision.get("core_direction") or "unknown_core_direction")
            core_direction_counts[direction] = core_direction_counts.get(direction, 0) + 1
        if status == "fallback_to_linux":
            reason = str(decision.get("reason") or "unknown")
            fallback_reasons[reason] = fallback_reasons.get(reason, 0) + 1
        if status == "bpf_owned_planner_error":
            reason = str(decision.get("reason") or "unknown")
            planner_error_reasons[reason] = planner_error_reasons.get(reason, 0) + 1
        if bool(decision.get("skip")):
            skipped_specs.append(annotated)
        else:
            kept_specs.append(annotated)
        command_reports.append(
            {
                **annotated.to_dict(),
                "eligible_for_bpf_owned": bool(decision.get("eligible_for_bpf_owned")),
                "core_direction": decision.get("core_direction"),
            }
        )

    skipped_count = len(skipped_specs)
    fallback_count = counts_by_status.get("fallback_to_linux", 0)
    planner_error_count = counts_by_status.get("bpf_owned_planner_error", 0)
    bpf_owned_skipped_count = counts_by_status.get("bpf_owned", 0)
    eligible_core_fallback_to_linux = sum(
        1
        for report in command_reports
        if report.get("eligible_for_bpf_owned")
        and report.get("ownership_status") == "fallback_to_linux"
    )
    linux_gz_fallback_command_count = sum(
        1
        for report in command_reports
        if report.get("eligible_for_bpf_owned")
        and report.get("ownership_status") == "fallback_to_linux"
    )
    linux_gz_fallback_enabled = bool(
        str(backend_config.get("backend_mode") or "") == "bpf_owned"
        and linux_gz_fallback_command_count > 0
    )
    backend_mode = str(backend_config.get("backend_mode") or "")
    effective_backend_mode = (
        "bpf_owned_planner_error"
        if backend_mode == "bpf_owned" and planner_error_count > 0
        else "bpf_owned_debug_safety_linux_fallback"
        if backend_mode == "bpf_owned" and linux_gz_fallback_enabled
        else backend_mode
    )
    strict_guard_ok = not (
        backend_mode == "bpf_owned"
        and (linux_gz_fallback_command_count > 0 or planner_error_count > 0)
    )
    return kept_specs, {
        "contract_phase": contract_phase,
        "backend_mode": backend_config.get("backend_mode"),
        "effective_backend_mode": effective_backend_mode,
        "linux_backend_mode": backend_config.get("linux_backend_mode"),
        "eligible_scopes": list(backend_config.get("eligible_scopes") or []),
        "fail_closed": bool(backend_config.get("fail_closed")),
        "bpf_owned_readiness": dict(backend_config.get("bpf_owned_readiness") or {}),
        "requested_command_count": len(specs),
        "linux_applied_command_count": len(kept_specs),
        "skipped_linux_command_count": skipped_count,
        "skipped_by_bpf_backend_count": bpf_owned_skipped_count,
        "linux_gz_fallback_enabled": linux_gz_fallback_enabled,
        "linux_gz_fallback_command_count": linux_gz_fallback_command_count,
        "eligible_core_fallback_to_linux": eligible_core_fallback_to_linux,
        "bpf_owned_strict_no_linux_gz_fallback_guard": strict_guard_ok,
        "fallback_to_linux_count": fallback_count,
        "fallback_to_linux_reasons": dict(sorted(fallback_reasons.items())),
        "bpf_owned_planner_error_count": planner_error_count,
        "bpf_owned_planner_error_reasons": dict(sorted(planner_error_reasons.items())),
        "bpf_owned_satellite_core_gz_scope_count": counts_by_status.get(
            "bpf_owned",
            0,
        ),
        "host_required_count": counts_by_status.get("host_required", 0),
        "shadow_only_count": counts_by_status.get("shadow_only", 0),
        "linux_owned_count": counts_by_status.get("linux_owned", 0),
        "counts_by_ownership_status": dict(sorted(counts_by_status.items())),
        "counts_by_apply_action": dict(sorted(counts_by_action.items())),
        "counts_by_scope": dict(sorted(counts_by_scope.items())),
        "core_direction_counts": dict(sorted(core_direction_counts.items())),
        "gz_core_coverage": {
            "g_to_z_core_command_count": core_direction_counts.get("g_to_z_core", 0),
            "z_to_g_core_command_count": core_direction_counts.get("z_to_g_core", 0),
            "core_mpls_readiness_command_count": core_direction_counts.get(
                "core_mpls_readiness",
                0,
            ),
        },
        "cleanup_scopes_narrowed_by_bpf_ownership": (
            ["satellite_core_mpls_lfib_residue", "mpls_readiness_sysctl_cleanup_or_check"]
            if bpf_owned_skipped_count
            else []
        ),
        "skipped_specs": _command_specs_payload(skipped_specs),
        "commands": command_reports,
        "scope_boundary": {
            "satellite_core_forwarding": list(BPF_OWNED_ELIGIBLE_SCOPES),
            "excluded": [
                "qdisc delay/rate/loss/bandwidth",
                "veth/netns/address lifecycle",
                "UT/GW edge IP behavior",
                "iptables/SNAT",
                "Mini Internet edge routes",
                "fallback/control traffic",
            ],
        },
    }


def _shared_zone_return_install_mapping(
    *,
    all_intents: Sequence[ZoneReturnInstallIntent],
    reapplied_intents: Sequence[ZoneReturnInstallIntent],
) -> Dict[str, Any]:
    all_apply_specs = _shared_zone_return_apply_specs(all_intents)
    reapplied_apply_specs = _shared_zone_return_apply_specs(reapplied_intents)
    all_rollback_specs = _shared_zone_return_rollback_specs(all_intents)
    forbidden_legacy_backbone_command_families = [
        {
            "blocker_id": "gateway_ut32_return_route",
            "legacy_command_family": "ip route replace <ut /32> encap mpls <per-flow reverse label>",
            "replacement": "ip route replace <zone return prefix> encap mpls <shared G/Z reverse label>",
            "actual_install_count": 0,
        },
        {
            "blocker_id": "reverse_lfib_label_stack",
            "legacy_command_family": "ip -f mpls route replace <per-flow reverse label>",
            "replacement": "one shared reverse label stack per (GW, zone, responsible satellite)",
            "actual_install_count": 0,
        },
        {
            "blocker_id": "reverse_transit_lfib_entry",
            "legacy_command_family": "ip -f mpls route replace <per-flow reverse transit label>",
            "replacement": "shared reverse transit LFIB per G/Z reverse path hop",
            "actual_install_count": 0,
        },
    ]
    return {
        "mapping_kind": "gz_shared_return_install_mapping",
        "current_linux_realization": "shared_gz_backbone",
        "intent_scope": "(selected_gateway, zone, responsible_satellite)",
        "all_intent_count": len(tuple(all_intents)),
        "reapplied_intent_count": len(tuple(reapplied_intents)),
        "forbidden_legacy_backbone_command_families": forbidden_legacy_backbone_command_families,
        "preserved_edge_command_families": [
            "gateway NAT/session edge policy",
            "serving-satellite local/access route to UT after label pop",
        ],
        "all_intents": [intent.to_dict() for intent in all_intents],
        "reapplied_intents": [intent.to_dict() for intent in reapplied_intents],
        "all_shared_apply_specs": _command_specs_payload(all_apply_specs),
        "reapplied_shared_apply_specs": _command_specs_payload(reapplied_apply_specs),
        "all_shared_rollback_specs": _command_specs_payload(all_rollback_specs),
        "all_shared_apply_command_count": len(all_apply_specs),
        "reapplied_shared_apply_command_count": len(reapplied_apply_specs),
        "all_shared_rollback_command_count": len(all_rollback_specs),
    }


def _subset_active_runtime_session(
    session: ActiveRuntimeSession,
    *,
    flow_ids: Sequence[str],
) -> ActiveRuntimeSession:
    selected = {str(flow_id) for flow_id in flow_ids}
    filtered_contexts: List[DirectGatewayExperimentContext] = []
    filtered_preflights: List[Dict[str, Any]] = []
    for context, preflight in zip(session.contexts, session.context_preflights):
        if str(context.flow_id) not in selected:
            continue
        filtered_contexts.append(context)
        filtered_preflights.append(dict(preflight))
    target_slice = dict(session.target_slice or {})
    target_slice["executed_ut_ids"] = [context.ut_id for context in filtered_contexts]
    target_slice["executed_flow_ids"] = [context.flow_id for context in filtered_contexts]
    target_slice["contains_multi_hop"] = any(
        not context.is_direct_gateway_path for context in filtered_contexts
    )
    return ActiveRuntimeSession(
        session_id=f"{session.session_id}:subset:{','.join(sorted(selected))}",
        backend=dict(session.backend or {}),
        target_slice=target_slice,
        host_runtime_boundary=dict(session.host_runtime_boundary or {}),
        preflight=dict(session.preflight or {}),
        apply_results=list(session.apply_results or []),
        verify_results=list(session.verify_results or []),
        ping_results=[
            result
            for result in list(session.ping_results or [])
            if str(result.get("flow_id")) in selected
        ],
        timing=dict(session.timing or {}),
        contexts=tuple(filtered_contexts),
        context_preflights=tuple(filtered_preflights),
    )


def _select_incremental_runtime_contexts(
    *,
    all_contexts: Sequence[DirectGatewayExperimentContext],
    existing_session: Optional[ActiveRuntimeSession],
) -> Dict[str, Any]:
    unchanged_flow_ids: set[str] = set()
    incremental_cleanup_flow_ids: List[str] = []
    existing_preflights_by_flow_id: Dict[str, Dict[str, Any]] = {}
    if existing_session is not None:
        existing_preflights_by_flow_id = {
            str(context.flow_id): dict(preflight)
            for context, preflight in zip(
                existing_session.contexts,
                existing_session.context_preflights,
            )
        }
        desired_signatures = {
            str(context.flow_id): _context_signature(context) for context in all_contexts
        }
        existing_signatures = {
            str(context.flow_id): _context_signature(context)
            for context in existing_session.contexts
        }
        unchanged_flow_ids = {
            flow_id
            for flow_id, desired_signature in desired_signatures.items()
            if existing_signatures.get(flow_id) == desired_signature
        }
        incremental_cleanup_flow_ids = [
            flow_id
            for flow_id in existing_signatures
            if flow_id not in unchanged_flow_ids
        ]
    changed_contexts = tuple(
        context
        for context in all_contexts
        if str(context.flow_id) not in unchanged_flow_ids
    )
    return {
        "all_contexts": tuple(all_contexts),
        "changed_contexts": changed_contexts,
        "unchanged_flow_ids": unchanged_flow_ids,
        "incremental_cleanup_flow_ids": incremental_cleanup_flow_ids,
        "existing_preflights_by_flow_id": existing_preflights_by_flow_id,
    }


def _runtime_ping_gateway_class_key(context: DirectGatewayExperimentContext) -> str:
    gateway_class_id = getattr(context, "gateway_class_id", "")
    return str(gateway_class_id or context.selected_gateway_id)


def _runtime_context_has_return_path(context: DirectGatewayExperimentContext) -> bool:
    return bool(
        getattr(context, "zone_id", None)
        or getattr(context, "zone_return_prefixes", ())
        or getattr(context, "return_path_intent", {})
    )


def _runtime_context_is_override(context: DirectGatewayExperimentContext) -> bool:
    return bool(
        str(getattr(context, "traffic_scope", "")) == "special_prefix_override"
        or ":override:" in str(getattr(context, "flow_id", ""))
        or ":override:" in str(getattr(context, "gateway_class_id", ""))
    )


def _runtime_context_is_direct(context: DirectGatewayExperimentContext) -> bool:
    direct = getattr(context, "is_direct_gateway_path", None)
    if direct is not None:
        return bool(direct)
    return len(tuple(getattr(context, "satellite_path", ()) or ())) <= 1


def _select_runtime_ping_validation_contexts(
    *,
    all_contexts: Sequence[DirectGatewayExperimentContext],
    reapplied_contexts: Sequence[DirectGatewayExperimentContext],
    validation_scope: str,
) -> Dict[str, Any]:
    normalized_scope = str(validation_scope or DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE)
    if normalized_scope not in {
        "all_active_flows",
        "reapplied_flows",
        DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE,
    }:
        raise RuntimeError(f"unsupported ping_validation_scope: {validation_scope}")

    fallback_reason: Optional[str] = None
    sample_source = normalized_scope
    if normalized_scope == "all_active_flows":
        selected_contexts = tuple(all_contexts)
        source_contexts = selected_contexts
    elif normalized_scope == "reapplied_flows":
        selected_contexts = tuple(reapplied_contexts)
        if not selected_contexts and all_contexts:
            selected_contexts = tuple(all_contexts)
            fallback_reason = "no_reapplied_flows"
            sample_source = "all_active_flows_fallback"
        source_contexts = selected_contexts
    else:
        source_contexts = tuple(reapplied_contexts)
        if not source_contexts and all_contexts:
            source_contexts = tuple(all_contexts)
            fallback_reason = "no_reapplied_flows"
            sample_source = "all_active_flows_fallback"
        representatives: Dict[str, DirectGatewayExperimentContext] = {}
        for context in source_contexts:
            gateway_class_key = _runtime_ping_gateway_class_key(context)
            representatives.setdefault(gateway_class_key, context)
        selected_context_list = [
            representatives[gateway_class_key]
            for gateway_class_key in sorted(representatives)
        ]

        coverage_flow_ids: Dict[str, str] = {}

        def append_coverage(
            category: str,
            candidates: Sequence[DirectGatewayExperimentContext],
        ) -> None:
            for candidate in candidates:
                if candidate in selected_context_list:
                    coverage_flow_ids[category] = str(candidate.flow_id)
                    return
            for candidate in candidates:
                selected_context_list.append(candidate)
                coverage_flow_ids[category] = str(candidate.flow_id)
                return

        append_coverage(
            "direct_handoff",
            [context for context in source_contexts if _runtime_context_is_direct(context)],
        )
        append_coverage(
            "core_mpls_like",
            [
                context
                for context in source_contexts
                if not _runtime_context_is_direct(context)
            ],
        )
        append_coverage(
            "return_z",
            [
                context
                for context in source_contexts
                if _runtime_context_has_return_path(context)
            ],
        )
        append_coverage(
            "default_plus_override",
            [
                context
                for context in source_contexts
                if _runtime_context_is_override(context)
            ],
        )
        zone_to_contexts: Dict[str, List[DirectGatewayExperimentContext]] = {}
        for context in source_contexts:
            zone_id = str(getattr(context, "zone_id", "") or "")
            if zone_id:
                zone_to_contexts.setdefault(zone_id, []).append(context)
        for zone_id in sorted(zone_to_contexts):
            zone_contexts = zone_to_contexts[zone_id]
            if len({str(context.ut_id) for context in zone_contexts}) < 2:
                continue
            already_selected = {
                str(context.ut_id)
                for context in selected_context_list
                if str(getattr(context, "zone_id", "") or "") == zone_id
            }
            if len(already_selected) >= 2:
                coverage_flow_ids["same_zone_multi_ut"] = next(
                    str(context.flow_id)
                    for context in selected_context_list
                    if str(getattr(context, "zone_id", "") or "") == zone_id
                )
                break
            append_coverage(
                "same_zone_multi_ut",
                [
                    context
                    for context in zone_contexts
                    if str(context.ut_id) not in already_selected
                ],
            )
            break
        selected_contexts = tuple(selected_context_list)

    representative_by_gateway_class = {
        _runtime_ping_gateway_class_key(context): context.flow_id
        for context in selected_contexts
    }
    source_gateway_class_keys = {
        _runtime_ping_gateway_class_key(context) for context in source_contexts
    }
    selected_gateway_class_keys = {
        _runtime_ping_gateway_class_key(context) for context in selected_contexts
    }
    return {
        "validation_scope": normalized_scope,
        "sample_source": sample_source,
        "active_flow_count": len(tuple(all_contexts)),
        "reapplied_flow_count": len(tuple(reapplied_contexts)),
        "sample_source_flow_count": len(source_contexts),
        "sample_source_gateway_class_count": len(source_gateway_class_keys),
        "selected_flow_count": len(selected_contexts),
        "selected_gateway_class_count": len(selected_gateway_class_keys),
        "gateway_class_sample_reduction_count": max(
            0,
            len(source_contexts) - len(selected_contexts),
        ),
        "selected_to_sample_source_flow_ratio": _ratio(
            len(selected_contexts),
            len(source_contexts),
        ),
        "selected_contexts": selected_contexts,
        "target_flow_ids": [context.flow_id for context in selected_contexts],
        "target_ut_ids": [context.ut_id for context in selected_contexts],
        "target_gateway_class_keys": [
            _runtime_ping_gateway_class_key(context) for context in selected_contexts
        ],
        "representative_flow_by_gateway_class": representative_by_gateway_class,
        "coverage_flow_ids": locals().get("coverage_flow_ids", {}),
        "coverage": {
            "direct_probe_selected": any(
                _runtime_context_is_direct(context) for context in selected_contexts
            ),
            "core_probe_selected": any(
                not _runtime_context_is_direct(context) for context in selected_contexts
            ),
            "return_z_probe_selected": any(
                _runtime_context_has_return_path(context)
                for context in selected_contexts
            ),
            "override_probe_selected": any(
                _runtime_context_is_override(context) for context in selected_contexts
            ),
            "same_zone_multi_ut_probe_selected": any(
                len(
                    {
                        str(candidate.ut_id)
                        for candidate in selected_contexts
                        if str(getattr(candidate, "zone_id", "") or "")
                        == str(getattr(context, "zone_id", "") or "")
                    }
                )
                >= 2
                for context in selected_contexts
                if str(getattr(context, "zone_id", "") or "")
            ),
        },
        "fallback_reason": fallback_reason,
    }


def _run_docker_command(
    *,
    node_id: str,
    command: Sequence[str],
    phase: str,
    description: str,
    allow_failure: bool = False,
) -> RuntimeCommandResult:
    completed = _timed_subprocess_run(
        ["docker", "exec", node_id, *command],
        timeout_seconds=DOCKER_EXEC_TIMEOUT_SECONDS,
    )
    if completed.returncode == 127:
        completed = _run_host_netns_command(
            node_id=node_id,
            command=command,
        )
    result = RuntimeCommandResult(
        node_id=node_id,
        phase=phase,
        description=description,
        command=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
        allow_failure=allow_failure,
        wall_seconds=float(getattr(completed, "skyseed_wall_seconds", 0.0)),
    )
    if completed.returncode != 0 and not allow_failure:
        raise RuntimeCommandFailure(result)
    return result


def _batch_shell_line(spec: RuntimeCommandSpec) -> str:
    command_line = shlex.join(spec.command)
    if spec.allow_failure:
        return f"{command_line} || true"
    return command_line


def _instrumented_batch_shell_lines(index: int, spec: RuntimeCommandSpec) -> List[str]:
    command_line = shlex.join(spec.command)
    lines = [
        f"printf '__SKYSEED_CMD_BEGIN__ {index} %s\\n' \"$(date +%s.%N)\"",
        "set +e",
        command_line,
        "__skyseed_cmd_rc=$?",
        "set -e",
        f"printf '__SKYSEED_CMD_END__ {index} %s %s\\n' "
        '"$(date +%s.%N)" "$__skyseed_cmd_rc"',
    ]
    if not spec.allow_failure:
        lines.append('if [ "$__skyseed_cmd_rc" -ne 0 ]; then exit "$__skyseed_cmd_rc"; fi')
    return lines


def _parse_node_local_command_timings(stdout: str) -> Tuple[Dict[str, Any], ...]:
    starts: Dict[int, float] = {}
    timings: List[Dict[str, Any]] = []
    for line in str(stdout).splitlines():
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        marker = parts[0]
        if marker == "__SKYSEED_CMD_BEGIN__":
            try:
                starts[int(parts[1])] = float(parts[2])
            except (TypeError, ValueError):
                continue
        elif marker == "__SKYSEED_CMD_END__" and len(parts) >= 4:
            try:
                command_index = int(parts[1])
                ended_at = float(parts[2])
                returncode = int(parts[3])
            except (TypeError, ValueError):
                continue
            started_at = starts.get(command_index)
            wall_seconds = max(0.0, ended_at - started_at) if started_at is not None else 0.0
            timings.append(
                {
                    "command_index": command_index,
                    "wall_seconds": round(wall_seconds, 6),
                    "returncode": returncode,
                    "timing_source": "node_local_batch_shell_marker",
                }
            )
    return tuple(sorted(timings, key=lambda item: int(item["command_index"])))


def _strip_node_local_command_timing_markers(stdout: str) -> str:
    marker_prefixes = ("__SKYSEED_CMD_BEGIN__ ", "__SKYSEED_CMD_END__ ")
    lines = [
        line
        for line in str(stdout).splitlines()
        if not line.startswith(marker_prefixes)
    ]
    return "\n".join(lines).strip()


@lru_cache(maxsize=None)
def _container_pid(node_id: str) -> int:
    completed = _timed_subprocess_run(
        ["docker", "inspect", "-f", "{{.State.Pid}}", node_id],
        timeout_seconds=DOCKER_INSPECT_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"unable to resolve container pid for {node_id}: {completed.stderr.strip()}"
        )
    return int(completed.stdout.strip())


def _run_host_netns_command(
    *,
    node_id: str,
    command: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    return _timed_subprocess_run(
        ["sudo", "-n", "nsenter", "-t", str(_container_pid(node_id)), "-n", "--", *command],
        timeout_seconds=DOCKER_EXEC_TIMEOUT_SECONDS,
    )


def _run_host_netns_batch(
    *,
    node_id: str,
    script: str,
    timeout_seconds: float = NODE_LOCAL_BATCH_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    return _timed_subprocess_run(
        ["sudo", "-n", "nsenter", "-t", str(_container_pid(node_id)), "-n", "--", "sh", "-s"],
        timeout_seconds=timeout_seconds,
        input_text=script,
    )


def _run_node_local_batch(
    batch: NodeLocalBatchSpec,
    *,
    timeout_seconds: float = NODE_LOCAL_BATCH_TIMEOUT_SECONDS,
) -> NodeLocalBatchResult:
    started_at = time.perf_counter()
    script_lines = ["set -eu"]
    for index, spec in enumerate(batch.command_specs):
        script_lines.append(f"printf '%s\\n' {shlex.quote(f'[{spec.phase}] {spec.description}')}")
        script_lines.extend(_instrumented_batch_shell_lines(index, spec))
    script = "\n".join(script_lines) + "\n"
    completed = _timed_subprocess_run(
        ["docker", "exec", "-i", batch.node_id, "sh", "-s"],
        timeout_seconds=timeout_seconds,
        input_text=script,
    )
    if completed.returncode == 127:
        completed = _run_host_netns_batch(
            node_id=batch.node_id,
            script=script,
            timeout_seconds=timeout_seconds,
        )
    command_timings = _parse_node_local_command_timings(completed.stdout)
    result = NodeLocalBatchResult(
        node_id=batch.node_id,
        phase=batch.phase,
        command_count=batch.command_count,
        descriptions=tuple(spec.description for spec in batch.command_specs),
        returncode=completed.returncode,
        stdout=_strip_node_local_command_timing_markers(completed.stdout),
        stderr=completed.stderr.strip(),
        wall_seconds=time.perf_counter() - started_at,
        command_timings=command_timings,
    )
    if completed.returncode != 0:
        raise NodeLocalBatchFailure(result)
    return result


def _group_node_local_batches(
    specs: Sequence[RuntimeCommandSpec],
) -> List[NodeLocalBatchSpec]:
    grouped: Dict[Tuple[str, str], List[RuntimeCommandSpec]] = {}
    seen_by_group: Dict[
        Tuple[str, str],
        set[Tuple[Tuple[str, ...], bool]],
    ] = {}
    for spec in specs:
        group_key = (spec.node_id, spec.phase)
        command_key = (tuple(spec.command), bool(spec.allow_failure))
        seen = seen_by_group.setdefault(group_key, set())
        if command_key in seen:
            continue
        seen.add(command_key)
        grouped.setdefault(group_key, []).append(spec)
    return [
        NodeLocalBatchSpec(
            node_id=node_id,
            phase=phase,
            command_specs=tuple(grouped_specs),
        )
        for (node_id, phase), grouped_specs in grouped.items()
    ]


def _execute_node_local_batches(
    batches: Sequence[NodeLocalBatchSpec],
    *,
    max_workers: int,
    timeout_seconds: float = NODE_LOCAL_BATCH_TIMEOUT_SECONDS,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> List[NodeLocalBatchResult]:
    if not batches:
        return []
    bounded_workers = max(1, min(int(max_workers), len(batches)))
    results: List[Tuple[int, NodeLocalBatchResult]] = []
    with ThreadPoolExecutor(max_workers=bounded_workers) as executor:
        future_by_index = {
            executor.submit(
                _run_node_local_batch,
                batch,
                timeout_seconds=timeout_seconds,
            ): (index, batch)
            for index, batch in enumerate(batches)
        }
        for future in as_completed(future_by_index):
            index, _batch = future_by_index[future]
            result = future.result()
            results.append((index, result))
            if progress_callback is not None:
                progress_callback(
                    {
                        "completed_batch_count": len(results),
                        "total_batch_count": len(batches),
                        "node_id": result.node_id,
                        "phase": result.phase,
                        "command_count": result.command_count,
                        "returncode": result.returncode,
                        "wall_seconds": result.wall_seconds,
                    }
                )
    return [result for _index, result in sorted(results, key=lambda item: item[0])]


def _node_local_batch_cleanup_metrics(
    *,
    requested_specs: Sequence[RuntimeCommandSpec],
    batches: Sequence[NodeLocalBatchSpec],
    max_workers: int,
) -> Dict[str, Any]:
    requested_count = len(requested_specs)
    grouped_count = sum(batch.command_count for batch in batches)
    worker_count = max(0, min(max(1, int(max_workers)), len(batches))) if batches else 0
    plan_summary = summarize_node_local_batches(
        [batch.to_dict() for batch in batches],
        [],
    )
    return {
        "requested_command_count": requested_count,
        "grouped_command_count": grouped_count,
        "duplicate_cleanup_skipped_count": max(0, requested_count - grouped_count),
        "skipped_noop_count": max(0, requested_count - grouped_count),
        "bounded_parallel_worker_count": worker_count,
        "node_local_rollback_worker_count": worker_count,
        "node_local_command_batch_count": len(batches),
        "cleanup_counts_by_scope": dict(plan_summary.get("cleanup_counts_by_scope") or {}),
        "planned_state_touch_count_by_scope": dict(
            plan_summary.get("planned_state_touch_count_by_scope") or {}
        ),
        "bpf_owned_gz_forwarding_cleanup_candidate_command_count": int(
            (plan_summary.get("bpf_owned_gz_forwarding_candidate") or {}).get(
                "command_count",
                0,
            )
            or 0
        ),
        "cleanup_scopes_that_would_shrink_if_bpf_owned_gz_forwarding": list(
            (
                plan_summary.get("bpf_owned_gz_forwarding_candidate") or {}
            ).get("cleanup_scopes_that_would_shrink", [])
        ),
        "changed_state_count_available": False,
    }


def _batch_timing_summary(
    batch_results: Sequence[NodeLocalBatchResult],
) -> Dict[str, Any]:
    per_node_seconds: Dict[str, float] = {}
    per_phase_seconds: Dict[str, float] = {}
    for batch in batch_results:
        per_node_seconds[batch.node_id] = per_node_seconds.get(batch.node_id, 0.0) + float(
            batch.wall_seconds
        )
        per_phase_seconds[batch.phase] = per_phase_seconds.get(batch.phase, 0.0) + float(
            batch.wall_seconds
        )
    return {
        "batch_count": len(batch_results),
        "total_wall_seconds": sum(float(batch.wall_seconds) for batch in batch_results),
        "per_node_wall_seconds": {
            node_id: round(seconds, 6)
            for node_id, seconds in sorted(per_node_seconds.items())
        },
        "per_phase_wall_seconds": {
            phase: round(seconds, 6)
            for phase, seconds in sorted(per_phase_seconds.items())
        },
    }


def _probe_marker_command(probe_id: str, command: Sequence[str]) -> Tuple[str, ...]:
    begin_marker = f"__SKYSEED_PROBE_BEGIN_{probe_id}__"
    end_marker = f"__SKYSEED_PROBE_END_{probe_id}__"
    return (
        "sh",
        "-lc",
        (
            f"printf '%s\\n' {shlex.quote(begin_marker)}; "
            f"{shlex.join(command)} || true; "
            f"printf '\\n%s\\n' {shlex.quote(end_marker)}"
        ),
    )


def _parse_probe_outputs(
    batch_results: Sequence[NodeLocalBatchResult],
    *,
    probe_ids: Sequence[str],
) -> Dict[str, str]:
    combined_stdout = "\n".join(result.stdout for result in batch_results)
    outputs: Dict[str, str] = {}
    for probe_id in probe_ids:
        begin_marker = f"__SKYSEED_PROBE_BEGIN_{probe_id}__"
        end_marker = f"__SKYSEED_PROBE_END_{probe_id}__"
        begin_index = combined_stdout.find(begin_marker)
        if begin_index < 0:
            outputs[probe_id] = ""
            continue
        content_start = begin_index + len(begin_marker)
        end_index = combined_stdout.find(end_marker, content_start)
        if end_index < 0:
            outputs[probe_id] = combined_stdout[content_start:].strip()
            continue
        outputs[probe_id] = combined_stdout[content_start:end_index].strip()
    return outputs


def _context_path_summary(context: DirectGatewayExperimentContext) -> Dict[str, Any]:
    target_ids = [str(target.get("node_id")) for target in context.validation_targets]
    target_ips = [str(target.get("ip_address")) for target in context.validation_targets]
    return {
        "flow_id": context.flow_id,
        "ut_id": context.ut_id,
        "zone_id": context.zone_id,
        "serving_satellite_id": context.serving_satellite_id,
        "selected_gateway_id": context.selected_gateway_id,
        "selected_gateway_satellite_id": context.selected_gateway_satellite_id,
        "gateway_class_id": context.gateway_class_id,
        "traffic_scope": context.traffic_scope,
        "zone_return_target_key": ":".join(_zone_return_target_key(context)),
        "satellite_path": list(context.satellite_path),
        "path_hop_count": len(context.satellite_path),
        "path_string": " -> ".join(
            [
                context.ut_id,
                *context.satellite_path,
                context.selected_gateway_id,
            ]
        ),
        "target_host_node_ids": target_ids,
        "target_host_ips": target_ips,
        "forward_labels": list(context.forward_labels),
        "reverse_labels": list(context.reverse_labels),
        "is_direct_gateway_path": context.is_direct_gateway_path,
    }


def _context_primary_target_matches(
    context: DirectGatewayExperimentContext,
    target_selector: str,
) -> bool:
    return (
        str(context.target_host_node_id) == target_selector
        or str(context.ping_target_ip) == target_selector
    )


def _context_validation_target_matches(
    context: DirectGatewayExperimentContext,
    target_selector: str,
) -> bool:
    return any(
        str(target.get("node_id")) == target_selector
        or str(target.get("ip_address")) == target_selector
        for target in context.validation_targets
    )


def _context_observation_target(
    context: DirectGatewayExperimentContext,
    target_selector: str,
) -> Dict[str, Any]:
    for target in context.validation_targets:
        if (
            str(target.get("node_id")) == target_selector
            or str(target.get("ip_address")) == target_selector
        ):
            return dict(target)
    return {
        "node_id": context.target_host_node_id,
        "ip_address": context.ping_target_ip,
    }


def _ping_validation_targets(
    context: DirectGatewayExperimentContext,
    *,
    phase: str,
    description: str,
    primary_only: bool = False,
) -> Tuple[List[Dict[str, Any]], float]:
    validation_targets = (
        [
            {
                "node_id": context.target_host_node_id,
                "ip_address": context.ping_target_ip,
            }
        ]
        if primary_only
        else list(context.validation_targets)
    ) or [
        {
            "node_id": context.target_host_node_id,
            "ip_address": context.ping_target_ip,
        }
    ]
    ping_results: List[Dict[str, Any]] = []
    started_at = time.perf_counter()
    for validation_target in validation_targets:
        ping = None
        attempts: List[Dict[str, Any]] = []
        for attempt_index in range(3):
            ping = _run_docker_command(
                node_id=context.ut_id,
                command=(
                    "ping",
                    "-c",
                    "1",
                    "-W",
                    "2",
                    str(validation_target["ip_address"]),
                ),
                phase=phase,
                description=description,
                allow_failure=True,
            )
            attempts.append(
                {
                    "attempt": attempt_index + 1,
                    "returncode": ping.returncode,
                    "stdout": ping.stdout,
                    "stderr": ping.stderr,
                }
            )
            if ping.returncode == 0:
                break
            if attempt_index < 2:
                time.sleep(0.2)
        assert ping is not None
        ping_results.append(
            {
                "flow_id": context.flow_id,
                "ut_id": context.ut_id,
                "traffic_scope": (
                    "default_gateway_class"
                    if str(validation_target["node_id"]) == str(context.target_host_node_id)
                    else "special_prefix_override"
                ),
                "ping_target_ip": str(validation_target["ip_address"]),
                "target_host_node_id": str(validation_target["node_id"]),
                "selected_gateway_id": context.selected_gateway_id,
                "serving_satellite_id": context.serving_satellite_id,
                "satellite_path": list(context.satellite_path),
                "path_hop_count": len(context.satellite_path),
                "path_string": " -> ".join(
                    [
                        context.ut_id,
                        *context.satellite_path,
                        context.selected_gateway_id,
                        str(validation_target["node_id"]),
                    ]
                ),
                "success": ping.returncode == 0,
                "stdout": ping.stdout,
                "stderr": ping.stderr,
                "returncode": ping.returncode,
                "attempt_count": len(attempts),
                "attempts": attempts,
            }
        )
    return ping_results, time.perf_counter() - started_at


def _ground_gateway_sanity_probe(
    session: "ActiveRuntimeSession",
    *,
    primary_only: bool,
) -> Dict[str, Any]:
    started_at = time.perf_counter()
    flow_results: List[Dict[str, Any]] = []
    gateway_mapping: Dict[str, Dict[str, Any]] = {}
    for context in session.contexts:
        targets = _bpf_owned_validation_targets_for_context(
            context,
            primary_only=primary_only,
        )
        mapping = gateway_mapping.setdefault(
            context.ut_id,
            {
                "ut_id": context.ut_id,
                "default_gateway_ids": [],
                "secondary_or_override_gateway_ids": [],
                "flow_ids": [],
            },
        )
        if context.flow_id not in mapping["flow_ids"]:
            mapping["flow_ids"].append(context.flow_id)
        for target in targets:
            target_ip = str(target["ip_address"])
            traffic_scope = (
                "default_gateway_class"
                if str(target["node_id"]) == str(context.target_host_node_id)
                else "special_prefix_override"
            )
            gateway_bucket = (
                "default_gateway_ids"
                if traffic_scope == "default_gateway_class"
                else "secondary_or_override_gateway_ids"
            )
            if context.selected_gateway_id not in mapping[gateway_bucket]:
                mapping[gateway_bucket].append(context.selected_gateway_id)
            route = _run_docker_command(
                node_id=context.selected_gateway_id,
                command=("ip", "route", "get", target_ip),
                phase="ground_sanity",
                description="probe selected gateway route to Mini Internet target",
                allow_failure=True,
            )
            route_text = f"{route.stdout}\n{route.stderr}".lower()
            route_success = route.returncode == 0 and "unreachable" not in route_text
            ping = _run_docker_command(
                node_id=context.selected_gateway_id,
                command=("ping", "-n", "-c", "1", "-W", "2", target_ip),
                phase="ground_sanity",
                description="probe selected gateway ping to Mini Internet target",
                allow_failure=True,
            )
            flow_results.append(
                {
                    "flow_id": context.flow_id,
                    "ut_id": context.ut_id,
                    "zone_id": context.zone_id,
                    "traffic_scope": traffic_scope,
                    "context_traffic_scope": context.traffic_scope,
                    "gateway_class_id": context.gateway_class_id,
                    "selected_gateway_id": context.selected_gateway_id,
                    "serving_satellite_id": context.serving_satellite_id,
                    "ut_access_ip": context.ut_access_ip,
                    "ut_access_peer_ip": context.ut_access_peer_ip,
                    "target_host_node_id": str(target["node_id"]),
                    "target_ip": target_ip,
                    "route_get_success": route_success,
                    "gateway_ping_success": ping.returncode == 0,
                    "route_get": route.to_dict(),
                    "gateway_ping": ping.to_dict(),
                }
            )
    blockers: List[str] = []
    if any(not result["route_get_success"] for result in flow_results):
        blockers.append("gw_route_missing")
    if any(not result["gateway_ping_success"] for result in flow_results):
        blockers.append("ground_target_unreachable")
    return {
        "schema_version": "skyseed-ground-gw-mini-internet-sanity-v1",
        "enabled": True,
        "primary_only": primary_only,
        "ok": not blockers,
        "blockers": blockers,
        "flow_count": len(flow_results),
        "selected_gateway_ids": sorted(
            {
                str(result["selected_gateway_id"])
                for result in flow_results
                if result.get("selected_gateway_id")
            }
        ),
        "target_ips": sorted(
            {
                str(result["target_ip"])
                for result in flow_results
                if result.get("target_ip")
            }
        ),
        "gateway_mapping_by_ut": list(gateway_mapping.values()),
        "flow_results": flow_results,
        "wall_seconds": time.perf_counter() - started_at,
    }


def _warm_direct_access_link(
    *,
    node_id: str,
    target_ip: str,
    phase: str,
    description: str,
) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    started_at = time.perf_counter()
    ping = None
    for attempt_index in range(3):
        ping = _run_docker_command(
            node_id=node_id,
            command=("ping", "-c", "1", "-W", "1", target_ip),
            phase=phase,
            description=description,
            allow_failure=True,
        )
        attempts.append(
            {
                "attempt": attempt_index + 1,
                "returncode": ping.returncode,
                "stdout": ping.stdout,
                "stderr": ping.stderr,
            }
        )
        if ping.returncode == 0:
            break
        if attempt_index < 2:
            time.sleep(0.2)
    assert ping is not None
    return {
        "node_id": node_id,
        "target_ip": target_ip,
        "success": ping.returncode == 0,
        "returncode": ping.returncode,
        "stdout": ping.stdout,
        "stderr": ping.stderr,
        "attempt_count": len(attempts),
        "attempts": attempts,
        "wall_seconds": time.perf_counter() - started_at,
    }


def _rule_probe_command(
    *,
    source_ip: str,
    policy_table_id: int,
) -> Tuple[str, ...]:
    return (
        "sh",
        "-lc",
        (
            "ip rule show | grep -Fq "
            f"'from {source_ip} lookup {policy_table_id}'"
        ),
    )


def _iptables_probe_command(
    *,
    table_name: Optional[str],
    rule_args: Sequence[str],
) -> Tuple[str, ...]:
    table_prefix = f"-t {table_name} " if table_name is not None else ""
    joined_args = " ".join(rule_args)
    return (
        "sh",
        "-lc",
        f"iptables -w {IPTABLES_WAIT_SECONDS} {table_prefix}-C {joined_args}",
    )


def _sysctl_read_command(interface_name: str) -> Tuple[str, ...]:
    return ("sysctl", "-n", f"net.mpls.conf.{interface_name}.input")


def _platform_labels_read_command() -> Tuple[str, ...]:
    return ("sysctl", "-n", "net.mpls.platform_labels")


def _interface_packet_counter_command(interface_name: str) -> Tuple[str, ...]:
    return (
        "sh",
        "-lc",
        (
            f"printf '%s %s %s %s' "
            f"\"$(cat /sys/class/net/{interface_name}/statistics/rx_packets)\" "
            f"\"$(cat /sys/class/net/{interface_name}/statistics/tx_packets)\" "
            f"\"$(cat /sys/class/net/{interface_name}/ifindex)\" "
            f"\"$(cat /sys/class/net/{interface_name}/iflink)\""
        ),
    )


def _as_int(value: str, *, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _default_preflight_state(context: DirectGatewayExperimentContext) -> Dict[str, Any]:
    return {
        "ut_default_route_present": False,
        "ut_default_route_stdout": "",
        "sat_source_rule_present": False,
        "sat_policy_default_present": False,
        "sat_policy_default_stdout": "",
        "sat_delivery_route_present": False,
        "sat_delivery_route_stdout": "",
        "sat_reverse_pop_present": False,
        "sat_reverse_pop_stdout": "",
        "gw_forward_pop_present": False,
        "gw_forward_pop_stdout": "",
        "gw_reverse_route_present": False,
        "gw_reverse_route_stdout": "",
        "gw_forward_rule_present": False,
        "gw_reverse_rule_present": False,
        "gw_snat_rule_present": False,
        "node_platform_labels": {
            node_id: "0"
            for node_id in context.nodes_requiring_platform_labels
        },
        "mpls_input_state": {
            f"{node_id}:{interface_name}": "0"
            for node_id, interface_name in context.mpls_input_interfaces
        },
        "forward_lfib_state": {},
        "reverse_lfib_state": {},
    }


def _route_show_command(*parts: str) -> Tuple[str, ...]:
    return ("ip", "route", "show", *parts)


def _mpls_route_show_command(label: int) -> Tuple[str, ...]:
    return ("sh", "-lc", f"ip -f mpls route show {label}")


def _capture_preflight_state(context: DirectGatewayExperimentContext) -> Dict[str, Any]:
    ut_default_route = _run_docker_command(
        node_id=context.ut_id,
        command=_route_show_command("default"),
        phase="preflight",
        description="capture UT default route",
        allow_failure=True,
    )
    sat_source_rule = _run_docker_command(
        node_id=context.serving_satellite_id,
        command=_rule_probe_command(
            source_ip=context.ut_access_ip,
            policy_table_id=context.policy_table_id,
        ),
        phase="preflight",
        description="probe serving-satellite source rule",
        allow_failure=True,
    )
    sat_policy_default = _run_docker_command(
        node_id=context.serving_satellite_id,
        command=_route_show_command("table", str(context.policy_table_id), "default"),
        phase="preflight",
        description="capture serving-satellite policy-table default",
        allow_failure=True,
    )
    sat_delivery_route = _run_docker_command(
        node_id=context.serving_satellite_id,
        command=_route_show_command(context.return_target_prefix),
        phase="preflight",
        description="capture serving-satellite UT delivery route",
        allow_failure=True,
    )
    sat_reverse_pop = _run_docker_command(
        node_id=context.serving_satellite_id,
        command=_mpls_route_show_command(context.reverse_labels[-1]),
        phase="preflight",
        description="capture serving-satellite reverse pop label",
        allow_failure=True,
    )
    gw_forward_pop = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_mpls_route_show_command(context.forward_labels[-1]),
        phase="preflight",
        description="capture gateway forward pop label",
        allow_failure=True,
    )
    gw_reverse_route = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_route_show_command(context.return_target_prefix),
        phase="preflight",
        description="capture gateway reverse ingress route",
        allow_failure=True,
    )
    node_platform_labels: Dict[str, str] = {}
    for node_id in context.nodes_requiring_platform_labels:
        result = _run_docker_command(
            node_id=node_id,
            command=_platform_labels_read_command(),
            phase="preflight",
            description=f"read {node_id} MPLS platform labels",
        )
        node_platform_labels[node_id] = result.stdout
    mpls_input_state: Dict[str, str] = {}
    for node_id, interface_name in context.mpls_input_interfaces:
        result = _run_docker_command(
            node_id=node_id,
            command=_sysctl_read_command(interface_name),
            phase="preflight",
            description=f"read MPLS input state on {node_id}:{interface_name}",
        )
        mpls_input_state[f"{node_id}:{interface_name}"] = result.stdout
    forward_lfib_state: Dict[str, str] = {}
    for index, segment in enumerate(context.forward_segments[1:], start=1):
        result = _run_docker_command(
            node_id=segment.source_node_id,
            command=_mpls_route_show_command(context.forward_labels[index - 1]),
            phase="preflight",
            description=(
                f"capture forward LFIB on {segment.source_node_id} "
                f"for label {context.forward_labels[index - 1]}"
            ),
            allow_failure=True,
        )
        forward_lfib_state[f"{segment.source_node_id}:{context.forward_labels[index - 1]}"] = (
            result.stdout
        )
    reverse_lfib_state: Dict[str, str] = {}
    for index, segment in enumerate(context.reverse_segments[1:], start=1):
        result = _run_docker_command(
            node_id=segment.source_node_id,
            command=_mpls_route_show_command(context.reverse_labels[index - 1]),
            phase="preflight",
            description=(
                f"capture reverse LFIB on {segment.source_node_id} "
                f"for label {context.reverse_labels[index - 1]}"
            ),
            allow_failure=True,
        )
        reverse_lfib_state[f"{segment.source_node_id}:{context.reverse_labels[index - 1]}"] = (
            result.stdout
        )
    forward_rule_args = (
        "FORWARD",
        "-i",
        context.gateway_access_interface,
        "-o",
        context.gateway_nat_interface,
        "-s",
        f"{context.ut_access_ip}/32",
        "-j",
        "ACCEPT",
    )
    reverse_rule_args = (
        "FORWARD",
        "-i",
        context.gateway_nat_interface,
        "-o",
        context.gateway_access_interface,
        "-d",
        f"{context.ut_access_ip}/32",
        "-m",
        "conntrack",
        "--ctstate",
        "ESTABLISHED,RELATED",
        "-j",
        "ACCEPT",
    )
    snat_rule_args = (
        "POSTROUTING",
        "-s",
        f"{context.ut_access_ip}/32",
        "-o",
        context.gateway_nat_interface,
        "-j",
        "SNAT",
        "--to-source",
        context.gateway_nat_source_ip,
    )
    gw_forward_rule = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_iptables_probe_command(table_name=None, rule_args=forward_rule_args),
        phase="preflight",
        description="probe gateway forward filter rule",
        allow_failure=True,
    )
    gw_reverse_rule = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_iptables_probe_command(table_name=None, rule_args=reverse_rule_args),
        phase="preflight",
        description="probe gateway reverse filter rule",
        allow_failure=True,
    )
    gw_snat_rule = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_iptables_probe_command(table_name="nat", rule_args=snat_rule_args),
        phase="preflight",
        description="probe gateway SNAT rule",
        allow_failure=True,
    )
    return {
        "ut_default_route_present": bool(ut_default_route.stdout),
        "ut_default_route_stdout": ut_default_route.stdout,
        "sat_source_rule_present": sat_source_rule.returncode == 0,
        "sat_policy_default_present": bool(sat_policy_default.stdout),
        "sat_policy_default_stdout": sat_policy_default.stdout,
        "sat_delivery_route_present": bool(sat_delivery_route.stdout),
        "sat_delivery_route_stdout": sat_delivery_route.stdout,
        "sat_reverse_pop_present": bool(sat_reverse_pop.stdout),
        "sat_reverse_pop_stdout": sat_reverse_pop.stdout,
        "gw_forward_pop_present": bool(gw_forward_pop.stdout),
        "gw_forward_pop_stdout": gw_forward_pop.stdout,
        "gw_reverse_route_present": bool(gw_reverse_route.stdout),
        "gw_reverse_route_stdout": gw_reverse_route.stdout,
        "gw_forward_rule_present": gw_forward_rule.returncode == 0,
        "gw_reverse_rule_present": gw_reverse_rule.returncode == 0,
        "gw_snat_rule_present": gw_snat_rule.returncode == 0,
        "node_platform_labels": node_platform_labels,
        "mpls_input_state": mpls_input_state,
        "forward_lfib_state": forward_lfib_state,
        "reverse_lfib_state": reverse_lfib_state,
    }


def _capture_bpf_owned_preflight_state(
    context: DirectGatewayExperimentContext,
) -> Dict[str, Any]:
    ut_default_route = _run_docker_command(
        node_id=context.ut_id,
        command=_route_show_command("default"),
        phase="preflight",
        description="capture UT default route",
        allow_failure=True,
    )
    forward_rule_args = (
        "FORWARD",
        "-i",
        context.gateway_access_interface,
        "-o",
        context.gateway_nat_interface,
        "-s",
        f"{context.ut_access_ip}/32",
        "-j",
        "ACCEPT",
    )
    reverse_rule_args = (
        "FORWARD",
        "-i",
        context.gateway_nat_interface,
        "-o",
        context.gateway_access_interface,
        "-d",
        f"{context.ut_access_ip}/32",
        "-m",
        "conntrack",
        "--ctstate",
        "ESTABLISHED,RELATED",
        "-j",
        "ACCEPT",
    )
    snat_rule_args = (
        "POSTROUTING",
        "-s",
        f"{context.ut_access_ip}/32",
        "-o",
        context.gateway_nat_interface,
        "-j",
        "SNAT",
        "--to-source",
        context.gateway_nat_source_ip,
    )
    gw_forward_rule = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_iptables_probe_command(table_name=None, rule_args=forward_rule_args),
        phase="preflight",
        description="probe gateway forward filter rule",
        allow_failure=True,
    )
    gw_reverse_rule = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_iptables_probe_command(table_name=None, rule_args=reverse_rule_args),
        phase="preflight",
        description="probe gateway reverse filter rule",
        allow_failure=True,
    )
    gw_snat_rule = _run_docker_command(
        node_id=context.selected_gateway_id,
        command=_iptables_probe_command(table_name="nat", rule_args=snat_rule_args),
        phase="preflight",
        description="probe gateway SNAT rule",
        allow_failure=True,
    )
    return {
        **_default_preflight_state(context),
        "preflight_backend": "bpf_owned",
        "legacy_linux_mpls_debug_probe": {
            "enabled": False,
            "authoritative": False,
            "reason": "strict_bpf_owned_uses_bpf_native_evidence",
        },
        "ut_default_route_present": bool(ut_default_route.stdout),
        "ut_default_route_stdout": ut_default_route.stdout,
        "gw_forward_rule_present": gw_forward_rule.returncode == 0,
        "gw_reverse_rule_present": gw_reverse_rule.returncode == 0,
        "gw_snat_rule_present": gw_snat_rule.returncode == 0,
    }


def _validate_preflight_state(
    context: DirectGatewayExperimentContext,
    preflight: Dict[str, Any],
) -> None:
    conflicts: List[str] = []
    if preflight["ut_default_route_present"] and (
        context.ut_access_peer_ip not in preflight["ut_default_route_stdout"]
        or context.ut_access_interface not in preflight["ut_default_route_stdout"]
    ):
        conflicts.append("ut_default_route_conflict")
    if preflight["sat_policy_default_present"] and (
        str(context.forward_labels[0]) not in preflight["sat_policy_default_stdout"]
        or context.forward_segments[0].source_interface not in preflight["sat_policy_default_stdout"]
    ):
        conflicts.append("serving_satellite_policy_default_conflict")
    if preflight["sat_delivery_route_present"] and (
        context.ut_access_ip not in preflight["sat_delivery_route_stdout"]
        or context.ut_access_interface not in preflight["sat_delivery_route_stdout"]
    ):
        conflicts.append("serving_satellite_delivery_route_conflict")
    if preflight["gw_reverse_route_present"] and (
        str(context.reverse_labels[0]) not in preflight["gw_reverse_route_stdout"]
        or context.gateway_access_interface not in preflight["gw_reverse_route_stdout"]
    ):
        conflicts.append("gateway_reverse_route_conflict")
    for index, segment in enumerate(context.forward_segments[1:], start=1):
        state_key = f"{segment.source_node_id}:{context.forward_labels[index - 1]}"
        output = str(preflight.get("forward_lfib_state", {}).get(state_key, ""))
        if output and (
            str(context.forward_labels[index]) not in output
            or segment.source_interface not in output
        ):
            conflicts.append(f"forward_lfib_conflict:{state_key}")
    for index, segment in enumerate(context.reverse_segments[1:], start=1):
        state_key = f"{segment.source_node_id}:{context.reverse_labels[index - 1]}"
        output = str(preflight.get("reverse_lfib_state", {}).get(state_key, ""))
        if output and (
            str(context.reverse_labels[index]) not in output
            or segment.source_interface not in output
        ):
            conflicts.append(f"reverse_lfib_conflict:{state_key}")
    if conflicts:
        raise RuntimeError(
            "real runtime preflight found conflicting existing state: "
            + ", ".join(conflicts)
        )


def _realization_contexts(
    *,
    topology_path: Path,
    topology_snapshot: TopologySnapshot,
    runtime_update_plan: RuntimeUpdatePlan,
    target_ut_ids: Optional[Sequence[str]] = None,
) -> List[DirectGatewayExperimentContext]:
    topology = _load_json(topology_path)
    gateways = _topology_entry_by_id(topology.get("gateways", []), "gateway_id")
    contexts: List[DirectGatewayExperimentContext] = []
    used_gateway_nat_source_ips: Dict[str, set[str]] = {}
    for flow_context in _flow_contexts_from_plan(
        runtime_update_plan,
        target_ut_ids=target_ut_ids,
    ):
        satellite_path = [str(node_id) for node_id in flow_context.get("satellite_path", [])]
        forward_link_ids = [str(link_id) for link_id in flow_context.get("forward_link_ids", [])]
        reverse_link_ids = [str(link_id) for link_id in flow_context.get("reverse_link_ids", [])]
        forward_labels = tuple(int(label) for label in flow_context.get("forward_labels", []))
        reverse_labels = tuple(int(label) for label in flow_context.get("reverse_labels", []))
        serving_satellite_id = str(flow_context["serving_satellite_id"])
        selected_gateway_id = str(flow_context["selected_gateway_id"])
        selected_gateway_satellite_id = str(flow_context["selected_gateway_satellite_id"])
        gateway_class_id = str(flow_context.get("gateway_class_id") or "")
        if not gateway_class_id and flow_context.get("zone_id"):
            gateway_class_id = (
                f"{flow_context['zone_id']}:default-gateway:{selected_gateway_id}"
            )
        if not satellite_path or satellite_path[0] != serving_satellite_id:
            raise RuntimeError(f"invalid satellite path for {flow_context.get('flow_id')}")
        if satellite_path[-1] != selected_gateway_satellite_id:
            raise RuntimeError(
                "selected gateway satellite does not match the final satellite path node"
            )
        forward_nodes = [*satellite_path, selected_gateway_id]
        reverse_nodes = [selected_gateway_id, *reversed(satellite_path)]
        if len(forward_link_ids) != len(forward_nodes) - 1:
            raise RuntimeError(f"unexpected forward link count for {flow_context.get('flow_id')}")
        if len(reverse_link_ids) != len(reverse_nodes) - 1:
            raise RuntimeError(f"unexpected reverse link count for {flow_context.get('flow_id')}")
        if len(forward_labels) != len(forward_link_ids):
            raise RuntimeError(f"unexpected forward label count for {flow_context.get('flow_id')}")
        if len(reverse_labels) != len(reverse_link_ids):
            raise RuntimeError(f"unexpected reverse label count for {flow_context.get('flow_id')}")

        ut_link = _active_link_by_id(topology_snapshot, str(flow_context["ut_access_link_id"]))
        ut_endpoint = _endpoint_for_node(ut_link, str(flow_context["ut_id"]))
        sat_ut_endpoint = _endpoint_for_node(ut_link, serving_satellite_id)
        forward_segments: List[MplsPathSegment] = []
        for index, link_id in enumerate(forward_link_ids):
            link = _active_link_by_id(topology_snapshot, link_id)
            source_node_id = forward_nodes[index]
            destination_node_id = forward_nodes[index + 1]
            source_endpoint = _endpoint_for_node(link, source_node_id)
            destination_endpoint = _endpoint_for_node(link, destination_node_id)
            forward_segments.append(
                MplsPathSegment(
                    link_id=link_id,
                    source_node_id=source_node_id,
                    source_node_type=str(source_endpoint.get("node_type")),
                    source_interface=str(source_endpoint["interface_name"]),
                    source_ip=_strip_prefix(str(source_endpoint["ip_address"])),
                    destination_node_id=destination_node_id,
                    destination_node_type=str(destination_endpoint.get("node_type")),
                    destination_interface=str(destination_endpoint["interface_name"]),
                    destination_ip=_strip_prefix(str(destination_endpoint["ip_address"])),
                    label=forward_labels[index],
                )
            )
        reverse_segments: List[MplsPathSegment] = []
        for index, link_id in enumerate(reverse_link_ids):
            link = _active_link_by_id(topology_snapshot, link_id)
            source_node_id = reverse_nodes[index]
            destination_node_id = reverse_nodes[index + 1]
            source_endpoint = _endpoint_for_node(link, source_node_id)
            destination_endpoint = _endpoint_for_node(link, destination_node_id)
            reverse_segments.append(
                MplsPathSegment(
                    link_id=link_id,
                    source_node_id=source_node_id,
                    source_node_type=str(source_endpoint.get("node_type")),
                    source_interface=str(source_endpoint["interface_name"]),
                    source_ip=_strip_prefix(str(source_endpoint["ip_address"])),
                    destination_node_id=destination_node_id,
                    destination_node_type=str(destination_endpoint.get("node_type")),
                    destination_interface=str(destination_endpoint["interface_name"]),
                    destination_ip=_strip_prefix(str(destination_endpoint["ip_address"])),
                    label=reverse_labels[index],
                )
            )
        gateway_entry = gateways[selected_gateway_id]
        bootstrap_endpoint = dict(
            gateway_entry.get("bootstrap_network", {}).get("endpoint", {})
        )
        internet_uplink = dict(gateway_entry.get("internet_uplink", {}))
        gateway_nat_source_ip = _strip_prefix(
            str(
                bootstrap_endpoint.get("ip_address")
                or internet_uplink.get("ip_address")
            )
        )
        gateway_nat_source_ip = _gateway_nat_source_ip_for_flow(
            base_ip=gateway_nat_source_ip,
            ut_id=str(flow_context["ut_id"]),
            used_ips=used_gateway_nat_source_ips.setdefault(
                selected_gateway_id,
                {gateway_nat_source_ip},
            ),
        )
        contexts.append(
            DirectGatewayExperimentContext(
                flow_id=str(flow_context["flow_id"]),
                ut_id=str(flow_context["ut_id"]),
                zone_id=(
                    str(flow_context.get("zone_id"))
                    if flow_context.get("zone_id") is not None
                    else None
                ),
                serving_satellite_id=serving_satellite_id,
                selected_gateway_id=selected_gateway_id,
                selected_gateway_satellite_id=selected_gateway_satellite_id,
                zone_return_prefixes=tuple(
                    str(prefix)
                    for prefix in list(flow_context.get("zone_return_prefixes", []))
                    if str(prefix)
                ),
                ping_target_ip=str(flow_context["ping_target_ip"]),
                target_host_node_id=str(flow_context["target_host_node_id"]),
                policy_table_id=int(flow_context["policy_table_id"]),
                policy_rule_priority=int(flow_context["policy_rule_priority"]),
                satellite_path=tuple(satellite_path),
                forward_labels=forward_labels,
                reverse_labels=reverse_labels,
                ut_access_interface=str(ut_endpoint["interface_name"]),
                ut_access_ip=_strip_prefix(str(ut_endpoint["ip_address"])),
                ut_access_peer_ip=_strip_prefix(str(sat_ut_endpoint["ip_address"])),
                gateway_access_interface=str(forward_segments[-1].destination_interface),
                gateway_access_ip=str(forward_segments[-1].destination_ip),
                gateway_access_peer_ip=str(reverse_segments[0].destination_ip),
                gateway_nat_interface=str(internet_uplink.get("network_name")),
                gateway_nat_source_ip=gateway_nat_source_ip,
                forward_segments=tuple(forward_segments),
                reverse_segments=tuple(reverse_segments),
                validation_targets=tuple(
                    {
                        "node_id": str(target.get("node_id")),
                        "ip_address": str(target.get("ip_address")),
                    }
                    for target in flow_context.get("validation_targets", [])
                    if target.get("node_id") and target.get("ip_address")
                ),
                gateway_class_id=gateway_class_id or None,
                return_path_intent=dict(flow_context.get("return_path_intent", {})),
                traffic_scope=str(flow_context.get("traffic_scope") or ""),
            )
        )
    return contexts


class MinimalHostRuntimeManager:
    """Separate host/runtime boundary kept only as a transitional guardrail."""

    manager_name = "minimal_host_runtime_manager"

    def validate_real_execution(self, network_mutation_plan: NetworkMutationPlan) -> Dict[str, Any]:
        if len(network_mutation_plan.operations) != 0:
            raise HostRuntimeMutationBlocked(
                "real non-legacy runtime execution currently requires zero host-side "
                "NetworkMutationPlan operations"
            )
        return {
            "manager_name": self.manager_name,
            "execution_mode": "no_host_mutation_required",
            "mutation_id": network_mutation_plan.mutation_id,
            "operation_count": len(network_mutation_plan.operations),
            "future_runtime_target": "host_runtime_manager_daemon",
            "concurrency_model": "serial_bounded",
        }


class MinimalRealLinuxMplsInstaller:
    """Real-but-minimal Linux/MPLS installer using a transitional direct-container shim."""

    backend_name = "minimal_real_linux_mpls_installer"
    backend_kind = "nonlegacy_backend_specific_runtime_target"
    backend_version = "0.1.0"

    def _observation_endpoints(
        self,
        context: DirectGatewayExperimentContext,
    ) -> List[Tuple[str, str, str]]:
        endpoints: List[Tuple[str, str, str]] = [
            (context.ut_id, context.ut_access_interface, "ut_access"),
            (
                context.serving_satellite_id,
                context.ut_access_interface,
                "serving_satellite_ut_access",
            ),
            (
                context.serving_satellite_id,
                context.forward_segments[0].destination_interface,
                "serving_satellite_ingress",
            ),
        ]
        for index, segment in enumerate(context.forward_segments, start=1):
            endpoints.append(
                (
                    segment.source_node_id,
                    segment.source_interface,
                    f"forward_tx_hop_{index}",
                )
            )
            endpoints.append(
                (
                    segment.destination_node_id,
                    segment.destination_interface,
                    f"forward_rx_hop_{index}",
                )
            )
        endpoints.append(
            (
                context.selected_gateway_id,
                context.gateway_nat_interface,
                "gateway_nat_egress",
            )
        )
        deduped: List[Tuple[str, str, str]] = []
        seen: set[Tuple[str, str, str]] = set()
        for endpoint in endpoints:
            if endpoint in seen:
                continue
            seen.add(endpoint)
            deduped.append(endpoint)
        return deduped

    def _capture_interface_counters(
        self,
        endpoints: Sequence[Tuple[str, str, str]],
    ) -> Dict[str, Dict[str, Any]]:
        counters: Dict[str, Dict[str, Any]] = {}
        for node_id, interface_name, endpoint_role in endpoints:
            result = _run_docker_command(
                node_id=node_id,
                command=_interface_packet_counter_command(interface_name),
                phase="observe",
                description=f"read packet counters on {node_id}:{interface_name}",
            )
            rx_packets, tx_packets, ifindex, iflink = (
                result.stdout.split() + ["0", "0", "0", "0"]
            )[:4]
            counters[f"{node_id}:{interface_name}"] = {
                "node_id": node_id,
                "interface_name": interface_name,
                "endpoint_role": endpoint_role,
                "netns": node_id,
                "ifindex": _as_int(ifindex),
                "iflink": _as_int(iflink),
                "rx_packets": _as_int(rx_packets),
                "tx_packets": _as_int(tx_packets),
            }
        return counters

    def observe_packet_path(
        self,
        session: ActiveRuntimeSession,
        *,
        observe_selectors: Sequence[str],
    ) -> Dict[str, Any]:
        started_at = time.perf_counter()
        normalized_selectors = [str(selector).strip() for selector in observe_selectors if str(selector).strip()]
        if not normalized_selectors:
            return {
                "enabled": False,
                "observations": [],
                "selector_count": 0,
                "wall_seconds": 0.0,
            }
        observations: List[Dict[str, Any]] = []
        for selector in normalized_selectors:
            if ":" not in selector:
                raise RuntimeError(
                    f"invalid observe selector {selector!r}; expected ut_id:target_host_node_id_or_ip"
                )
            ut_id, target_selector = selector.split(":", 1)
            ut_contexts = [
                context
                for context in session.contexts
                if context.ut_id == ut_id
            ]
            matching_context = next(
                (
                    context
                    for context in ut_contexts
                    if _context_primary_target_matches(context, target_selector)
                ),
                None,
            )
            if matching_context is None:
                matching_context = next(
                    (
                        context
                        for context in ut_contexts
                        if _context_validation_target_matches(context, target_selector)
                    ),
                    None,
                )
            if matching_context is None:
                observations.append(
                    {
                        "selector": selector,
                        "matched": False,
                        "reason": "no_matching_context_in_active_slice",
                    }
                )
                continue
            target = _context_observation_target(matching_context, target_selector)
            endpoints = self._observation_endpoints(matching_context)
            before_counters = self._capture_interface_counters(endpoints)
            bpf_owned_observation = (
                str(dict(session.backend or {}).get("forwarding_backend") or "")
                == "bpf_owned"
            )
            before_bpf_counters: Dict[str, Any] = (
                _read_bpf_owned_packet_counters()
                if bpf_owned_observation
                else {"ok": False, "reason": "not_bpf_owned", "counters": {}}
            )
            ping_results, wall_seconds = _ping_validation_targets(
                matching_context,
                phase="observe",
                description="observe packet path with targeted ping",
                primary_only=bpf_owned_observation,
            )
            after_bpf_counters: Dict[str, Any] = (
                _read_bpf_owned_packet_counters()
                if bpf_owned_observation
                else {"ok": False, "reason": "not_bpf_owned", "counters": {}}
            )
            after_counters = self._capture_interface_counters(endpoints)
            counter_deltas: List[Dict[str, Any]] = []
            for endpoint_key, before in before_counters.items():
                after = after_counters[endpoint_key]
                delta = {
                    "node_id": before["node_id"],
                    "interface_name": before["interface_name"],
                    "endpoint_role": before["endpoint_role"],
                    "netns": before["netns"],
                    "ifindex_before": before["ifindex"],
                    "iflink_before": before["iflink"],
                    "ifindex_after": after["ifindex"],
                    "iflink_after": after["iflink"],
                    "rx_packets_before": before["rx_packets"],
                    "tx_packets_before": before["tx_packets"],
                    "rx_packets_after": after["rx_packets"],
                    "tx_packets_after": after["tx_packets"],
                    "rx_packets_delta": after["rx_packets"] - before["rx_packets"],
                    "tx_packets_delta": after["tx_packets"] - before["tx_packets"],
                }
                counter_deltas.append(delta)
            observations.append(
                {
                    "selector": selector,
                    "matched": True,
                    "flow_id": matching_context.flow_id,
                    "path_string": " -> ".join(
                        [
                            matching_context.ut_id,
                            *matching_context.satellite_path,
                            matching_context.selected_gateway_id,
                            str(target.get("node_id")),
                        ]
                    ),
                    "target_host_node_id": str(target.get("node_id")),
                    "target_ip_address": str(target.get("ip_address")),
                    "selected_gateway_id": matching_context.selected_gateway_id,
                    "serving_satellite_id": matching_context.serving_satellite_id,
                    "satellite_path": list(matching_context.satellite_path),
                    "path_hop_count": len(matching_context.satellite_path),
                    "ping_wall_seconds": wall_seconds,
                    "ping_results": [
                        result
                        for result in ping_results
                        if str(result.get("target_host_node_id")) == str(target.get("node_id"))
                    ],
                    "bpf_packet_counters_before": before_bpf_counters,
                    "bpf_packet_counters_after": after_bpf_counters,
                    "bpf_packet_counter_delta": (
                        _bpf_owned_packet_counter_delta(
                            before_bpf_counters,
                            after_bpf_counters,
                        )
                        if before_bpf_counters.get("ok") is True
                        and after_bpf_counters.get("ok") is True
                        else {}
                    ),
                    "interface_counter_deltas": counter_deltas,
                }
            )
        return {
            "enabled": True,
            "selector_count": len(normalized_selectors),
            "observations": observations,
            "wall_seconds": time.perf_counter() - started_at,
        }

    def assess_realization(
        self,
        *,
        runtime_update_plan: RuntimeUpdatePlan,
        target_ut_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        support = _realization_support_summary(
            runtime_update_plan,
            target_ut_ids=target_ut_ids,
        )
        return {
            "backend_name": self.backend_name,
            "supported": support["supported"],
            "total_flow_count": support["total_flow_count"],
            "direct_flow_count": len(support["direct_flow_ids"]),
            "multi_hop_flow_count": len(support["multi_hop_flow_ids"]),
            "direct_flow_ids": list(support["direct_flow_ids"]),
            "multi_hop_flow_ids": list(support["multi_hop_flow_ids"]),
            "blockers": list(support["blockers"]),
        }

    def _apply_specs(
        self,
        context: DirectGatewayExperimentContext,
        preflight: Dict[str, Any],
    ) -> List[RuntimeCommandSpec]:
        specs: List[RuntimeCommandSpec] = []
        required_platform_labels = max(
            1000,
            max(context.forward_labels) + 1,
            max(context.reverse_labels) + 1,
        )
        for node_id in context.nodes_requiring_platform_labels:
            if _as_int(preflight["node_platform_labels"].get(node_id, "0")) < required_platform_labels:
                specs.append(
                    RuntimeCommandSpec(
                        node_id=node_id,
                        phase="apply",
                        description=f"set {node_id} MPLS platform labels",
                        command=(
                            "sysctl",
                            "-w",
                            f"net.mpls.platform_labels={required_platform_labels}",
                        ),
                    )
                )
        if not preflight["sat_source_rule_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="apply",
                    description="install serving-satellite source rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            "ip rule show | grep -Fq "
                            f"'from {context.ut_access_ip} lookup {context.policy_table_id}' "
                            "|| ip rule add "
                            f"pref {context.policy_rule_priority} "
                            f"from {context.ut_access_ip}/32 "
                            f"table {context.policy_table_id}"
                        ),
                    ),
                )
            )
        for node_id, interface_name in context.mpls_input_interfaces:
            state_key = f"{node_id}:{interface_name}"
            if str(preflight["mpls_input_state"].get(state_key, "0")).strip() != "1":
                specs.append(
                    RuntimeCommandSpec(
                        node_id=node_id,
                        phase="apply",
                        description=f"enable MPLS input on {node_id}:{interface_name}",
                        command=(
                            "sysctl",
                            "-w",
                            f"net.mpls.conf.{interface_name}.input=1",
                        ),
                    )
                )
        first_forward_segment = context.forward_segments[0]
        specs.extend(
            [
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="apply",
                    description="install serving-satellite policy-table default route",
                    command=(
                        "ip",
                        "route",
                        "replace",
                        "default",
                        "encap",
                        "mpls",
                        str(context.forward_labels[0]),
                        "via",
                        "inet",
                        first_forward_segment.destination_ip,
                        "dev",
                        first_forward_segment.source_interface,
                        "table",
                        str(context.policy_table_id),
                    ),
                ),
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="apply",
                    description="install serving-satellite UT delivery route",
                    command=(
                        "ip",
                        "route",
                        "replace",
                        context.return_target_prefix,
                        "via",
                        context.ut_access_ip,
                        "dev",
                        context.ut_access_interface,
                    ),
                ),
            ]
        )
        for index, segment in enumerate(context.forward_segments[1:], start=1):
            specs.append(
                RuntimeCommandSpec(
                    node_id=segment.source_node_id,
                    phase="apply",
                    description=(
                        f"install forward LFIB on {segment.source_node_id} "
                        f"for label {context.forward_labels[index - 1]}"
                    ),
                    command=(
                        "ip",
                        "-f",
                        "mpls",
                        "route",
                        "replace",
                        str(context.forward_labels[index - 1]),
                        "as",
                        str(context.forward_labels[index]),
                        "via",
                        "inet",
                        segment.destination_ip,
                        "dev",
                        segment.source_interface,
                    ),
                )
            )
        specs.append(
            RuntimeCommandSpec(
                node_id=context.selected_gateway_id,
                phase="apply",
                description="install gateway forward pop entry",
                command=(
                    "ip",
                    "-f",
                    "mpls",
                    "route",
                    "replace",
                    str(context.forward_labels[-1]),
                    "dev",
                    "lo",
                ),
            )
        )
        specs.extend(
            [
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="apply",
                    description="install gateway forward filter rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -C FORWARD "
                            f"-i {context.gateway_access_interface} "
                            f"-o {context.gateway_nat_interface} "
                            f"-s {context.ut_access_ip}/32 -j ACCEPT 2>/dev/null || "
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -A FORWARD "
                            f"-i {context.gateway_access_interface} "
                            f"-o {context.gateway_nat_interface} "
                            f"-s {context.ut_access_ip}/32 -j ACCEPT"
                        ),
                    ),
                ),
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="apply",
                    description="install gateway reverse filter rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -C FORWARD "
                            f"-i {context.gateway_nat_interface} "
                            f"-o {context.gateway_access_interface} "
                            f"-d {context.ut_access_ip}/32 "
                            "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT "
                            "2>/dev/null || "
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -A FORWARD "
                            f"-i {context.gateway_nat_interface} "
                            f"-o {context.gateway_access_interface} "
                            f"-d {context.ut_access_ip}/32 "
                            "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
                        ),
                    ),
                ),
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="apply",
                    description="install gateway SNAT rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -t nat -C POSTROUTING "
                            f"-s {context.ut_access_ip}/32 "
                            f"-o {context.gateway_nat_interface} "
                            f"-j SNAT --to-source {context.gateway_nat_source_ip} "
                            "2>/dev/null || "
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -t nat -A POSTROUTING "
                            f"-s {context.ut_access_ip}/32 "
                            f"-o {context.gateway_nat_interface} "
                            f"-j SNAT --to-source {context.gateway_nat_source_ip}"
                        ),
                    ),
                ),
                RuntimeCommandSpec(
                    node_id=context.ut_id,
                    phase="apply",
                    description="install UT default route",
                    command=(
                        "ip",
                        "route",
                        "replace",
                        "default",
                        "via",
                        context.ut_access_peer_ip,
                        "dev",
                        context.ut_access_interface,
                        "src",
                        context.ut_access_ip,
                    ),
                ),
            ]
        )
        return specs

    def _bpf_owned_apply_specs(
        self,
        context: DirectGatewayExperimentContext,
        preflight: Dict[str, Any],
    ) -> List[RuntimeCommandSpec]:
        specs: List[RuntimeCommandSpec] = []
        if not preflight["gw_forward_rule_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="apply",
                    description="install gateway forward filter rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -C FORWARD "
                            f"-i {context.gateway_access_interface} "
                            f"-o {context.gateway_nat_interface} "
                            f"-s {context.ut_access_ip}/32 -j ACCEPT 2>/dev/null || "
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -A FORWARD "
                            f"-i {context.gateway_access_interface} "
                            f"-o {context.gateway_nat_interface} "
                            f"-s {context.ut_access_ip}/32 -j ACCEPT"
                        ),
                    ),
                )
            )
        if not preflight["gw_reverse_rule_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="apply",
                    description="install gateway reverse filter rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -C FORWARD "
                            f"-i {context.gateway_nat_interface} "
                            f"-o {context.gateway_access_interface} "
                            f"-d {context.ut_access_ip}/32 "
                            "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT "
                            "2>/dev/null || "
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -A FORWARD "
                            f"-i {context.gateway_nat_interface} "
                            f"-o {context.gateway_access_interface} "
                            f"-d {context.ut_access_ip}/32 "
                            "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
                        ),
                    ),
                )
            )
        if not preflight["gw_snat_rule_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="apply",
                    description="install gateway SNAT rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -t nat -C POSTROUTING "
                            f"-s {context.ut_access_ip}/32 "
                            f"-o {context.gateway_nat_interface} "
                            f"-j SNAT --to-source {context.gateway_nat_source_ip} "
                            "2>/dev/null || "
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -t nat -A POSTROUTING "
                            f"-s {context.ut_access_ip}/32 "
                            f"-o {context.gateway_nat_interface} "
                            f"-j SNAT --to-source {context.gateway_nat_source_ip}"
                        ),
                    ),
                )
            )
        if not preflight["ut_default_route_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.ut_id,
                    phase="apply",
                    description="install UT default route",
                    command=(
                        "ip",
                        "route",
                        "replace",
                        "default",
                        "via",
                        context.ut_access_peer_ip,
                        "dev",
                        context.ut_access_interface,
                        "src",
                        context.ut_access_ip,
                    ),
                )
            )
        return specs

    def _verify_specs(self, context: DirectGatewayExperimentContext) -> List[RuntimeCommandSpec]:
        specs: List[RuntimeCommandSpec] = [
            RuntimeCommandSpec(
                node_id=context.ut_id,
                phase="verify",
                description="show UT default route",
                command=_route_show_command("default"),
            ),
            RuntimeCommandSpec(
                node_id=context.serving_satellite_id,
                phase="verify",
                description="show serving-satellite source-policy rules",
                command=("ip", "rule", "show"),
            ),
            RuntimeCommandSpec(
                node_id=context.serving_satellite_id,
                phase="verify",
                description="show serving-satellite policy table",
                command=_route_show_command("table", str(context.policy_table_id)),
            ),
            RuntimeCommandSpec(
                node_id=context.serving_satellite_id,
                phase="verify",
                description="show serving-satellite reverse pop LFIB",
                command=("ip", "-f", "mpls", "route", "show"),
            ),
            RuntimeCommandSpec(
                node_id=context.selected_gateway_id,
                phase="verify",
                description="show gateway LFIB",
                command=("ip", "-f", "mpls", "route", "show"),
            ),
            RuntimeCommandSpec(
                node_id=context.selected_gateway_id,
                phase="verify",
                description="show gateway NAT table",
                command=("sh", "-lc", f"iptables -w {IPTABLES_WAIT_SECONDS} -t nat -S"),
            ),
        ]
        for node_id in dict.fromkeys(
            list(context.satellite_path) + [context.selected_gateway_id]
        ):
            specs.append(
                RuntimeCommandSpec(
                    node_id=node_id,
                    phase="verify",
                    description=f"show MPLS LFIB on {node_id}",
                    command=("ip", "-f", "mpls", "route", "show"),
                )
            )
        return specs

    def _rollback_specs(
        self,
        context: DirectGatewayExperimentContext,
        preflight: Dict[str, Any],
    ) -> List[RuntimeCommandSpec]:
        specs: List[RuntimeCommandSpec] = []
        required_platform_labels = max(
            1000,
            max(context.forward_labels) + 1,
            max(context.reverse_labels) + 1,
        )
        shared_gz_backbone_mode = (
            _context_linux_realization_mode(context) == "shared_gz_backbone"
        )
        if not preflight["ut_default_route_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.ut_id,
                    phase="rollback",
                    description="delete UT default route",
                    command=("ip", "route", "del", "default"),
                    allow_failure=True,
                )
            )
        if not preflight["sat_policy_default_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="rollback",
                    description="delete serving-satellite policy-table default route",
                    command=(
                        "ip",
                        "route",
                        "del",
                        "default",
                        "table",
                        str(context.policy_table_id),
                    ),
                    allow_failure=True,
                )
            )
        if not preflight["gw_snat_rule_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="rollback",
                    description="delete gateway SNAT rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -t nat -D POSTROUTING "
                            f"-s {context.ut_access_ip}/32 "
                            f"-o {context.gateway_nat_interface} "
                            f"-j SNAT --to-source {context.gateway_nat_source_ip}"
                        ),
                    ),
                    allow_failure=True,
                )
            )
        if not preflight["gw_forward_rule_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="rollback",
                    description="delete gateway forward filter rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -D FORWARD "
                            f"-i {context.gateway_access_interface} "
                            f"-o {context.gateway_nat_interface} "
                            f"-s {context.ut_access_ip}/32 -j ACCEPT"
                        ),
                    ),
                    allow_failure=True,
                )
            )
        if not preflight["gw_reverse_rule_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="rollback",
                    description="delete gateway reverse filter rule",
                    command=(
                        "sh",
                        "-lc",
                        (
                            f"iptables -w {IPTABLES_WAIT_SECONDS} -D FORWARD "
                            f"-i {context.gateway_nat_interface} "
                            f"-o {context.gateway_access_interface} "
                            f"-d {context.ut_access_ip}/32 "
                            "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
                        ),
                    ),
                    allow_failure=True,
                )
            )
        for index, segment in enumerate(context.forward_segments[1:], start=1):
            state_key = f"{segment.source_node_id}:{context.forward_labels[index - 1]}"
            if not str(preflight.get("forward_lfib_state", {}).get(state_key, "")).strip():
                specs.append(
                    RuntimeCommandSpec(
                        node_id=segment.source_node_id,
                        phase="rollback",
                        description=(
                            f"delete forward LFIB on {segment.source_node_id} "
                            f"for label {context.forward_labels[index - 1]}"
                        ),
                        command=(
                            "ip",
                            "-f",
                            "mpls",
                            "route",
                            "del",
                            str(context.forward_labels[index - 1]),
                        ),
                        allow_failure=True,
                    )
                )
        if not preflight["gw_forward_pop_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.selected_gateway_id,
                    phase="rollback",
                    description="delete gateway forward pop entry",
                    command=(
                        "ip",
                        "-f",
                        "mpls",
                        "route",
                        "del",
                        str(context.forward_labels[-1]),
                    ),
                    allow_failure=True,
                )
            )
        if not shared_gz_backbone_mode:
            for index, segment in enumerate(context.reverse_segments[1:], start=1):
                state_key = f"{segment.source_node_id}:{context.reverse_labels[index - 1]}"
                if not str(preflight.get("reverse_lfib_state", {}).get(state_key, "")).strip():
                    specs.append(
                        RuntimeCommandSpec(
                            node_id=segment.source_node_id,
                            phase="rollback",
                            description=(
                                f"delete reverse LFIB on {segment.source_node_id} "
                                f"for label {context.reverse_labels[index - 1]}"
                            ),
                            command=(
                                "ip",
                                "-f",
                                "mpls",
                                "route",
                                "del",
                                str(context.reverse_labels[index - 1]),
                            ),
                            allow_failure=True,
                        )
                    )
            if not preflight["gw_reverse_route_present"]:
                specs.append(
                    RuntimeCommandSpec(
                        node_id=context.selected_gateway_id,
                        phase="rollback",
                        description="delete gateway reverse ingress route",
                        command=("ip", "route", "del", context.return_target_prefix),
                        allow_failure=True,
                    )
                )
            if not preflight["sat_reverse_pop_present"]:
                specs.append(
                    RuntimeCommandSpec(
                        node_id=context.serving_satellite_id,
                        phase="rollback",
                        description="delete serving-satellite reverse pop entry",
                        command=(
                            "ip",
                            "-f",
                            "mpls",
                            "route",
                            "del",
                            str(context.reverse_labels[-1]),
                        ),
                        allow_failure=True,
                    )
                )
        if not preflight["sat_delivery_route_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="rollback",
                    description="delete serving-satellite UT delivery route",
                    command=("ip", "route", "del", context.return_target_prefix),
                    allow_failure=True,
                )
            )
        if not preflight["sat_source_rule_present"]:
            specs.append(
                RuntimeCommandSpec(
                    node_id=context.serving_satellite_id,
                    phase="rollback",
                    description="delete serving-satellite source rule",
                    command=(
                        "ip",
                        "rule",
                        "del",
                        "pref",
                        str(context.policy_rule_priority),
                        "from",
                        f"{context.ut_access_ip}/32",
                        "table",
                        str(context.policy_table_id),
                    ),
                    allow_failure=True,
                )
            )
        for node_id, interface_name in sorted(context.mpls_input_interfaces):
            state_key = f"{node_id}:{interface_name}"
            previous_value = str(preflight["mpls_input_state"].get(state_key, "0")).strip() or "0"
            if previous_value != "1":
                specs.append(
                    RuntimeCommandSpec(
                        node_id=node_id,
                        phase="rollback",
                        description=f"restore MPLS input state on {node_id}:{interface_name}",
                        command=(
                            "sysctl",
                            "-w",
                            f"net.mpls.conf.{interface_name}.input={previous_value}",
                        ),
                        allow_failure=True,
                    )
                )
        for node_id in sorted(context.nodes_requiring_platform_labels):
            previous_value = str(preflight["node_platform_labels"].get(node_id, "0")).strip() or "0"
            if _as_int(previous_value) < required_platform_labels:
                specs.append(
                    RuntimeCommandSpec(
                        node_id=node_id,
                        phase="rollback",
                        description=f"restore {node_id} MPLS platform labels",
                        command=(
                            "sysctl",
                            "-w",
                            f"net.mpls.platform_labels={previous_value}",
                        ),
                        allow_failure=True,
                    )
                )
        return specs

    def _rollback_verification(
        self,
        context: DirectGatewayExperimentContext,
    ) -> Dict[str, Any]:
        ut_default = _run_docker_command(
            node_id=context.ut_id,
            command=_route_show_command("default"),
            phase="rollback_verify",
            description="verify UT default route cleanup",
            allow_failure=True,
        )
        sat_policy_default = _run_docker_command(
            node_id=context.serving_satellite_id,
            command=_route_show_command("table", str(context.policy_table_id), "default"),
            phase="rollback_verify",
            description="verify serving-satellite policy default cleanup",
            allow_failure=True,
        )
        gw_forward_pop = _run_docker_command(
            node_id=context.selected_gateway_id,
            command=_mpls_route_show_command(context.forward_labels[-1]),
            phase="rollback_verify",
            description="verify gateway forward pop cleanup",
            allow_failure=True,
        )
        transit_forward_state: Dict[str, bool] = {}
        for index, segment in enumerate(context.forward_segments[1:], start=1):
            result = _run_docker_command(
                node_id=segment.source_node_id,
                command=_mpls_route_show_command(context.forward_labels[index - 1]),
                phase="rollback_verify",
                description=(
                    f"verify forward LFIB cleanup on {segment.source_node_id} "
                    f"for label {context.forward_labels[index - 1]}"
                ),
                allow_failure=True,
            )
            transit_forward_state[f"{segment.source_node_id}:{context.forward_labels[index - 1]}"] = bool(
                result.stdout
            )
        transit_reverse_state: Dict[str, bool] = {}
        for index, segment in enumerate(context.reverse_segments[1:], start=1):
            result = _run_docker_command(
                node_id=segment.source_node_id,
                command=_mpls_route_show_command(context.reverse_labels[index - 1]),
                phase="rollback_verify",
                description=(
                    f"verify reverse LFIB cleanup on {segment.source_node_id} "
                    f"for label {context.reverse_labels[index - 1]}"
                ),
                allow_failure=True,
            )
            transit_reverse_state[f"{segment.source_node_id}:{context.reverse_labels[index - 1]}"] = bool(
                result.stdout
            )
        return {
            "ut_default_route_present": bool(ut_default.stdout),
            "sat_policy_default_present": bool(sat_policy_default.stdout),
            "gw_forward_pop_present": bool(gw_forward_pop.stdout),
            "transit_forward_entries_present": transit_forward_state,
            "transit_reverse_entries_present": transit_reverse_state,
            "cleanup_success": not bool(ut_default.stdout)
            and not bool(sat_policy_default.stdout)
            and not bool(gw_forward_pop.stdout)
            and not any(transit_forward_state.values())
            and not any(transit_reverse_state.values()),
        }

    def _rollback_verification_probe_plan(
        self,
        contexts: Sequence[DirectGatewayExperimentContext],
        *,
        preserve_shared_zone_return_target_keys: Sequence[str] = (),
    ) -> Dict[str, Any]:
        specs: List[RuntimeCommandSpec] = []
        probe_ids: List[str] = []
        context_probes: List[Dict[str, Any]] = []
        shared_zone_return_group_probes: List[Dict[str, Any]] = []
        probe_by_command: Dict[Tuple[str, Tuple[str, ...]], str] = {}
        preserved_shared_target_keys = {
            str(target_key) for target_key in preserve_shared_zone_return_target_keys
        }

        def add_probe(
            *,
            node_id: str,
            description: str,
            command: Sequence[str],
        ) -> str:
            command_key = (str(node_id), tuple(command))
            existing_probe_id = probe_by_command.get(command_key)
            if existing_probe_id is not None:
                return existing_probe_id
            probe_id = f"p{len(probe_ids):04d}"
            probe_by_command[command_key] = probe_id
            probe_ids.append(probe_id)
            specs.append(
                RuntimeCommandSpec(
                    node_id=str(node_id),
                    phase="rollback_verify",
                    description=f"{description} ({probe_id})",
                    command=_probe_marker_command(probe_id, command),
                )
            )
            return probe_id

        for intent in _shared_gz_backbone_intents(_zone_return_install_intents(contexts)):
            if intent.target_key in preserved_shared_target_keys:
                shared_zone_return_group_probes.append(
                    {
                        "target_key": intent.target_key,
                        "selected_gateway_id": intent.selected_gateway_id,
                        "zone_id": intent.zone_id,
                        "zone_serving_satellite_id": intent.zone_serving_satellite_id,
                        "shared_group_cleanup_verification_skipped": True,
                        "skip_reason": "shared_zone_return_target_preserved_for_active_flows",
                        "route_probes": {},
                        "mpls_label_probes": {},
                    }
                )
                continue
            route_probes = {
                str(route_prefix): add_probe(
                    node_id=intent.selected_gateway_id,
                    description=(
                        "verify shared gateway zone-return route cleanup "
                        f"for {intent.target_key}"
                    ),
                    command=_route_show_command(str(route_prefix)),
                )
                for route_prefix in intent.route_prefixes
            }
            mpls_label_probes: Dict[str, str] = {}
            if intent.reverse_labels:
                mpls_label_probes[
                    f"{intent.zone_serving_satellite_id}:{intent.reverse_labels[-1]}"
                ] = add_probe(
                    node_id=intent.zone_serving_satellite_id,
                    description=(
                        "verify shared serving-satellite reverse pop cleanup "
                        f"for {intent.target_key}"
                    ),
                    command=_mpls_route_show_command(intent.reverse_labels[-1]),
                )
            for index, segment in enumerate(intent.reverse_segments[1:], start=1):
                label = intent.reverse_labels[index - 1]
                mpls_label_probes[f"{segment.source_node_id}:{label}"] = add_probe(
                    node_id=segment.source_node_id,
                    description=(
                        "verify shared reverse LFIB cleanup on "
                        f"{segment.source_node_id} for {intent.target_key}"
                    ),
                    command=_mpls_route_show_command(label),
                )
            shared_zone_return_group_probes.append(
                {
                    "target_key": intent.target_key,
                    "selected_gateway_id": intent.selected_gateway_id,
                    "zone_id": intent.zone_id,
                    "zone_serving_satellite_id": intent.zone_serving_satellite_id,
                    "shared_group_cleanup_verification_skipped": False,
                    "route_probes": route_probes,
                    "mpls_label_probes": mpls_label_probes,
                }
            )

        for context in contexts:
            transit_forward_probes: Dict[str, str] = {}
            for index, segment in enumerate(context.forward_segments[1:], start=1):
                state_key = f"{segment.source_node_id}:{context.forward_labels[index - 1]}"
                transit_forward_probes[state_key] = add_probe(
                    node_id=segment.source_node_id,
                    description=(
                        f"verify forward LFIB cleanup on {segment.source_node_id} "
                        f"for label {context.forward_labels[index - 1]}"
                    ),
                    command=_mpls_route_show_command(context.forward_labels[index - 1]),
                )
            shared_target_key = ":".join(_zone_return_target_key(context))
            skip_shared_reverse_cleanup_verification = (
                _context_linux_realization_mode(context) == "shared_gz_backbone"
                and shared_target_key in preserved_shared_target_keys
            )
            transit_reverse_probes: Dict[str, str] = {}
            if not skip_shared_reverse_cleanup_verification:
                for index, segment in enumerate(context.reverse_segments[1:], start=1):
                    state_key = f"{segment.source_node_id}:{context.reverse_labels[index - 1]}"
                    transit_reverse_probes[state_key] = add_probe(
                        node_id=segment.source_node_id,
                        description=(
                            f"verify reverse LFIB cleanup on {segment.source_node_id} "
                            f"for label {context.reverse_labels[index - 1]}"
                        ),
                        command=_mpls_route_show_command(context.reverse_labels[index - 1]),
                    )
            context_probes.append(
                {
                    "ut_id": context.ut_id,
                    "zone_return_target_key": shared_target_key,
                    "shared_reverse_cleanup_verification_skipped": (
                        skip_shared_reverse_cleanup_verification
                    ),
                    "ut_default_route_probe": add_probe(
                        node_id=context.ut_id,
                        description="verify UT default route cleanup",
                        command=_route_show_command("default"),
                    ),
                    "sat_policy_default_probe": add_probe(
                        node_id=context.serving_satellite_id,
                        description="verify serving-satellite policy default cleanup",
                        command=_route_show_command(
                            "table",
                            str(context.policy_table_id),
                            "default",
                        ),
                    ),
                    "gw_forward_pop_probe": add_probe(
                        node_id=context.selected_gateway_id,
                        description="verify gateway forward pop cleanup",
                        command=_mpls_route_show_command(context.forward_labels[-1]),
                    ),
                    "transit_forward_probes": transit_forward_probes,
                    "transit_reverse_probes": transit_reverse_probes,
                }
            )
        return {
            "probe_ids": probe_ids,
            "specs": specs,
            "context_probes": context_probes,
            "shared_zone_return_group_probes": shared_zone_return_group_probes,
        }

    def _rollback_verification_many(
        self,
        contexts: Sequence[DirectGatewayExperimentContext],
        *,
        max_node_workers: int,
        preserve_shared_zone_return_target_keys: Sequence[str] = (),
    ) -> Dict[str, Any]:
        started_at = time.perf_counter()
        plan = self._rollback_verification_probe_plan(
            contexts,
            preserve_shared_zone_return_target_keys=(
                preserve_shared_zone_return_target_keys
            ),
        )
        probe_specs = list(plan["specs"])
        probe_batches = _group_node_local_batches(probe_specs)
        probe_batch_plan = [batch.to_dict() for batch in probe_batches]
        probe_batch_results = _execute_node_local_batches(
            probe_batches,
            max_workers=max_node_workers,
        )
        probe_outputs = _parse_probe_outputs(
            probe_batch_results,
            probe_ids=list(plan["probe_ids"]),
        )
        shared_zone_return_group_verifications: List[Dict[str, Any]] = []
        for group_probe in list(plan["shared_zone_return_group_probes"]):
            route_state = {
                prefix: bool(probe_outputs.get(probe_id, "").strip())
                for prefix, probe_id in dict(group_probe["route_probes"]).items()
            }
            label_state = {
                state_key: bool(probe_outputs.get(probe_id, "").strip())
                for state_key, probe_id in dict(group_probe["mpls_label_probes"]).items()
            }
            skipped = bool(
                group_probe.get("shared_group_cleanup_verification_skipped")
            )
            shared_zone_return_group_verifications.append(
                {
                    "target_key": group_probe["target_key"],
                    "selected_gateway_id": group_probe["selected_gateway_id"],
                    "zone_id": group_probe["zone_id"],
                    "zone_serving_satellite_id": group_probe[
                        "zone_serving_satellite_id"
                    ],
                    "shared_group_cleanup_verification_skipped": skipped,
                    "skip_reason": group_probe.get("skip_reason"),
                    "gateway_zone_routes_present": route_state,
                    "shared_mpls_label_entries_present": label_state,
                    "cleanup_success": skipped
                    or (
                        not any(route_state.values())
                        and not any(label_state.values())
                    ),
                }
            )
        rollback_verifications: List[Dict[str, Any]] = []
        for context_probe in list(plan["context_probes"]):
            transit_forward_state = {
                state_key: bool(probe_outputs.get(probe_id, "").strip())
                for state_key, probe_id in dict(
                    context_probe["transit_forward_probes"]
                ).items()
            }
            transit_reverse_state = {
                state_key: bool(probe_outputs.get(probe_id, "").strip())
                for state_key, probe_id in dict(
                    context_probe["transit_reverse_probes"]
                ).items()
            }
            ut_default_route_present = bool(
                probe_outputs.get(
                    str(context_probe["ut_default_route_probe"]),
                    "",
                ).strip()
            )
            sat_policy_default_present = bool(
                probe_outputs.get(
                    str(context_probe["sat_policy_default_probe"]),
                    "",
                ).strip()
            )
            gw_forward_pop_present = bool(
                probe_outputs.get(
                    str(context_probe["gw_forward_pop_probe"]),
                    "",
                ).strip()
            )
            rollback_verifications.append(
                {
                    "ut_id": context_probe["ut_id"],
                    "ut_default_route_present": ut_default_route_present,
                    "zone_return_target_key": context_probe["zone_return_target_key"],
                    "shared_reverse_cleanup_verification_skipped": bool(
                        context_probe.get(
                            "shared_reverse_cleanup_verification_skipped"
                        )
                    ),
                    "sat_policy_default_present": sat_policy_default_present,
                    "gw_forward_pop_present": gw_forward_pop_present,
                    "transit_forward_entries_present": transit_forward_state,
                    "transit_reverse_entries_present": transit_reverse_state,
                    "cleanup_success": not ut_default_route_present
                    and not sat_policy_default_present
                    and not gw_forward_pop_present
                    and not any(transit_forward_state.values())
                    and not any(transit_reverse_state.values()),
                }
            )
        cleanup_success = all(
            bool(verification.get("cleanup_success"))
            for verification in rollback_verifications
        ) and all(
            bool(verification.get("cleanup_success"))
            for verification in shared_zone_return_group_verifications
        )
        return {
            "contexts": rollback_verifications,
            "shared_zone_return_groups": shared_zone_return_group_verifications,
            "cleanup_success": cleanup_success,
            "verification_mode": "batched_node_local_probe",
            "node_local_batch_plan": probe_batch_plan,
            "timing": {
                "rollback_verification_wall_seconds": time.perf_counter() - started_at,
                "rollback_verification_batch_timing": _batch_timing_summary(
                    probe_batch_results
                ),
                "rollback_verification_typed_operation_metrics": summarize_node_local_batches(
                    probe_batch_plan,
                    [batch.to_dict() for batch in probe_batch_results],
                ),
            },
        }

    def apply_and_validate(
        self,
        *,
        topology_path: Path,
        topology_snapshot: TopologySnapshot,
        runtime_update_plan: RuntimeUpdatePlan,
        distribution_batch: DistributedEnvelopeBatch,
        require_preview_validation: bool = True,
        target_ut_ids: Optional[Sequence[str]] = None,
        existing_session: Optional[ActiveRuntimeSession] = None,
        host_runtime_summary: Optional[Dict[str, Any]] = None,
        max_node_workers: int = 2,
        runtime_mode: str = "correctness",
        warmup_ut_ids: Optional[Sequence[str]] = None,
        ping_validation_mode: str = "full",
        ping_validation_scope: str = DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE,
        forwarding_backend: str = "linux_mpls",
        bpf_owned_readiness: Optional[Dict[str, Any]] = None,
        resident_bpf_attach_contexts: Optional[
            Sequence[DirectGatewayExperimentContext]
        ] = None,
    ) -> ActiveRuntimeSession:
        session_started_at = time.perf_counter()
        session_started_epoch_seconds = time.time()
        effective_bpf_owned_readiness = dict(bpf_owned_readiness or {})
        if not effective_bpf_owned_readiness and existing_session is not None:
            existing_backend = dict(existing_session.backend or {})
            if str(existing_backend.get("forwarding_backend") or "") == "bpf_owned":
                effective_bpf_owned_readiness = dict(
                    existing_backend.get("bpf_owned_readiness") or {}
                )
        backend_config = _normalize_forwarding_backend_config(
            forwarding_backend=forwarding_backend,
            bpf_owned_readiness=effective_bpf_owned_readiness,
        )
        preview_targets = sorted(distribution_batch.node_delivery_views)
        realization_support = self.assess_realization(
            runtime_update_plan=runtime_update_plan,
            target_ut_ids=target_ut_ids,
        )
        if realization_support["supported"] is not True:
            raise RuntimeError(
                "minimal real runtime path is not available for this slice: "
                + ", ".join(realization_support["blockers"])
            )
        all_contexts = tuple(
            _realization_contexts(
                topology_path=topology_path,
                topology_snapshot=topology_snapshot,
                runtime_update_plan=runtime_update_plan,
                target_ut_ids=target_ut_ids,
            )
        )
        host_mpls_summary: Dict[str, Any] = {
            "skipped": True,
            "reason": "host_forwarding_backend_preparation_deferred_until_after_incremental_cleanup",
            "wall_seconds": 0.0,
        }
        all_zone_return_install_intents = _zone_return_install_intents(all_contexts)
        incremental_selection = _select_incremental_runtime_contexts(
            all_contexts=all_contexts,
            existing_session=existing_session,
        )
        existing_preflights_by_flow_id = dict(
            incremental_selection["existing_preflights_by_flow_id"]
        )
        unchanged_flow_ids = set(incremental_selection["unchanged_flow_ids"])
        incremental_cleanup_flow_ids = list(
            incremental_selection["incremental_cleanup_flow_ids"]
        )
        incremental_cleanup_report: Dict[str, Any] | None = None
        contexts = tuple(incremental_selection["changed_contexts"])
        reapplied_zone_return_install_intents = _zone_return_install_intents_for_contexts(
            all_zone_return_install_intents,
            contexts,
        )
        zone_return_install_boundary = _zone_return_install_boundary_payload(
            all_contexts=all_contexts,
            reapplied_contexts=contexts,
            all_intents=all_zone_return_install_intents,
            reapplied_intents=reapplied_zone_return_install_intents,
        )
        if require_preview_validation:
            required_node_ids = {
                node_id
                for context in contexts
                for node_id in (
                    context.ut_id,
                    context.selected_gateway_id,
                    *context.satellite_path,
                )
            }
            for node_id in sorted(required_node_ids):
                if node_id not in preview_targets:
                    raise RuntimeError(
                        f"preview distribution is missing required target node {node_id}"
                    )

        apply_results: List[Dict[str, Any]] = []
        verify_results: List[Dict[str, Any]] = []
        ping_results: List[Dict[str, Any]] = []
        preflight_results: List[Dict[str, Any]] = []
        context_preflights: List[Dict[str, Any]] = []
        apply_batch_plan: List[Dict[str, Any]] = []
        verify_batch_plan: List[Dict[str, Any]] = []
        apply_batch_results: List[NodeLocalBatchResult] = []
        verify_batch_results: List[NodeLocalBatchResult] = []
        apply_backend_contract: Dict[str, Any] = {}
        verify_backend_contract: Dict[str, Any] = {}
        bpf_owned_packet_counters_after_apply: Dict[str, Any] = {
            "enabled": False,
            "reason": "forwarding_backend_not_bpf_owned",
            "bpf_classifier_miss_count": 0,
            "bpf_ping_path_hit_count": 0,
            "bpf_return_handoff_hit_count": 0,
        }
        failure_reason: Optional[str] = None
        failure_exception: Optional[BaseException] = None
        access_link_warmup_results: List[Dict[str, Any]] = []
        phase_timing: Dict[str, float] = {
            "host_mpls_prepare_wall_seconds": 0.0,
            "preflight_wall_seconds": 0.0,
            "apply_wall_seconds": 0.0,
            "verify_wall_seconds": 0.0,
            "access_link_warmup_wall_seconds": 0.0,
            "ping_wall_seconds": 0.0,
            "incremental_cleanup_wall_seconds": 0.0,
            "bpf_prepare_after_incremental_cleanup_wall_seconds": 0.0,
            "bpf_reuse_audit_seconds": 0.0,
            "bpf_map_compile_seconds": 0.0,
            "bpf_map_write_seconds": 0.0,
            "active_generation_flip_seconds": 0.0,
            "bpf_attach_seconds": 0.0,
            "bpf_live_role_read_seconds": 0.0,
        }
        phase_epoch_seconds: Dict[str, float] = {
            "session_started_epoch_seconds": session_started_epoch_seconds,
        }
        final_contexts = list(all_contexts)
        final_context_preflights: List[Dict[str, Any]] = []
        selected_warmup_ut_ids = (
            {str(ut_id) for ut_id in warmup_ut_ids}
            if warmup_ut_ids is not None
            else None
        )
        if ping_validation_mode not in {"full", "none"}:
            raise RuntimeError(
                f"unsupported ping_validation_mode: {ping_validation_mode}"
            )
        ping_validation_selection = _select_runtime_ping_validation_contexts(
            all_contexts=final_contexts,
            reapplied_contexts=contexts,
            validation_scope=ping_validation_scope,
        )
        target_slice_payload = {
            "slice_id": topology_snapshot.time_slice.get("slice_id"),
            "snapshot_id": topology_snapshot.snapshot_id,
            "contains_multi_hop": any(
                not context.is_direct_gateway_path for context in all_contexts
            ),
            "executed_ut_ids": [context.ut_id for context in all_contexts],
            "executed_flow_ids": [context.flow_id for context in all_contexts],
            "reapplied_flow_ids": [context.flow_id for context in contexts],
            "held_flow_ids": sorted(unchanged_flow_ids),
            "incremental_cleanup_flow_ids": list(incremental_cleanup_flow_ids),
            "active_flow_count": len(all_contexts),
            "reapplied_flow_count": len(contexts),
            "held_flow_count": len(unchanged_flow_ids),
            "incremental_cleanup_flow_count": len(incremental_cleanup_flow_ids),
            "reapplied_to_active_flow_ratio": _ratio(
                len(contexts),
                len(all_contexts),
            ),
            "held_to_active_flow_ratio": _ratio(
                len(unchanged_flow_ids),
                len(all_contexts),
            ),
            "zone_return_install_intent_count": len(all_zone_return_install_intents),
            "reapplied_zone_return_install_intent_count": len(
                reapplied_zone_return_install_intents
            ),
        }
        try:
            if incremental_cleanup_flow_ids:
                incremental_cleanup_started_at = time.perf_counter()
                incremental_cleanup_report = self.rollback_session(
                    _subset_active_runtime_session(
                        existing_session,
                        flow_ids=incremental_cleanup_flow_ids,
                    ),
                    max_node_workers=max_node_workers,
                    preserve_shared_zone_return_target_keys=(
                        intent.target_key for intent in all_zone_return_install_intents
                    ),
                    preserve_bpf_owned_backend=True,
                )
                phase_timing["incremental_cleanup_wall_seconds"] = (
                    time.perf_counter() - incremental_cleanup_started_at
                )
                if (
                    incremental_cleanup_report["rollback_verification"]["cleanup_success"]
                    is not True
                ):
                    raise RuntimeError(
                        "incremental cleanup failed before node-local apply for changed flows"
                    )
            backend_prepare_started_at = time.perf_counter()
            if backend_config["backend_mode"] == "bpf_owned":
                prepared_bpf_owned_readiness = _prepare_bpf_owned_readiness(
                    runtime_update_plan=runtime_update_plan,
                    topology_path=topology_path,
                    contexts=all_contexts,
                    resident_attach_contexts=resident_bpf_attach_contexts,
                    bpf_owned_readiness=effective_bpf_owned_readiness,
                    fail_closed_on_reuse_miss=(
                        str(runtime_mode) == "operational_clean"
                        and bool(effective_bpf_owned_readiness)
                    ),
                )
                backend_config = _normalize_forwarding_backend_config(
                    forwarding_backend=forwarding_backend,
                    bpf_owned_readiness=prepared_bpf_owned_readiness,
                )
            bpf_owned_ready = (
                backend_config["backend_mode"] == "bpf_owned"
                and bool(backend_config["bpf_owned_readiness"].get("ready"))
            )
            if bpf_owned_ready:
                host_mpls_summary = {
                    "skipped": True,
                    "reason": "bpf_owned_backend_ready_for_satellite_core_gz_forwarding",
                    "wall_seconds": 0.0,
                    "bpf_prepare_order": "after_incremental_cleanup_before_preflight",
                }
            elif backend_config["backend_mode"] == "bpf_owned":
                raise RuntimeError(
                    _bpf_owned_runtime_readiness_failure_message(
                        dict(backend_config.get("bpf_owned_readiness") or {})
                    )
                )
            else:
                host_mpls_summary = ensure_host_mpls_ready(auto_prepare=True)
            phase_timing["host_mpls_prepare_wall_seconds"] = float(
                host_mpls_summary.get("wall_seconds", 0.0)
            )
            phase_timing["bpf_prepare_after_incremental_cleanup_wall_seconds"] = (
                time.perf_counter() - backend_prepare_started_at
                if backend_config["backend_mode"] == "bpf_owned"
                else 0.0
            )
            if backend_config["backend_mode"] == "bpf_owned":
                prepare_timing = dict(
                    dict(
                        backend_config.get("bpf_owned_readiness") or {}
                    )
                    .get("prepare_steps", {})
                    .get("real_backend_prepare", {})
                    .get("timing_breakdown", {})
                    or {}
                )
                _copy_bpf_prepare_timing_to_phase_timing(
                    phase_timing,
                    prepare_timing,
                )
            preflight_started_at = time.perf_counter()
            for context in contexts:
                if runtime_mode in LIGHTWEIGHT_RUNTIME_MODES:
                    preflight = _default_preflight_state(context)
                elif backend_config["backend_mode"] == "bpf_owned":
                    preflight = _capture_bpf_owned_preflight_state(context)
                else:
                    preflight = _capture_preflight_state(context)
                    _validate_preflight_state(context, preflight)
                preflight_results.append(
                    {
                        "ut_id": context.ut_id,
                        "runtime_mode": runtime_mode,
                        **preflight,
                    }
                )
                context_preflights.append(preflight)
            phase_timing["preflight_wall_seconds"] = time.perf_counter() - preflight_started_at
            phase_epoch_seconds["preflight_completed_epoch_seconds"] = time.time()

            apply_specs = [
                spec
                for context, preflight in zip(contexts, context_preflights)
                for spec in (
                    self._bpf_owned_apply_specs(context, preflight)
                    if backend_config["backend_mode"] == "bpf_owned"
                    else self._apply_specs(context, preflight)
                )
            ]
            if backend_config["backend_mode"] != "bpf_owned":
                apply_specs.extend(
                    _shared_zone_return_apply_specs(
                        _shared_gz_backbone_intents(reapplied_zone_return_install_intents)
                    )
                )
            else:
                phase_timing["bpf_owned_legacy_shared_zone_return_apply_skipped"] = 1.0
            apply_specs, apply_backend_contract = _plan_backend_apply_contract(
                apply_specs,
                backend_config=backend_config,
                contract_phase="apply",
            )
            if (
                backend_config["backend_mode"] == "bpf_owned"
                and (
                    bool(apply_backend_contract.get("linux_gz_fallback_enabled"))
                    or int(
                        apply_backend_contract.get(
                            "linux_gz_fallback_command_count",
                            0,
                        )
                        or 0
                    )
                    > 0
                    or int(
                        apply_backend_contract.get(
                            "bpf_owned_planner_error_count",
                            0,
                        )
                        or 0
                    )
                    > 0
                    or int(
                        apply_backend_contract.get(
                            "eligible_core_fallback_to_linux",
                            0,
                        )
                        or 0
                    )
                    > 0
                    or (
                        int(
                            apply_backend_contract.get(
                                "bpf_owned_satellite_core_gz_scope_count",
                                0,
                            )
                            or 0
                        )
                        > 0
                        and int(
                            apply_backend_contract.get(
                                "skipped_by_bpf_backend_count",
                                0,
                            )
                            or 0
                        )
                        <= 0
                    )
                )
            ):
                readiness = dict(backend_config.get("bpf_owned_readiness") or {})
                raise RuntimeError(
                    "bpf_owned_strict_contract_violation:"
                    "eligible G/Z scope must be BPF-owned with no Linux fallback; "
                    f"readiness_reason={readiness.get('reason')}; "
                    f"readiness_fallback_reason={readiness.get('fallback_reason')}; "
                    "skipped_by_bpf_backend="
                    f"{int(apply_backend_contract.get('skipped_by_bpf_backend_count') or 0)}; "
                    "eligible_core_fallback_to_linux="
                    f"{int(apply_backend_contract.get('eligible_core_fallback_to_linux') or 0)}; "
                    "linux_gz_fallback_command_count="
                    f"{int(apply_backend_contract.get('linux_gz_fallback_command_count') or 0)}; "
                    "bpf_owned_planner_error_count="
                    f"{int(apply_backend_contract.get('bpf_owned_planner_error_count') or 0)}"
                )
            apply_batches = _group_node_local_batches(apply_specs)
            apply_batch_plan = [batch.to_dict() for batch in apply_batches]
            apply_started_at = time.perf_counter()
            apply_batch_results = _execute_node_local_batches(
                apply_batches,
                max_workers=max_node_workers,
            )
            phase_timing["apply_wall_seconds"] = time.perf_counter() - apply_started_at
            phase_epoch_seconds["apply_completed_epoch_seconds"] = time.time()
            apply_results = [batch.to_dict() for batch in apply_batch_results]

            if runtime_mode in LIGHTWEIGHT_RUNTIME_MODES or backend_config["backend_mode"] == "bpf_owned":
                verify_specs = []
                verify_batches = []
                verify_batch_plan = []
                verify_batch_results = []
                verify_results = []
                phase_timing["verify_wall_seconds"] = 0.0
                if backend_config["backend_mode"] == "bpf_owned":
                    verify_backend_contract = {
                        "contract_phase": "verify",
                        "backend_mode": "bpf_owned",
                        "legacy_linux_mpls_verification": {
                            "enabled": False,
                            "authoritative": False,
                            "reason": "strict_bpf_owned_uses_bpf_native_validation",
                        },
                        "requested_command_count": 0,
                        "linux_applied_command_count": 0,
                        "linux_gz_fallback_command_count": 0,
                        "eligible_core_fallback_to_linux": 0,
                        "bpf_owned_planner_error_count": 0,
                    }
            else:
                verify_specs = [
                    spec
                    for context in contexts
                    for spec in self._verify_specs(context)
                ]
                verify_specs, verify_backend_contract = _plan_backend_apply_contract(
                    verify_specs,
                    backend_config=backend_config,
                    contract_phase="verify",
                )
                verify_batches = _group_node_local_batches(verify_specs)
                verify_batch_plan = [batch.to_dict() for batch in verify_batches]
                verify_started_at = time.perf_counter()
                verify_batch_results = _execute_node_local_batches(
                    verify_batches,
                    max_workers=max_node_workers,
                )
                phase_timing["verify_wall_seconds"] = time.perf_counter() - verify_started_at
                phase_epoch_seconds["verify_completed_epoch_seconds"] = time.time()
                verify_results = [batch.to_dict() for batch in verify_batch_results]
            if "verify_completed_epoch_seconds" not in phase_epoch_seconds:
                phase_epoch_seconds["verify_completed_epoch_seconds"] = time.time()

            bpf_counters_before_ping_validation: Dict[str, Any] = {
                "ok": False,
                "reason": "not_bpf_owned",
                "counters": {},
            }
            if backend_config["backend_mode"] == "bpf_owned":
                bpf_counters_before_ping_validation = (
                    _read_bpf_owned_packet_counters()
                )
            final_context_preflights: List[Dict[str, Any]] = []
            new_preflights_by_flow_id = {
                str(context.flow_id): dict(preflight)
                for context, preflight in zip(contexts, context_preflights)
            }
            for final_context in final_contexts:
                flow_id = str(final_context.flow_id)
                if flow_id in unchanged_flow_ids:
                    final_context_preflights.append(
                        dict(existing_preflights_by_flow_id[flow_id])
                    )
                else:
                    final_context_preflights.append(
                        dict(new_preflights_by_flow_id[flow_id])
                    )

            if ping_validation_mode == "full":
                warmed_access_links: set[Tuple[str, str, str, str]] = set()
                for context in ping_validation_selection["selected_contexts"]:
                    access_link_key = (
                        context.ut_id,
                        context.ut_access_ip,
                        context.serving_satellite_id,
                        context.ut_access_peer_ip,
                    )
                    warmup_selected = (
                        backend_config["backend_mode"] != "bpf_owned"
                        and (
                            selected_warmup_ut_ids is None
                            or context.ut_id in selected_warmup_ut_ids
                        )
                    )
                    if warmup_selected and access_link_key not in warmed_access_links:
                        access_link_warmup_results.append(
                            _warm_direct_access_link(
                                node_id=context.ut_id,
                                target_ip=context.ut_access_peer_ip,
                                phase="warmup",
                                description="warm direct UT access link",
                            )
                        )
                        phase_timing["access_link_warmup_wall_seconds"] += float(
                            access_link_warmup_results[-1]["wall_seconds"]
                        )
                        warmed_access_links.add(access_link_key)
                    context_ping_results, ping_seconds = _ping_validation_targets(
                        context,
                        phase="verify",
                        description="ping Mini Internet host from UT",
                        primary_only=backend_config["backend_mode"] == "bpf_owned",
                    )
                    phase_timing["ping_wall_seconds"] += ping_seconds
                    ping_results.extend(context_ping_results)
                    if any(result["success"] is not True for result in context_ping_results):
                        failure_reason = f"ut_ping_failed_after_real_apply:{context.ut_id}"
                        break
            phase_epoch_seconds["access_link_warmup_completed_epoch_seconds"] = time.time()
            phase_epoch_seconds["ping_completed_epoch_seconds"] = time.time()
            if backend_config["backend_mode"] == "bpf_owned":
                bpf_counter_read = _read_bpf_owned_packet_counters()
                bpf_counter_source = bpf_counter_read
                bpf_counter_delta: Dict[str, int] = {}
                if (
                    bpf_counters_before_ping_validation.get("ok") is True
                    and bpf_counter_read.get("ok") is True
                ):
                    bpf_counter_delta = _bpf_owned_packet_counter_delta(
                        bpf_counters_before_ping_validation,
                        bpf_counter_read,
                    )
                    bpf_counter_source = {
                        "ok": True,
                        "enabled": bpf_counter_read.get("enabled", True),
                        "counters": bpf_counter_delta,
                        "source": "epoch_ping_validation_delta",
                        "before_map_reference": bpf_counters_before_ping_validation.get(
                            "map_reference"
                        ),
                        "after_map_reference": bpf_counter_read.get("map_reference"),
                    }
                bpf_owned_packet_counters_after_apply = {
                    "enabled": True,
                    **dict(bpf_counter_source),
                    "bpf_owned_packet_counters_before_ping_validation": dict(
                        bpf_counters_before_ping_validation
                    ),
                    "bpf_owned_packet_counters_cumulative": dict(
                        bpf_counter_read
                    ),
                    "bpf_owned_packet_counter_delta_during_validation": dict(
                        bpf_counter_delta
                    ),
                    **_bpf_owned_counter_artifact_fields(bpf_counter_source),
                }
                miss_drop = dict(
                    bpf_owned_packet_counters_after_apply.get("miss_drop_counters")
                    or {}
                )
                if any(
                    int(miss_drop.get(key) or 0) > 0
                    for key in (
                        "miss",
                        "drop_missing_label",
                        "drop_missing_generation",
                        "drop_bad_packet",
                        "drop",
                    )
                ):
                    failure_reason = "bpf_owned_epoch_validation_miss_drop"
                elif (
                    ping_validation_mode == "full"
                    and bool(
                        dict(ping_validation_selection.get("coverage") or {}).get(
                            "core_probe_selected"
                        )
                    )
                    and int(bpf_counter_delta.get("core_lfib_hit") or 0) <= 0
                ):
                    failure_reason = "bpf_owned_epoch_validation_core_lfib_not_exercised"
        except (RuntimeCommandFailure, NodeLocalBatchFailure, RuntimeError) as exc:
            failure_reason = str(exc)
            failure_exception = exc

        if failure_reason is not None:
            cleanup_session = ActiveRuntimeSession(
                session_id="failed-session-cleanup",
                backend={
                    "backend_name": self.backend_name,
                    "backend_kind": self.backend_kind,
                    "backend_version": self.backend_version,
                    "forwarding_backend": backend_config["backend_mode"],
                    "bpf_owned_readiness": dict(backend_config["bpf_owned_readiness"]),
                },
                target_slice=dict(target_slice_payload),
                host_runtime_boundary={},
                preflight={
                    "contexts": preflight_results,
                    "preview_targets": preview_targets,
                    "realization_support": realization_support,
                    "host_mpls_preparation": host_mpls_summary,
                    "access_link_warmup_results": access_link_warmup_results,
                    "zone_return_install_boundary": dict(zone_return_install_boundary),
                    "backend_apply_contract": dict(apply_backend_contract),
                    "backend_verify_contract": dict(verify_backend_contract),
                    "bpf_owned_packet_counters_after_apply": dict(
                        bpf_owned_packet_counters_after_apply
                    ),
                    "incremental_cleanup": (
                        {
                            "flow_ids": list(incremental_cleanup_flow_ids),
                            **dict(incremental_cleanup_report or {}),
                        }
                        if incremental_cleanup_flow_ids
                        else {
                            "flow_ids": [],
                            "rollback_results": [],
                            "rollback_verification": {"contexts": [], "cleanup_success": True},
                            "timing": {"rollback_wall_seconds": 0.0},
                            "node_local_batch_plan": [],
                        }
                    ),
                    "node_local_batch_plan": {
                        "apply": apply_batch_plan,
                        "verify": verify_batch_plan,
                        "rollback": [],
                    },
                    "runtime_mode": runtime_mode,
                    "ping_validation_mode": ping_validation_mode,
                    "ping_validation_scope": ping_validation_selection[
                        "validation_scope"
                    ],
                    "ping_validation_selection": {
                        key: value
                        for key, value in ping_validation_selection.items()
                        if key != "selected_contexts"
                    },
                },
                apply_results=apply_results,
                verify_results=verify_results,
                ping_results=ping_results,
                timing={
                    **phase_timing,
                    **phase_epoch_seconds,
                    "total_wall_seconds": time.perf_counter() - session_started_at,
                    "total_completed_epoch_seconds": time.time(),
                    "apply_batch_timing": _batch_timing_summary(apply_batch_results),
                    "verify_batch_timing": _batch_timing_summary(verify_batch_results),
                    "apply_typed_operation_metrics": summarize_node_local_batches(
                        apply_batch_plan,
                        apply_results,
                    ),
                    "verify_typed_operation_metrics": summarize_node_local_batches(
                        verify_batch_plan,
                        verify_results,
                    ),
                    "flow_path_observations": [
                        _context_path_summary(context) for context in all_contexts
                    ],
                    "zone_return_install_boundary": dict(zone_return_install_boundary),
                    "backend_apply_contract": dict(apply_backend_contract),
                    "backend_verify_contract": dict(verify_backend_contract),
                    "runtime_mode": runtime_mode,
                    "ping_validation_mode": ping_validation_mode,
                    "ping_validation_scope": ping_validation_selection[
                        "validation_scope"
                    ],
                    "ping_validation_target_count": len(
                        ping_validation_selection["selected_contexts"]
                    ),
                    "ping_validation_target_flow_ids": list(
                        ping_validation_selection["target_flow_ids"]
                    ),
                },
                contexts=contexts,
                context_preflights=tuple(context_preflights),
            )
            bpf_owned_packet_path_diagnostics = (
                _bpf_owned_packet_path_diagnostics(
                    installer=self,
                    session=cleanup_session,
                    failed_ping_results=ping_results,
                )
                if backend_config["backend_mode"] == "bpf_owned"
                else {
                    "schema_version": "skyseed-bpf-owned-packet-path-diagnostics-v1",
                    "enabled": False,
                    "reason": "forwarding_backend_not_bpf_owned",
                }
            )
            if (
                str(runtime_mode) == "operational_clean"
                and backend_config["backend_mode"] == "bpf_owned"
            ):
                rollback_report = {
                    "rollback_results": [],
                    "rollback_verification": {
                        "contexts": [],
                        "shared_zone_return_groups": [],
                        "cleanup_success": True,
                        "verification_mode": (
                            "operational_clean_bpf_owned_fail_fast_no_node_local_rollback"
                        ),
                        "cleanup_metrics": {
                            "requested_spec_count": 0,
                            "batch_count": 0,
                        },
                        "backend_rollback_contract": {
                            "contract_phase": "rollback",
                            "backend_mode": "bpf_owned",
                            "requested_command_count": 0,
                        },
                        "bpf_backend_cleanup": {
                            "attempted": False,
                            "reason": (
                                "operational_clean_defers_bpf_cleanup_to_final_artifact_cleanup"
                            ),
                            "rollback_completed": True,
                        },
                    },
                    "timing": {
                        "rollback_wall_seconds": 0.0,
                        "rollback_verification_skipped": True,
                        "rollback_batch_timeout_seconds": 0.0,
                    },
                    "node_local_batch_plan": [],
                    "cleanup_metrics": {
                        "requested_spec_count": 0,
                        "batch_count": 0,
                    },
                    "bpf_backend_cleanup": {
                        "attempted": False,
                        "reason": (
                            "operational_clean_defers_bpf_cleanup_to_final_artifact_cleanup"
                        ),
                        "rollback_completed": True,
                    },
                    "rollback_verification_batch_plan": [],
                }
            else:
                rollback_report = self.rollback_session(
                    cleanup_session,
                    max_node_workers=max_node_workers,
                )
            backend_failure_summary = (
                "backend_apply_skipped_by_bpf="
                f"{int(apply_backend_contract.get('skipped_by_bpf_backend_count') or 0)}; "
                "backend_apply_fallback_to_linux="
                f"{int(apply_backend_contract.get('fallback_to_linux_count') or 0)}; "
                "backend_apply_requested_commands="
                f"{int(apply_backend_contract.get('requested_command_count') or 0)}"
            )
            runtime_error = RuntimeError(
                f"{failure_reason}; {backend_failure_summary}; "
                "cleanup_success="
                f"{rollback_report['rollback_verification']['cleanup_success']}"
            )
            runtime_error.skyseed_details = {  # type: ignore[attr-defined]
                "forwarding_backend": backend_config["backend_mode"],
                "bpf_owned_readiness": dict(backend_config["bpf_owned_readiness"]),
                "backend_apply_contract": dict(apply_backend_contract),
                "backend_verify_contract": dict(verify_backend_contract),
                "bpf_owned_packet_path_diagnostics": dict(
                    bpf_owned_packet_path_diagnostics
                ),
                "rollback_cleanup_success": rollback_report[
                    "rollback_verification"
                ]["cleanup_success"],
                "bpf_backend_cleanup": dict(
                    rollback_report.get("bpf_backend_cleanup") or {}
                ),
            }
            raise runtime_error from failure_exception

        execution_seed = {
            "slice_id": topology_snapshot.time_slice.get("slice_id"),
            "contexts": [asdict(context) for context in contexts],
            "backend": self.backend_name,
            "mode": "apply_and_hold",
        }
        session_id = f"rtsess-{_fingerprint(execution_seed)}"
        return ActiveRuntimeSession(
            session_id=session_id,
            backend={
                "backend_name": self.backend_name,
                "backend_kind": self.backend_kind,
                "backend_version": self.backend_version,
                "forwarding_backend": backend_config["backend_mode"],
                "backend_switching": {
                    "available_modes": list(FORWARDING_BACKEND_MODES),
                    "selected_mode": backend_config["backend_mode"],
                    "linux_mpls": backend_config["backend_mode"] == "linux_mpls",
                    "bpf_shadow": backend_config["backend_mode"] == "bpf_shadow",
                    "bpf_canary": backend_config["backend_mode"] == "bpf_canary",
                    "bpf_owned": backend_config["backend_mode"] == "bpf_owned",
                },
                "bpf_owned_readiness": dict(backend_config["bpf_owned_readiness"]),
                "bpf_owned_packet_counters_after_apply": dict(
                    bpf_owned_packet_counters_after_apply
                ),
                "execution_mode": "real_apply_verify_hold",
                "runtime_boundary": "switchable_satellite_core_forwarding_backend",
                "execution_transport": "transitional_node_local_batch_via_docker_exec",
                "future_runtime_target": "node_local_agent_or_daemon",
                "concurrency_model": "per_node_serial_bounded_parallelism",
                "max_node_workers": max(1, int(max_node_workers)),
                "incremental_reapply_mode": (
                    "changed_flow_subset"
                    if existing_session is not None
                    else "full_slice_apply"
                ),
                "node_local_execution_split": {
                    "external_orchestrator_role": "publish_per_node_ordered_batches",
                    "node_local_role": "execute_ordered_batch_within_node_namespace",
                },
            },
            target_slice=dict(target_slice_payload),
            host_runtime_boundary=(
                dict(host_runtime_summary)
                if host_runtime_summary is not None
                else {
                    "execution_mode": "node_local_only",
                    "operation_count": 0,
                }
            ),
            preflight={
                "contexts": preflight_results,
                "preview_targets": preview_targets,
                "realization_support": realization_support,
                "host_mpls_preparation": host_mpls_summary,
                "access_link_warmup_results": access_link_warmup_results,
                "zone_return_install_boundary": dict(zone_return_install_boundary),
                "backend_apply_contract": dict(apply_backend_contract),
                "backend_verify_contract": dict(verify_backend_contract),
                "bpf_owned_packet_counters_after_apply": dict(
                    bpf_owned_packet_counters_after_apply
                ),
                "incremental_cleanup": (
                    {
                        "flow_ids": list(incremental_cleanup_flow_ids),
                        **dict(incremental_cleanup_report or {}),
                    }
                    if incremental_cleanup_flow_ids
                    else {
                        "flow_ids": [],
                        "rollback_results": [],
                        "rollback_verification": {"contexts": [], "cleanup_success": True},
                        "timing": {"rollback_wall_seconds": 0.0},
                        "node_local_batch_plan": [],
                    }
                ),
                "node_local_batch_plan": {
                    "apply": apply_batch_plan,
                    "verify": verify_batch_plan,
                    "rollback": [],
                },
                "runtime_mode": runtime_mode,
                "ping_validation_mode": ping_validation_mode,
                "ping_validation_scope": ping_validation_selection[
                    "validation_scope"
                ],
                "ping_validation_selection": {
                    key: value
                    for key, value in ping_validation_selection.items()
                    if key != "selected_contexts"
                },
            },
            apply_results=apply_results,
            verify_results=verify_results,
            ping_results=ping_results,
            timing={
                **phase_timing,
                **phase_epoch_seconds,
                "total_wall_seconds": time.perf_counter() - session_started_at,
                "total_completed_epoch_seconds": time.time(),
                "apply_batch_timing": _batch_timing_summary(apply_batch_results),
                "verify_batch_timing": _batch_timing_summary(verify_batch_results),
                "apply_typed_operation_metrics": summarize_node_local_batches(
                    apply_batch_plan,
                    apply_results,
                ),
                "verify_typed_operation_metrics": summarize_node_local_batches(
                    verify_batch_plan,
                    verify_results,
                ),
                "flow_path_observations": [
                    _context_path_summary(context) for context in all_contexts
                ],
                "zone_return_install_boundary": dict(zone_return_install_boundary),
                "backend_apply_contract": dict(apply_backend_contract),
                "backend_verify_contract": dict(verify_backend_contract),
                "runtime_mode": runtime_mode,
                "ping_validation_mode": ping_validation_mode,
                "ping_validation_scope": ping_validation_selection[
                    "validation_scope"
                ],
                "ping_validation_target_count": len(
                    ping_validation_selection["selected_contexts"]
                ),
                "ping_validation_target_flow_ids": list(
                    ping_validation_selection["target_flow_ids"]
                ),
            },
            contexts=all_contexts,
            context_preflights=tuple(final_context_preflights),
        )

    def probe_connectivity(
        self,
        session: ActiveRuntimeSession,
        *,
        primary_only: bool = False,
        warm_access_links: bool = False,
        include_ground_gateway_sanity: bool = True,
    ) -> Dict[str, Any]:
        started_at = time.perf_counter()
        flow_results: List[Dict[str, Any]] = []
        access_link_warmup_results: List[Dict[str, Any]] = []
        bpf_lifecycle_guard: Dict[str, Any] = {}
        if include_ground_gateway_sanity:
            ground_gateway_sanity = _ground_gateway_sanity_probe(
                session,
                primary_only=primary_only,
            )
        else:
            ground_gateway_sanity = {
                "schema_version": "skyseed-ground-gw-mini-internet-sanity-v1",
                "enabled": False,
                "skip_reason": "parameter_epoch_lightweight_validation",
                "primary_only": primary_only,
                "ok": True,
                "blockers": [],
                "flow_count": 0,
                "flow_results": [],
                "wall_seconds": 0.0,
            }
        if (
            str(dict(session.backend or {}).get("forwarding_backend") or "")
            == "bpf_owned"
        ):
            bpf_lifecycle_guard = _bpf_owned_final_validation_guard(
                session,
                primary_only=primary_only,
            )
            if bpf_lifecycle_guard.get("ok") is not True:
                return {
                    "slice_id": session.target_slice.get("slice_id"),
                    "wall_seconds": time.perf_counter() - started_at,
                    "flow_results": [],
                    "access_link_warmup_results": [],
                    "access_link_warmup_wall_seconds": 0.0,
                    "ground_gateway_sanity": ground_gateway_sanity,
                    "ping_success": False,
                    "failure_reason": "bpf_lifecycle_not_ready_for_validation",
                    **bpf_lifecycle_guard,
                }
        warmed_access_links: set[Tuple[str, str, str, str]] = set()
        for context in session.contexts:
            if (
                warm_access_links
                and str(dict(session.backend or {}).get("forwarding_backend") or "")
                != "bpf_owned"
            ):
                access_link_key = (
                    context.ut_id,
                    context.ut_access_ip,
                    context.serving_satellite_id,
                    context.ut_access_peer_ip,
                )
                if access_link_key not in warmed_access_links:
                    access_link_warmup_results.append(
                        _warm_direct_access_link(
                            node_id=context.ut_id,
                            target_ip=context.ut_access_peer_ip,
                            phase="observe",
                            description="warm direct UT access link before connectivity probe",
                        )
                    )
                    warmed_access_links.add(access_link_key)
            context_ping_results, _ping_seconds = _ping_validation_targets(
                context,
                phase="observe",
                description="probe Mini Internet host from UT before transition",
                primary_only=primary_only,
            )
            flow_results.extend(context_ping_results)
        bpf_counter_fields: Dict[str, Any] = {}
        bpf_strict_counter_success = True
        if (
            str(dict(session.backend or {}).get("forwarding_backend") or "")
            == "bpf_owned"
        ):
            bpf_counter_read = _read_bpf_owned_packet_counters()
            bpf_counter_source = bpf_counter_read
            bpf_counter_delta: Dict[str, int] = {}
            before_validation_counters = dict(
                bpf_lifecycle_guard.get("bpf_owned_packet_counters_before_validation")
                or {}
            )
            if (
                before_validation_counters.get("ok") is True
                and bpf_counter_read.get("ok") is True
            ):
                bpf_counter_delta = _bpf_owned_packet_counter_delta(
                    before_validation_counters,
                    bpf_counter_read,
                )
                bpf_counter_source = {
                    "ok": True,
                    "enabled": bpf_counter_read.get("enabled", True),
                    "counters": bpf_counter_delta,
                    "source": "validation_delta_after_lifecycle_guard",
                    "before_map_reference": before_validation_counters.get(
                        "map_reference"
                    ),
                    "after_map_reference": bpf_counter_read.get("map_reference"),
                }
            bpf_counter_fields = {
                "bpf_owned_packet_counters": bpf_counter_read,
                "bpf_owned_packet_counters_cumulative": bpf_counter_read,
                "bpf_owned_packet_counter_delta_during_validation": bpf_counter_delta,
                **_bpf_owned_counter_artifact_fields(bpf_counter_source),
                **bpf_lifecycle_guard,
            }
            if bpf_counter_delta:
                bpf_counter_fields["bpf_owned_packet_counters"] = bpf_counter_source
            miss_drop = dict(bpf_counter_fields.get("miss_drop_counters") or {})
            bpf_strict_counter_success = not any(
                int(miss_drop.get(key) or 0) > 0
                for key in (
                    "miss",
                    "drop_missing_label",
                    "drop_missing_generation",
                    "drop_bad_packet",
                    "drop",
                )
            )
            bpf_counter_fields["bpf_miss_drop_delta_clean"] = (
                bpf_strict_counter_success
            )
            if not bpf_strict_counter_success:
                bpf_counter_fields["failure_reason"] = "bpf_owned_validation_miss_drop"
        return {
            "slice_id": session.target_slice.get("slice_id"),
            "wall_seconds": time.perf_counter() - started_at,
            "flow_results": flow_results,
            "access_link_warmup_results": access_link_warmup_results,
            "access_link_warmup_wall_seconds": sum(
                float(result.get("wall_seconds", 0.0))
                for result in access_link_warmup_results
            ),
            "ground_gateway_sanity": ground_gateway_sanity,
            "ping_success": bool(flow_results)
            and all(result["success"] for result in flow_results)
            and bpf_strict_counter_success,
            **bpf_counter_fields,
        }

    def rollback_session(
        self,
        session: ActiveRuntimeSession,
        *,
        max_node_workers: int = 2,
        preserve_shared_zone_return_target_keys: Sequence[str] = (),
        preserve_bpf_owned_backend: bool = False,
        skip_verification: bool = False,
        node_local_batch_timeout_seconds: float = NODE_LOCAL_BATCH_TIMEOUT_SECONDS,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        started_at = time.perf_counter()
        preserved_shared_target_keys = {
            str(target_key) for target_key in preserve_shared_zone_return_target_keys
        }
        rollback_specs = _shared_zone_return_rollback_specs_for_contexts(
            session.contexts,
            preserve_target_keys=preserved_shared_target_keys,
        )
        rollback_specs.extend([
            spec
            for context, preflight in zip(
                reversed(session.contexts),
                reversed(session.context_preflights),
            )
            for spec in self._rollback_specs(context, preflight)
        ])
        backend_config = _normalize_forwarding_backend_config(
            forwarding_backend=str(
                dict(session.backend or {}).get("forwarding_backend") or "linux_mpls"
            ),
            bpf_owned_readiness=dict(
                dict(session.backend or {}).get("bpf_owned_readiness") or {}
            ),
        )
        rollback_specs, rollback_backend_contract = _plan_backend_apply_contract(
            rollback_specs,
            backend_config=backend_config,
            contract_phase="rollback",
        )
        rollback_batches = _group_node_local_batches(rollback_specs)
        rollback_cleanup_metrics = _node_local_batch_cleanup_metrics(
            requested_specs=rollback_specs,
            batches=rollback_batches,
            max_workers=max_node_workers,
        )
        rollback_batch_plan = [batch.to_dict() for batch in rollback_batches]
        rollback_batch_results = _execute_node_local_batches(
            rollback_batches,
            max_workers=max_node_workers,
            timeout_seconds=float(node_local_batch_timeout_seconds),
            progress_callback=progress_callback,
        )
        rollback_results = [batch.to_dict() for batch in rollback_batch_results]
        bpf_backend_cleanup = (
            {
                "attempted": False,
                "reason": "normal_runtime_update_preserves_bpf_owned_attach_and_maps",
                "rollback_completed": True,
                "attached_interfaces": list(
                    dict(dict(session.backend or {}).get("bpf_owned_readiness") or {}).get(
                        "attached_interfaces",
                        [],
                    )
                ),
            }
            if preserve_bpf_owned_backend
            and str(dict(session.backend or {}).get("forwarding_backend") or "")
            == "bpf_owned"
            else (
                _rollback_bpf_owned_prepare(
                    dict(dict(session.backend or {}).get("bpf_owned_readiness") or {})
                )
                if str(dict(session.backend or {}).get("forwarding_backend") or "")
                == "bpf_owned"
                else {
                    "attempted": False,
                    "reason": "forwarding_backend_not_bpf_owned",
                    "rollback_completed": True,
                    "attached_interfaces": [],
                }
            )
        )
        if (
            preserve_bpf_owned_backend
            and str(dict(session.backend or {}).get("forwarding_backend") or "")
            == "bpf_owned"
        ):
            verification_report = {
                "contexts": [],
                "shared_zone_return_groups": [],
                "cleanup_success": True,
                "verification_mode": "bpf_owned_normal_update_preserves_bpf_state",
                "timing": {"rollback_verification_wall_seconds": 0.0},
                "node_local_batch_plan": [],
                "legacy_linux_mpls_debug_probe": {
                    "enabled": False,
                    "authoritative": False,
                    "reason": "normal_runtime_update_preserves_bpf_owned_attach_and_maps",
                },
            }
        elif skip_verification:
            verification_report = {
                "contexts": [],
                "shared_zone_return_groups": [],
                "cleanup_success": all(
                    int(result.returncode) == 0 for result in rollback_batch_results
                ),
                "verification_mode": "skipped_bounded_operational_clean_failure_cleanup",
                "timing": {"rollback_verification_wall_seconds": 0.0},
                "node_local_batch_plan": [],
                "legacy_linux_mpls_debug_probe": {
                    "enabled": False,
                    "authoritative": False,
                    "reason": "bounded_operational_clean_failure_cleanup",
                },
            }
        else:
            verification_report = self._rollback_verification_many(
                tuple(reversed(session.contexts)),
                max_node_workers=max_node_workers,
                preserve_shared_zone_return_target_keys=preserved_shared_target_keys,
            )
        return {
            "rollback_results": rollback_results,
            "rollback_verification": {
                "contexts": list(verification_report["contexts"]),
                "shared_zone_return_groups": list(
                    verification_report["shared_zone_return_groups"]
                ),
                "cleanup_success": bool(verification_report["cleanup_success"]),
                "verification_mode": verification_report["verification_mode"],
                "cleanup_metrics": dict(rollback_cleanup_metrics),
                "backend_rollback_contract": dict(rollback_backend_contract),
                "bpf_backend_cleanup": dict(bpf_backend_cleanup),
            },
            "timing": {
                "rollback_wall_seconds": time.perf_counter() - started_at,
                "rollback_batch_timing": _batch_timing_summary(rollback_batch_results),
                "rollback_typed_operation_metrics": summarize_node_local_batches(
                    rollback_batch_plan,
                    rollback_results,
                ),
                "rollback_batch_timeout_seconds": float(
                    node_local_batch_timeout_seconds
                ),
                "rollback_verification_skipped": bool(skip_verification),
                "backend_rollback_contract": dict(rollback_backend_contract),
                "bpf_backend_cleanup": dict(bpf_backend_cleanup),
                **dict(verification_report["timing"]),
            },
            "node_local_batch_plan": rollback_batch_plan,
            "cleanup_metrics": dict(rollback_cleanup_metrics),
            "bpf_backend_cleanup": dict(bpf_backend_cleanup),
            "preserved_shared_zone_return_target_keys": sorted(
                preserved_shared_target_keys
            ),
            "rollback_verification_batch_plan": list(
                verification_report["node_local_batch_plan"]
            ),
        }

    def execute(
        self,
        *,
        topology_path: Path,
        topology_snapshot: TopologySnapshot,
        runtime_update_plan: RuntimeUpdatePlan,
        distribution_batch: DistributedEnvelopeBatch,
        require_preview_validation: bool = True,
        target_ut_ids: Optional[Sequence[str]] = None,
        host_runtime_summary: Optional[Dict[str, Any]] = None,
        max_node_workers: int = 2,
        runtime_mode: str = "correctness",
    ) -> RuntimeExecutionResult:
        session = self.apply_and_validate(
            topology_path=topology_path,
            topology_snapshot=topology_snapshot,
            runtime_update_plan=runtime_update_plan,
            distribution_batch=distribution_batch,
            require_preview_validation=require_preview_validation,
            target_ut_ids=target_ut_ids,
            host_runtime_summary=host_runtime_summary,
            max_node_workers=max_node_workers,
            runtime_mode=runtime_mode,
        )
        rollback_report = self.rollback_session(
            session,
            max_node_workers=max_node_workers,
        )
        ping_results = list(session.ping_results or [])
        primary_ping_result = ping_results[0] if ping_results else {
            "ut_id": None,
            "ping_target_ip": None,
            "target_host_node_id": None,
            "success": False,
            "stdout": "",
            "stderr": "",
            "returncode": None,
        }
        return RuntimeExecutionResult(
            execution_id=session.session_id,
            backend={**dict(session.backend or {}), "execution_mode": "real_apply_verify_rollback"},
            target_slice=dict(session.target_slice or {}),
            host_runtime_boundary=dict(session.host_runtime_boundary or {}),
            preflight={
                **dict(session.preflight or {}),
                "node_local_batch_plan": {
                    **dict((session.preflight or {}).get("node_local_batch_plan", {})),
                    "rollback": list(rollback_report["node_local_batch_plan"]),
                },
            },
            apply_results=list(session.apply_results),
            verify_results=list(session.verify_results),
            ping_result=primary_ping_result,
            rollback_results=list(rollback_report["rollback_results"]),
            rollback_verification=dict(rollback_report["rollback_verification"]),
            timing={
                **dict(session.timing or {}),
                **dict(rollback_report["timing"]),
                "total_wall_seconds": (
                    float((session.timing or {}).get("total_wall_seconds", 0.0))
                    + float(rollback_report["timing"].get("rollback_wall_seconds", 0.0))
                ),
            },
            outcome={
                "real_apply_success": True,
                "ping_success": bool(ping_results)
                and all(result["success"] for result in ping_results),
                "rollback_completed": bool(
                    rollback_report["rollback_verification"]["cleanup_success"]
                ),
                "failure_reason": None,
                "flow_results": ping_results,
                "batched_node_execution": True,
            },
        )
