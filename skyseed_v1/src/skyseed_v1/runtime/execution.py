"""Runtime execution contexts, command records, results, and session state.

These v1 records intentionally retain the Linux/MPLS compatibility fields used
by the validated route. Moving them does not claim that every field is a
future backend-neutral public contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


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
    gateway_selector_label: Optional[int] = None
    destination_prefix: Optional[str] = None

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


__all__ = [
    "ActiveRuntimeSession",
    "DirectGatewayExperimentContext",
    "HostRuntimeMutationBlocked",
    "MplsPathSegment",
    "NodeLocalBatchFailure",
    "NodeLocalBatchResult",
    "NodeLocalBatchSpec",
    "RuntimeCommandFailure",
    "RuntimeCommandResult",
    "RuntimeCommandSpec",
    "RuntimeExecutionResult",
    "SharedZoneReturnInstallPlan",
    "ZoneReturnInstallIntent",
]
