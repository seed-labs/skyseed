#!/usr/bin/env python3
# encoding: utf-8

"""Planner abstractions and the default Iteration 2 skeleton planner."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, DefaultDict, Deque, Dict, List, Optional, Sequence, Tuple

from contracts import (
    RUNTIME_ACTION_MODEL_VERSION,
    RUNTIME_UPDATE_PLAN_SCHEMA_VERSION,
    RuntimeUpdatePlan,
    TopologySnapshot,
    build_runtime_plan_action,
    build_mock_topology_snapshot,
)
from planner_validation import (
    PlannerValidationResult,
    validate_planner_flow,
    validate_planner_input,
    validate_planner_output,
)


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ExperimentFlowIntent:
    intent_id: str
    default_ping_target: str
    forward_label_seed: int
    reverse_label_seed: int
    policy_table_id: int
    policy_rule_priority: int


def _experiment_flow_intent(ut_id: str) -> ExperimentFlowIntent:
    ut_index = int(ut_id.removeprefix("ut"))
    return ExperimentFlowIntent(
        intent_id=f"ut-internet-egress:{ut_id}",
        default_ping_target="10.154.0.129",
        forward_label_seed=100 + (ut_index - 1) * 200,
        reverse_label_seed=200 + (ut_index - 1) * 200,
        policy_table_id=1000 + ut_index,
        policy_rule_priority=1000 + ut_index,
    )


def _gateway_links(topology_snapshot: TopologySnapshot) -> List[Dict[str, Any]]:
    return [
        link
        for link in topology_snapshot.active_links
        if str(link.get("slot_type")) == "gw_access"
    ]


def _gateway_attachment(
    topology_snapshot: TopologySnapshot,
    link: Dict[str, Any],
) -> Optional[Tuple[str, str]]:
    gateway_id: Optional[str] = None
    satellite_id: Optional[str] = None
    for endpoint in link.get("endpoints", []):
        node_id = str(endpoint.get("node_id"))
        node_type = topology_snapshot.nodes.get(node_id, {}).get("node_type")
        if node_type == "gateway":
            gateway_id = node_id
        elif node_type == "satellite":
            satellite_id = node_id
    if gateway_id is None or satellite_id is None:
        return None
    return gateway_id, satellite_id


class PlannerError(RuntimeError):
    """Base error for planner failures."""


class PlannerInputValidationError(PlannerError):
    """Raised when a topology snapshot does not satisfy planner requirements."""

    def __init__(self, result: PlannerValidationResult):
        self.result = result
        super().__init__(", ".join(result.issues) or "planner input validation failed")


class PlannerOutputValidationError(PlannerError):
    """Raised when a generated runtime update plan is invalid."""

    def __init__(self, result: PlannerValidationResult):
        self.result = result
        super().__init__(", ".join(result.issues) or "planner output validation failed")


class Planner(ABC):
    """Replaceable planner interface for TopologySnapshot -> RuntimeUpdatePlan."""

    planner_name: str
    planner_kind: str
    planner_version: str

    def metadata(self) -> Dict[str, str]:
        return {
            "planner_name": self.planner_name,
            "planner_kind": self.planner_kind,
            "planner_version": self.planner_version,
        }

    def validate_input(self, topology_snapshot: TopologySnapshot) -> PlannerValidationResult:
        return validate_planner_input(
            topology_snapshot,
            planner_name=self.planner_name,
            planner_kind=self.planner_kind,
        )

    def validate_output(
        self,
        topology_snapshot: TopologySnapshot,
        runtime_update_plan: RuntimeUpdatePlan,
    ) -> PlannerValidationResult:
        return validate_planner_output(
            topology_snapshot,
            runtime_update_plan,
            planner_name=self.planner_name,
            planner_kind=self.planner_kind,
        )

    def validate_flow(
        self,
        topology_snapshot: TopologySnapshot,
        runtime_update_plan: RuntimeUpdatePlan,
    ) -> PlannerValidationResult:
        return validate_planner_flow(
            topology_snapshot,
            runtime_update_plan,
            planner_name=self.planner_name,
            planner_kind=self.planner_kind,
        )

    def plan(self, topology_snapshot: TopologySnapshot) -> RuntimeUpdatePlan:
        input_result = self.validate_input(topology_snapshot)
        if not input_result.is_valid:
            raise PlannerInputValidationError(input_result)
        runtime_update_plan = self.build_runtime_update_plan(topology_snapshot)
        output_result = self.validate_output(topology_snapshot, runtime_update_plan)
        if not output_result.is_valid:
            raise PlannerOutputValidationError(output_result)
        return runtime_update_plan

    @abstractmethod
    def build_runtime_update_plan(
        self,
        topology_snapshot: TopologySnapshot,
    ) -> RuntimeUpdatePlan:
        """Build a full-state logical runtime plan for a topology snapshot."""


class DefaultPlannerSkeleton(Planner):
    """Minimal default planner that preserves contract flow without MPLS logic."""

    planner_name = "default_planner_skeleton"
    planner_kind = "planner_skeleton"
    planner_version = "0.1.0"

    def _node_action(
        self,
        *,
        action_id: str,
        action_type: str,
        node_id: str,
        sequence: int,
        intent: Dict[str, Any],
        expected_state: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        return build_runtime_plan_action(
            action_id=action_id,
            action_type=action_type,
            target_node_id=node_id,
            sequence=sequence,
            intent=intent,
            lifecycle={
                "idempotency_scope": "snapshot_id_and_target_node",
                "apply_behavior": "replace_or_confirm",
            },
            validation={
                "expected_state": expected_state,
            },
            metadata={
                "planner_family": "planner_skeleton",
                **metadata,
            },
        )

    def build_runtime_update_plan(
        self,
        topology_snapshot: TopologySnapshot,
    ) -> RuntimeUpdatePlan:
        actions_by_node: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)

        for zone_id, zone in sorted(topology_snapshot.zones.items()):
            serving_satellite_id = str(zone.get("active_serving_satellite_id") or "")
            if str(zone.get("resolution_state", "resolved")) != "resolved" or not serving_satellite_id:
                continue
            actions_by_node[serving_satellite_id].append(
                self._node_action(
                    action_id=f"zone-service:{zone_id}",
                    action_type="service_binding",
                    node_id=serving_satellite_id,
                    sequence=100 + len(actions_by_node[serving_satellite_id]) * 10,
                    intent={
                        "binding_kind": "zone_service",
                        "subject": {
                            "entity_type": "zone",
                            "entity_id": zone_id,
                        },
                        "target": {
                            "entity_type": "satellite",
                            "entity_id": serving_satellite_id,
                            "role": "active_responsible_serving_satellite",
                        },
                        "binding_scope": "slice_local",
                    },
                    expected_state=f"zone {zone_id} resolves to {serving_satellite_id}",
                    metadata={
                        "zone_id": zone_id,
                        "ut_ids": list(zone.get("ut_ids", [])),
                        "planner_semantics": "placeholder_for_future_zone_egress_policy",
                    },
                )
            )

        for ut_id, zone_id in sorted(topology_snapshot.ut_zone_membership.items()):
            attachment = dict(topology_snapshot.nodes[ut_id].get("attachment", {}))
            actions_by_node[ut_id].append(
                self._node_action(
                    action_id=f"ut-classification:{ut_id}",
                    action_type="traffic_classification",
                    node_id=ut_id,
                    sequence=100 + len(actions_by_node[ut_id]) * 10,
                    intent={
                        "match": {
                            "traffic_source": {
                                "entity_type": "user_terminal",
                                "entity_id": ut_id,
                            },
                            "attachment_state": attachment.get("state"),
                        },
                        "outcome": {
                            "zone_id": zone_id,
                            "serving_satellite_id": attachment.get(
                                "active_serving_satellite_id"
                            ),
                            "traffic_role": "ut_access_ingress",
                        },
                        "classification_scope": "node_local_ingress",
                    },
                    expected_state=f"{ut_id} remains mapped to {zone_id}",
                    metadata={
                        "ut_id": ut_id,
                        "zone_id": zone_id,
                        "attachment_state": attachment.get("state"),
                        "active_serving_satellite_id": attachment.get(
                            "active_serving_satellite_id"
                        ),
                        "planner_semantics": "placeholder_for_future_ut_ingress_policy",
                    },
                )
            )

        for link in _gateway_links(topology_snapshot):
            attachment = _gateway_attachment(topology_snapshot, link)
            if attachment is None:
                continue
            gateway_id, satellite_id = attachment
            actions_by_node[gateway_id].append(
                self._node_action(
                    action_id=f"gateway-service:{gateway_id}",
                    action_type="service_binding",
                    node_id=gateway_id,
                    sequence=100 + len(actions_by_node[gateway_id]) * 10,
                    intent={
                        "binding_kind": "gateway_egress",
                        "subject": {
                            "entity_type": "gateway",
                            "entity_id": gateway_id,
                        },
                        "target": {
                            "entity_type": "satellite",
                            "entity_id": satellite_id,
                            "role": "attached_gateway_satellite",
                        },
                        "binding_scope": "slice_local",
                    },
                    expected_state=f"{gateway_id} observed on {satellite_id}",
                    metadata={
                        "gateway_id": gateway_id,
                        "attached_satellite_id": satellite_id,
                        "link_id": link.get("link_id"),
                        "planner_semantics": "placeholder_for_future_gateway_egress_policy",
                    },
                )
            )
            actions_by_node[satellite_id].append(
                self._node_action(
                    action_id=f"gateway-anchor:{satellite_id}:{gateway_id}",
                    action_type="service_binding",
                    node_id=satellite_id,
                    sequence=100 + len(actions_by_node[satellite_id]) * 10,
                    intent={
                        "binding_kind": "gateway_reachability_anchor",
                        "subject": {
                            "entity_type": "satellite",
                            "entity_id": satellite_id,
                        },
                        "target": {
                            "entity_type": "gateway",
                            "entity_id": gateway_id,
                            "role": "local_gateway_egress",
                        },
                        "binding_scope": "slice_local",
                    },
                    expected_state=f"{satellite_id} exposes {gateway_id} egress",
                    metadata={
                        "gateway_id": gateway_id,
                        "satellite_id": satellite_id,
                        "link_id": link.get("link_id"),
                        "planner_semantics": "placeholder_for_future_gateway_reachability_policy",
                    },
                )
            )

        node_subplans: Dict[str, Dict[str, Any]] = {}
        for node_id in sorted(actions_by_node):
            node_subplans[node_id] = {
                "target_node_id": node_id,
                "target_node_type": topology_snapshot.nodes[node_id].get("node_type"),
                "apply_mode": "ordered_local_actions",
                "actions": sorted(
                    actions_by_node[node_id],
                    key=lambda action: int(action.get("sequence", 0)),
                ),
                "validation_expectations": {
                    "post_apply_validation_expected": True,
                    "validation_scope": "planner_skeleton_contract_only",
                },
            }

        plan_seed = {
            "snapshot_id": topology_snapshot.snapshot_id,
            "planner": self.metadata(),
            "node_ids": sorted(node_subplans),
            "zone_serving": {
                zone_id: zone.get("active_serving_satellite_id")
                for zone_id, zone in sorted(topology_snapshot.zones.items())
            },
        }
        plan_id = f"rtplan-{_fingerprint(plan_seed)}"
        return RuntimeUpdatePlan(
            schema_version=RUNTIME_UPDATE_PLAN_SCHEMA_VERSION,
            plan_id=plan_id,
            target_snapshot={
                "snapshot_id": topology_snapshot.snapshot_id,
                "slice_id": topology_snapshot.time_slice.get("slice_id"),
            },
            planner={
                **self.metadata(),
                "source": "planner_skeleton",
            },
            apply_model={
                "state_model": "full_state_intent",
                "action_model": RUNTIME_ACTION_MODEL_VERSION,
                "execution_projection": "node_local_subplans",
                "agent_policy_visibility": "none",
            },
            node_subplans=node_subplans,
            replacement_semantics={
                "replayable": True,
                "idempotency_scope": "snapshot_id_and_target_node",
                "rollback_on_failure": "best_effort",
            },
            validation_expectations={
                "planner_side_validation": "required_before_publish",
                "agent_side_validation": "required_before_local_apply",
                "installer_side_validation": "required_after_local_apply",
            },
        )


def _satellite_adjacency(
    topology_snapshot: TopologySnapshot,
) -> Dict[str, set[str]]:
    adjacency: Dict[str, set[str]] = defaultdict(set)
    for link in topology_snapshot.active_links:
        slot_type = str(link.get("slot_type"))
        if slot_type not in {"same_plane_isl", "fixed_isl", "cross_plane_isl"}:
            continue
        endpoint_ids = [
            str(endpoint.get("node_id"))
            for endpoint in link.get("endpoints", [])
            if topology_snapshot.nodes.get(str(endpoint.get("node_id")), {}).get("node_type")
            == "satellite"
        ]
        if len(endpoint_ids) != 2:
            continue
        first, second = endpoint_ids
        adjacency[first].add(second)
        adjacency[second].add(first)
    return adjacency


def _shortest_satellite_path(
    *,
    adjacency: Dict[str, set[str]],
    start_node_id: str,
    goal_node_id: str,
) -> Optional[List[str]]:
    if start_node_id == goal_node_id:
        return [start_node_id]
    frontier: Deque[List[str]] = deque([[start_node_id]])
    visited = {start_node_id}
    while frontier:
        path = frontier.popleft()
        last = path[-1]
        for neighbor in sorted(adjacency.get(last, set())):
            if neighbor in visited:
                continue
            next_path = [*path, neighbor]
            if neighbor == goal_node_id:
                return next_path
            visited.add(neighbor)
            frontier.append(next_path)
    return None


def _link_between_nodes(
    topology_snapshot: TopologySnapshot,
    first_node_id: str,
    second_node_id: str,
) -> Optional[Dict[str, Any]]:
    target_ids = {first_node_id, second_node_id}
    for link in topology_snapshot.active_links:
        endpoint_ids = {
            str(endpoint.get("node_id"))
            for endpoint in link.get("endpoints", [])
        }
        if endpoint_ids == target_ids:
            return link
    return None


class UtInternetExperimentPlanner(Planner):
    """First usable planner for the UT->MiniInternet experiment path."""

    planner_name = "ut_internet_experiment_planner"
    planner_kind = "gz_mpls_experiment"
    planner_version = "0.1.1"
    _SHARED_LABEL_BLOCK_SIZE = 256
    _SHARED_FORWARD_LABEL_BASE = 10000
    _SHARED_REVERSE_LABEL_BASE = 200000

    def __init__(self) -> None:
        self._reset_runtime_build_state()

    def _reset_runtime_build_state(self) -> None:
        self._shared_backbone_label_allocations: Dict[str, Dict[str, Any]] = {}

    def _shared_backbone_label_scope_id(
        self,
        *,
        serving_satellite_id: str,
        selected_gateway_id: str,
        selected_gateway_satellite_id: str,
        forward_link_ids: Sequence[str],
    ) -> str:
        return f"shared-backbone:{_fingerprint({'serving_satellite_id': serving_satellite_id, 'selected_gateway_id': selected_gateway_id, 'selected_gateway_satellite_id': selected_gateway_satellite_id, 'forward_link_ids': list(forward_link_ids)})}"

    def _shared_backbone_label_seeds(
        self,
        *,
        serving_satellite_id: str,
        selected_gateway_id: str,
        selected_gateway_satellite_id: str,
        forward_link_ids: Sequence[str],
        label_count: int,
    ) -> Tuple[int, int, str]:
        if label_count > self._SHARED_LABEL_BLOCK_SIZE:
            raise PlannerError(
                "shared backbone label block exhausted for one path: "
                f"{serving_satellite_id}->{selected_gateway_id} requires {label_count} labels"
            )
        scope_id = self._shared_backbone_label_scope_id(
            serving_satellite_id=serving_satellite_id,
            selected_gateway_id=selected_gateway_id,
            selected_gateway_satellite_id=selected_gateway_satellite_id,
            forward_link_ids=forward_link_ids,
        )
        allocation = self._shared_backbone_label_allocations.get(scope_id)
        if allocation is None:
            allocation_index = len(self._shared_backbone_label_allocations)
            allocation = {
                "label_count": label_count,
                "forward_label_seed": (
                    self._SHARED_FORWARD_LABEL_BASE
                    + allocation_index * self._SHARED_LABEL_BLOCK_SIZE
                ),
                "reverse_label_seed": (
                    self._SHARED_REVERSE_LABEL_BASE
                    + allocation_index * self._SHARED_LABEL_BLOCK_SIZE
                ),
            }
            self._shared_backbone_label_allocations[scope_id] = allocation
        elif int(allocation.get("label_count", 0)) != int(label_count):
            raise PlannerError(
                "shared backbone label allocation conflict for "
                f"{scope_id}: expected {allocation.get('label_count')} labels "
                f"but received {label_count}"
            )
        return (
            int(allocation["forward_label_seed"]),
            int(allocation["reverse_label_seed"]),
            scope_id,
        )

    def _node_action(
        self,
        *,
        action_id: str,
        action_type: str,
        node_id: str,
        sequence: int,
        intent: Dict[str, Any],
        expected_state: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        return build_runtime_plan_action(
            action_id=action_id,
            action_type=action_type,
            target_node_id=node_id,
            sequence=sequence,
            intent=intent,
            lifecycle={
                "idempotency_scope": "snapshot_id_and_target_node",
                "apply_behavior": "replace_or_confirm",
            },
            validation={"expected_state": expected_state},
            metadata={
                "planner_family": "gz_mpls_experiment",
                **metadata,
            },
        )

    def _gateway_candidates(
        self,
        topology_snapshot: TopologySnapshot,
    ) -> List[Tuple[str, str, Dict[str, Any]]]:
        candidates: List[Tuple[str, str, Dict[str, Any]]] = []
        for link in _gateway_links(topology_snapshot):
            attachment = _gateway_attachment(topology_snapshot, link)
            if attachment is None:
                continue
            gateway_id, satellite_id = attachment
            candidates.append((gateway_id, satellite_id, link))
        return sorted(candidates, key=lambda item: (item[0], item[1], item[2].get("link_id")))

    def _gateway_class_id(self, *, zone_id: str, gateway_id: str) -> str:
        return f"{zone_id}:default-gateway:{gateway_id}"

    def _return_path_intent(
        self,
        *,
        ut_id: str,
        zone_id: str,
        zone_return_prefixes: Sequence[str],
        serving_satellite_id: str,
        selected_gateway_id: str,
        selected_gateway_satellite_id: str,
        reverse_link_ids: Sequence[str],
        reverse_labels: Sequence[int],
        shared_backbone_label_scope_id: str,
    ) -> Dict[str, Any]:
        shared_return_target = {
            "target_kind": "gateway_zone_return",
            "target_key": (
                f"{selected_gateway_id}:{zone_id}:{serving_satellite_id}"
            ),
            "gateway_id": selected_gateway_id,
            "zone_id": zone_id,
            "zone_serving_satellite_id": serving_satellite_id,
            "state_owner": "satellite_backbone",
            "state_model": "shared_gateway_zone_or_zone_return_reachability",
        }
        candidate_zone_route = {
            "target_kind": "gateway_zone_return_route_candidate",
            "target_key": f"{shared_return_target['target_key']}:zone-route",
            "shared_backbone_return_target_key": shared_return_target["target_key"],
            "route_scope": "zone_or_gateway_zone_return_reachability",
            "route_owner": "satellite_backbone",
            "gateway_id": selected_gateway_id,
            "zone_id": zone_id,
            "route_prefixes": [
                str(prefix) for prefix in zone_return_prefixes if str(prefix)
            ],
            "zone_serving_satellite_id": serving_satellite_id,
            "egress_node_id": serving_satellite_id,
            "current_compatibility_route_scope": "ut_32_return_route",
            "target_route_scope": "zone_or_gateway_zone_return_target",
            "state_model": "candidate_shared_zone_return_route",
            "install_behavior": "installed_by_shared_gz_backbone_runtime",
        }
        reverse_path_signature = ":".join(str(link_id) for link_id in reverse_link_ids)
        candidate_label_stack = {
            "target_kind": "gateway_zone_return_label_stack_candidate",
            "target_key": (
                f"{shared_return_target['target_key']}:reverse-path:"
                f"{_fingerprint({'reverse_link_ids': list(reverse_link_ids)})}"
            ),
            "shared_backbone_return_target_key": shared_return_target["target_key"],
            "reverse_path_signature": reverse_path_signature,
            "current_compatibility_label_scope_id": shared_backbone_label_scope_id,
            "current_compatibility_reverse_label_count": len(list(reverse_labels)),
            "current_compatibility_reverse_labels": [
                int(label) for label in reverse_labels
            ],
            "state_owner": "satellite_backbone",
            "state_model": "candidate_shared_gateway_zone_return_label_stack",
            "install_behavior": "installed_by_shared_gz_backbone_runtime",
        }
        return {
            "intent_kind": "zone_return_delivery",
            "semantic_scope": "gateway_to_zone_then_local_ut_delivery",
            "zone_id": zone_id,
            "ut_id": ut_id,
            "selected_gateway_id": selected_gateway_id,
            "selected_gateway_satellite_id": selected_gateway_satellite_id,
            "zone_serving_satellite_id": serving_satellite_id,
            "gateway_return_role": "zone_return_ingress",
            "backbone_forwarding_target": {
                "target_kind": "zone_responsible_satellite",
                "entity_type": "zone",
                "entity_id": zone_id,
                "resolution": "active_responsible_serving_satellite",
                "resolved_node_id": serving_satellite_id,
                "shared_backbone_return_target_key": (
                    shared_return_target["target_key"]
                ),
                "state_owner": "satellite_backbone",
                "state_model": "shared_gateway_zone_or_zone_return_reachability",
            },
            "serving_satellite_delivery_target": {
                "target_kind": "ut_local_access_delivery",
                "entity_type": "user_terminal",
                "entity_id": ut_id,
                "delivery_scope": "serving_satellite_local_access",
                "serving_satellite_id": serving_satellite_id,
                "local_delivery_model": "access_link_delivery_to_ut",
                "state_owner": "serving_satellite_edge",
                "state_model": "local_ut_delivery_not_backbone_reachability",
            },
            "shared_backbone_return_target": shared_return_target,
            "candidate_shared_zone_return_route": candidate_zone_route,
            "candidate_shared_return_label_stack": candidate_label_stack,
            "edge_local_delivery_target": {
                "target_kind": "serving_satellite_local_ut_delivery",
                "serving_satellite_id": serving_satellite_id,
                "ut_id": ut_id,
                "state_owner": "serving_satellite_edge",
                "state_model": "local_ut_delivery_not_backbone_reachability",
            },
            "state_ownership": {
                "backbone": {
                    "owner": "satellite_backbone",
                    "state_model": "shared_gateway_zone_or_zone_return_reachability",
                    "scale_dimension": "gateway_zone_or_zone_target",
                },
                "edge": {
                    "owner": "serving_satellite_edge",
                    "state_model": "serving_satellite_local_ut_delivery",
                    "scale_dimension": "local_ut_delivery",
                },
            },
            "scale_target": "backbone_return_state_independent_of_ut_count",
            "current_linux_realization": {
                "mode": "shared_gz_backbone",
                "reason": (
                    "linux_installer_uses_gateway_zone_return_route_and_shared_reverse_lfib; "
                    "per_ut_state_is_limited_to_gateway_edge_policy_and_serving_satellite_local_delivery"
                ),
            },
        }

    def _flow_intent(
        self,
        *,
        ut_id: str,
        flow_suffix: str,
        flow_variant_index: int,
    ) -> ExperimentFlowIntent:
        base_intent = _experiment_flow_intent(ut_id)
        if flow_suffix == "default":
            return base_intent
        variant_offset = flow_variant_index * 20
        return ExperimentFlowIntent(
            intent_id=f"{base_intent.intent_id}:{flow_suffix}",
            default_ping_target=base_intent.default_ping_target,
            forward_label_seed=base_intent.forward_label_seed + variant_offset,
            reverse_label_seed=base_intent.reverse_label_seed + variant_offset,
            policy_table_id=base_intent.policy_table_id + flow_variant_index,
            policy_rule_priority=base_intent.policy_rule_priority + flow_variant_index,
        )

    def _internet_validation_targets(
        self,
        topology_snapshot: TopologySnapshot,
    ) -> List[Dict[str, Any]]:
        return [
            dict(target)
            for target in topology_snapshot.planner_hints.get(
                "internet_validation_targets",
                [],
            )
        ]

    def _special_prefix_policies(
        self,
        topology_snapshot: TopologySnapshot,
    ) -> List[Dict[str, Any]]:
        return [
            dict(policy)
            for policy in topology_snapshot.planner_hints.get("special_prefix_overrides", [])
        ]

    def _zone_default_gateway_policy(
        self,
        topology_snapshot: TopologySnapshot,
        *,
        zone_id: str,
    ) -> Dict[str, Any]:
        zone_policies = dict(
            topology_snapshot.planner_hints.get("zone_default_gateway_preferences", {})
        )
        return dict(zone_policies.get(zone_id, {}))

    def _zone_resolution_context(
        self,
        topology_snapshot: TopologySnapshot,
        *,
        ut_id: str,
        zone_id: str,
    ) -> Dict[str, Any]:
        zone = dict(topology_snapshot.zones.get(zone_id, {}))
        attachment = dict(topology_snapshot.nodes.get(ut_id, {}).get("attachment", {}))
        resolution_state = str(zone.get("resolution_state", "resolved"))
        serving_satellite_id = str(zone.get("active_serving_satellite_id") or "")
        service_role = str(zone.get("service_role", "required_service"))
        active_service = bool(
            zone.get(
                "active_service",
                service_role in {"required_service", "optional_service"},
            )
        )
        service_planning_enabled = (
            active_service and service_role in {"required_service", "optional_service"}
        )
        is_resolved = (
            service_planning_enabled
            and resolution_state == "resolved"
            and bool(serving_satellite_id)
        )
        return {
            "zone": zone,
            "attachment": attachment,
            "resolution_state": resolution_state,
            "serving_satellite_id": serving_satellite_id,
            "service_role": service_role,
            "active_service": active_service,
            "service_planning_enabled": service_planning_enabled,
            "is_resolved": is_resolved,
        }

    def _resolve_gateway_path(
        self,
        *,
        topology_snapshot: TopologySnapshot,
        serving_satellite_id: str,
        preferred_gateway_id: Optional[str] = None,
        preferred_gateway_ids: Optional[Sequence[str]] = None,
        selection_mode: str = "shortest_path",
        fallback_to_shortest_path: bool = True,
        max_additional_hops_relative_to_best_allowed: Optional[int] = None,
    ) -> Optional[Tuple[str, List[str], Dict[str, Any]]]:
        adjacency = _satellite_adjacency(topology_snapshot)
        gateway_candidates = self._gateway_candidates(topology_snapshot)
        allowlisted_gateway_ids: List[str] = []
        if preferred_gateway_ids:
            for gateway_id in preferred_gateway_ids:
                normalized_gateway_id = str(gateway_id).strip()
                if normalized_gateway_id and normalized_gateway_id not in allowlisted_gateway_ids:
                    allowlisted_gateway_ids.append(normalized_gateway_id)
        if preferred_gateway_id is not None:
            normalized_gateway_id = str(preferred_gateway_id).strip()
            if normalized_gateway_id and normalized_gateway_id not in allowlisted_gateway_ids:
                allowlisted_gateway_ids.append(normalized_gateway_id)

        def _reachable_path_for_gateway(
            gateway_id: str,
        ) -> Optional[Tuple[str, List[str], Dict[str, Any]]]:
            for candidate_gateway_id, gateway_satellite_id, gateway_link in gateway_candidates:
                if candidate_gateway_id != gateway_id:
                    continue
                path = _shortest_satellite_path(
                    adjacency=adjacency,
                    start_node_id=serving_satellite_id,
                    goal_node_id=gateway_satellite_id,
                )
                if path is None:
                    return None
                return candidate_gateway_id, path, gateway_link
            return None

        all_candidate_paths: List[Tuple[int, str, List[str], Dict[str, Any]]] = []
        for gateway_id, gateway_satellite_id, gateway_link in gateway_candidates:
            path = _shortest_satellite_path(
                adjacency=adjacency,
                start_node_id=serving_satellite_id,
                goal_node_id=gateway_satellite_id,
            )
            if path is None:
                continue
            all_candidate_paths.append((len(path) - 1, gateway_id, path, gateway_link))

        if selection_mode == "stable_preferred_gateway_order" and allowlisted_gateway_ids:
            ordered_preferred_paths: List[Tuple[str, List[str], Dict[str, Any]]] = []
            for gateway_id in allowlisted_gateway_ids:
                preferred_path = _reachable_path_for_gateway(gateway_id)
                if preferred_path is not None:
                    ordered_preferred_paths.append(preferred_path)
            if ordered_preferred_paths:
                if max_additional_hops_relative_to_best_allowed is None:
                    return ordered_preferred_paths[0]
                best_allowed_hops = (
                    min(hops for hops, _, _, _ in all_candidate_paths)
                    if fallback_to_shortest_path and all_candidate_paths
                    else min(len(path) - 1 for _, path, _ in ordered_preferred_paths)
                )
                for candidate_gateway_id, candidate_path, candidate_gateway_link in ordered_preferred_paths:
                    if (len(candidate_path) - 1) <= (
                        best_allowed_hops + int(max_additional_hops_relative_to_best_allowed)
                    ):
                        return (
                            candidate_gateway_id,
                            candidate_path,
                            candidate_gateway_link,
                        )
            if not fallback_to_shortest_path:
                return None
            if all_candidate_paths:
                _, selected_gateway_id, satellite_path, gateway_link = min(
                    all_candidate_paths,
                    key=lambda item: (item[0], item[1], item[2]),
                )
                return selected_gateway_id, satellite_path, gateway_link

        candidate_paths: List[Tuple[int, str, List[str], Dict[str, Any]]] = []
        for gateway_id, gateway_satellite_id, gateway_link in gateway_candidates:
            if allowlisted_gateway_ids and gateway_id not in allowlisted_gateway_ids:
                continue
            matching_path = next(
                (
                    (hops, candidate_gateway_id, path, candidate_gateway_link)
                    for hops, candidate_gateway_id, path, candidate_gateway_link in all_candidate_paths
                    if candidate_gateway_id == gateway_id
                ),
                None,
            )
            if matching_path is not None:
                candidate_paths.append(matching_path)
        if not candidate_paths and allowlisted_gateway_ids and fallback_to_shortest_path:
            candidate_paths = list(all_candidate_paths)
        if not candidate_paths:
            return None
        _, selected_gateway_id, satellite_path, gateway_link = min(
            candidate_paths,
            key=lambda item: (item[0], item[1], item[2]),
        )
        return selected_gateway_id, satellite_path, gateway_link

    def _flow_context(
        self,
        *,
        topology_snapshot: TopologySnapshot,
        ut_id: str,
        zone_id: str,
        flow_suffix: str,
        flow_variant_index: int,
        validation_targets: List[Dict[str, Any]],
        preferred_gateway_id: Optional[str] = None,
        preferred_gateway_ids: Optional[Sequence[str]] = None,
        gateway_selection_mode: str = "shortest_path",
        max_additional_hops_relative_to_best_allowed: Optional[int] = None,
        fallback_to_shortest_path: bool = True,
        traffic_scope: str = "ordinary_internet_default",
        policy_name: Optional[str] = None,
        destination_prefix: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        attachment = dict(topology_snapshot.nodes[ut_id].get("attachment", {}))
        zone = dict(topology_snapshot.zones.get(zone_id, {}))
        if not bool(
            zone.get("zone_service_resolved", zone.get("resolution_state") == "resolved")
        ):
            raise PlannerError(f"zone service is not resolved for {zone_id}/{ut_id}")
        zone_anchor_satellite_id = str(zone.get("active_serving_satellite_id") or "")
        if not zone_anchor_satellite_id:
            raise PlannerError(f"missing zone anchor satellite for {zone_id}/{ut_id}")
        ut_access_satellite_id = str(attachment.get("active_serving_satellite_id") or "")
        if not ut_access_satellite_id:
            raise PlannerError(f"missing UT access satellite for {zone_id}/{ut_id}")
        if str(attachment.get("state")) != "attached":
            raise PlannerError(
                f"{ut_id} is not attached to a visible serving satellite for {zone_id}"
            )
        if ut_access_satellite_id != zone_anchor_satellite_id:
            raise PlannerError(
                f"{ut_id} attaches to {ut_access_satellite_id}, not zone anchor "
                f"{zone_anchor_satellite_id} for {zone_id}"
            )
        serving_satellite_id = zone_anchor_satellite_id
        intent = self._flow_intent(
            ut_id=ut_id,
            flow_suffix=flow_suffix,
            flow_variant_index=flow_variant_index,
        )
        gateway_path = self._resolve_gateway_path(
            topology_snapshot=topology_snapshot,
            serving_satellite_id=serving_satellite_id,
            preferred_gateway_id=preferred_gateway_id,
            preferred_gateway_ids=preferred_gateway_ids,
            selection_mode=gateway_selection_mode,
            fallback_to_shortest_path=fallback_to_shortest_path,
            max_additional_hops_relative_to_best_allowed=(
                max_additional_hops_relative_to_best_allowed
            ),
        )
        if gateway_path is None:
            return None
        selected_gateway_id, satellite_path, gateway_link = gateway_path
        forward_links: List[str] = []
        for first_node_id, second_node_id in zip(satellite_path, satellite_path[1:]):
            link = _link_between_nodes(topology_snapshot, first_node_id, second_node_id)
            if link is None:
                raise PlannerError(
                    f"missing link between {first_node_id} and {second_node_id}"
                )
            forward_links.append(str(link.get("link_id")))
        forward_links.append(str(gateway_link.get("link_id")))
        reverse_links = list(reversed(forward_links))
        label_count = len(forward_links)
        (
            forward_label_seed,
            reverse_label_seed,
            shared_backbone_label_scope_id,
        ) = self._shared_backbone_label_seeds(
            serving_satellite_id=serving_satellite_id,
            selected_gateway_id=selected_gateway_id,
            selected_gateway_satellite_id=str(satellite_path[-1]),
            forward_link_ids=forward_links,
            label_count=label_count,
        )
        forward_labels = [
            forward_label_seed + offset for offset in range(label_count)
        ]
        reverse_labels = [
            reverse_label_seed + offset for offset in range(label_count)
        ]
        primary_target = (
            dict(validation_targets[0])
            if validation_targets
            else {
                "ip_address": topology_snapshot.planner_hints.get(
                    "internet_ping_target_ip",
                    intent.default_ping_target,
                ),
                "node_id": topology_snapshot.planner_hints.get(
                    "target_host_node_id",
                    "as154-host-new",
                ),
            }
        )
        zone = dict(topology_snapshot.zones.get(zone_id, {}))
        zone_return_prefixes = [
            str(prefix)
            for prefix in list(zone.get("return_prefixes", []))
            if str(prefix)
        ]
        return {
            "flow_id": intent.intent_id,
            "ut_id": ut_id,
            "zone_id": zone_id,
            "zone_return_prefixes": list(zone_return_prefixes),
            "traffic_scope": traffic_scope,
            "policy_name": policy_name,
            "destination_prefix": destination_prefix,
            "gateway_selection_mode": gateway_selection_mode,
            "preferred_gateway_ids": [
                str(gateway_id)
                for gateway_id in (preferred_gateway_ids or [])
                if str(gateway_id).strip()
            ],
            "validation_targets": [dict(target) for target in validation_targets],
            "ping_target_ip": primary_target.get("ip_address"),
            "target_host_node_id": primary_target.get("node_id"),
            "zone_anchor_satellite_id": zone_anchor_satellite_id,
            "serving_satellite_id": serving_satellite_id,
            "ut_access_satellite_id": ut_access_satellite_id,
            "serving_satellite_role": "zone_anchor_responsible_satellite",
            "zone_anchor_matches_ut_attachment": (
                bool(zone_anchor_satellite_id)
                and zone_anchor_satellite_id == ut_access_satellite_id
            ),
            "selected_gateway_id": selected_gateway_id,
            "selected_gateway_satellite_id": satellite_path[-1],
            "satellite_path": satellite_path,
            "ut_access_link_id": attachment.get("slot_name"),
            "gateway_access_link_id": gateway_link.get("link_id"),
            "gateway_class_id": self._gateway_class_id(
                zone_id=zone_id,
                gateway_id=selected_gateway_id,
            ),
            "shared_backbone_label_scope_id": shared_backbone_label_scope_id,
            "return_path_intent": self._return_path_intent(
                ut_id=ut_id,
                zone_id=zone_id,
                zone_return_prefixes=zone_return_prefixes,
                serving_satellite_id=serving_satellite_id,
                selected_gateway_id=selected_gateway_id,
                selected_gateway_satellite_id=str(satellite_path[-1]),
                reverse_link_ids=reverse_links,
                reverse_labels=reverse_labels,
                shared_backbone_label_scope_id=shared_backbone_label_scope_id,
            ),
            "label_allocation_mode": "shared_backbone_path",
            "forward_link_ids": forward_links,
            "reverse_link_ids": reverse_links,
            "forward_labels": forward_labels,
            "reverse_labels": reverse_labels,
            "policy_table_id": intent.policy_table_id,
            "policy_rule_priority": intent.policy_rule_priority,
        }

    def _satellite_hop_count(
        self,
        flow_context: Dict[str, Any],
    ) -> int:
        return max(0, len(list(flow_context.get("satellite_path", []))) - 1)

    def _override_is_competitive(
        self,
        *,
        default_flow_context: Dict[str, Any],
        override_flow_context: Dict[str, Any],
        policy: Dict[str, Any],
    ) -> Tuple[bool, str]:
        if not bool(policy.get("require_competitive_path_vs_default", False)):
            return True, "competitive_check_disabled"
        default_hops = self._satellite_hop_count(default_flow_context)
        override_hops = self._satellite_hop_count(override_flow_context)
        max_additional_hops = int(
            policy.get("max_additional_satellite_hops_relative_to_default", 0)
        )
        if override_hops > default_hops + max_additional_hops:
            return False, "preferred_gateway_path_excessive_vs_default"
        return True, "competitive_vs_default"

    def _gateway_selection_for_ut(
        self,
        *,
        zone_id: str,
        default_flow_context: Dict[str, Any],
        override_flow_contexts: List[Dict[str, Any]],
        unresolved_override_policies: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        bindings_by_gateway_class: Dict[str, Dict[str, Any]] = {}

        def register_binding(
            *,
            flow_context: Dict[str, Any],
            traffic_scope: str,
        ) -> str:
            gateway_id = str(flow_context["selected_gateway_id"])
            gateway_class_id = self._gateway_class_id(zone_id=zone_id, gateway_id=gateway_id)
            if gateway_class_id in bindings_by_gateway_class:
                return gateway_class_id
            ingress_label = next(iter(flow_context.get("forward_labels", [])), None)
            bindings_by_gateway_class[gateway_class_id] = {
                "flow_id": flow_context["flow_id"],
                "gateway_class_id": gateway_class_id,
                "selected_gateway_id": gateway_id,
                "serving_satellite_id": flow_context["serving_satellite_id"],
                "ingress_transport": {
                    "encapsulation": "mpls_label_impose",
                    "ingress_label": ingress_label,
                },
                "traffic_scope": traffic_scope,
                "ordinary_traffic_behavior": (
                    "default_gateway_class"
                    if traffic_scope == "ordinary_internet_default"
                    else "special_prefix_override"
                ),
            }
            return gateway_class_id

        default_gateway_class_id = register_binding(
            flow_context=default_flow_context,
            traffic_scope="ordinary_internet_default",
        )
        special_prefix_overrides: List[Dict[str, Any]] = []
        for flow_context in override_flow_contexts:
            gateway_class_id = register_binding(
                flow_context=flow_context,
                traffic_scope="special_prefix_override",
            )
            special_prefix_overrides.append(
                {
                    "policy_name": flow_context.get("policy_name"),
                    "destination_prefix": flow_context.get("destination_prefix"),
                    "gateway_class_id": gateway_class_id,
                    "resolution_state": "resolved",
                }
            )
        special_prefix_overrides.extend(unresolved_override_policies)
        default_binding = dict(bindings_by_gateway_class[default_gateway_class_id])
        return {
            "flow_id": default_flow_context["flow_id"],
            "ut_id": default_flow_context["ut_id"],
            "zone_id": zone_id,
            "default_gateway_class_id": default_gateway_class_id,
            "special_prefix_overrides": special_prefix_overrides,
            "gateway_class_binding": default_binding,
            "gateway_class_bindings": [
                dict(binding)
                for _, binding in sorted(bindings_by_gateway_class.items())
            ],
        }

    def _core_flow_contexts(
        self,
        *,
        topology_snapshot: TopologySnapshot,
        ut_id: str,
        zone_id: str,
    ) -> Dict[str, Any]:
        validation_targets = self._internet_validation_targets(topology_snapshot)
        zone_default_gateway_policy = self._zone_default_gateway_policy(
            topology_snapshot,
            zone_id=zone_id,
        )
        default_preferred_gateway_ids = [
            str(gateway_id)
            for gateway_id in zone_default_gateway_policy.get(
                "preferred_gateway_ids",
                zone_default_gateway_policy.get("regional_preferred_gateway_ids", []),
            )
            if str(gateway_id).strip()
        ]
        if not default_preferred_gateway_ids:
            default_preferred_gateway_ids = [
                str(gateway_id)
                for gateway_id in zone_default_gateway_policy.get("gateway_ids", [])
                if str(gateway_id).strip()
            ]
        default_flow_context = self._flow_context(
            topology_snapshot=topology_snapshot,
            ut_id=ut_id,
            zone_id=zone_id,
            flow_suffix="default",
            flow_variant_index=0,
            validation_targets=validation_targets,
            preferred_gateway_ids=default_preferred_gateway_ids,
            gateway_selection_mode=str(
                zone_default_gateway_policy.get("selection_mode", "shortest_path")
            ),
            max_additional_hops_relative_to_best_allowed=zone_default_gateway_policy.get(
                "max_additional_satellite_hops_relative_to_best_candidate"
            ),
            fallback_to_shortest_path=bool(
                zone_default_gateway_policy.get("fallback_to_shortest_path", True)
            ),
            traffic_scope="ordinary_internet_default",
        )
        if default_flow_context is None:
            raise PlannerError(f"no default gateway path available for {ut_id}")

        override_flow_contexts: List[Dict[str, Any]] = []
        unresolved_override_policies: List[Dict[str, Any]] = []
        for variant_index, policy in enumerate(
            self._special_prefix_policies(topology_snapshot),
            start=1,
        ):
            policy_zone_id = str(policy.get("zone_id") or "")
            if policy_zone_id and policy_zone_id != str(zone_id):
                continue
            target = dict(policy.get("target", {}))
            flow_context = self._flow_context(
                topology_snapshot=topology_snapshot,
                ut_id=ut_id,
                zone_id=zone_id,
                flow_suffix=f"override:{policy.get('policy_name', variant_index)}",
                flow_variant_index=variant_index,
                validation_targets=[target] if target else [],
                preferred_gateway_id=policy.get("preferred_gateway_id"),
                preferred_gateway_ids=list(policy.get("preferred_gateway_ids", [])),
                gateway_selection_mode=str(
                    policy.get("selection_mode", "target_affinity_shortest_path")
                ),
                fallback_to_shortest_path=bool(
                    policy.get("fallback_to_shortest_path", True)
                ),
                traffic_scope="special_prefix_override",
                policy_name=policy.get("policy_name"),
                destination_prefix=policy.get("destination_prefix"),
            )
            if flow_context is None:
                unresolved_override_policies.append(
                    {
                        "policy_name": policy.get("policy_name"),
                        "destination_prefix": policy.get("destination_prefix"),
                        "gateway_class_id": None,
                        "resolution_state": "preferred_gateway_unreachable",
                    }
                )
                continue
            if (
                flow_context["selected_gateway_id"]
                == default_flow_context["selected_gateway_id"]
            ):
                unresolved_override_policies.append(
                    {
                        "policy_name": policy.get("policy_name"),
                        "destination_prefix": policy.get("destination_prefix"),
                        "gateway_class_id": self._gateway_class_id(
                            zone_id=zone_id,
                            gateway_id=str(flow_context["selected_gateway_id"]),
                        ),
                        "resolution_state": "resolved_same_as_default",
                    }
                )
                continue
            is_competitive, competitive_reason = self._override_is_competitive(
                default_flow_context=default_flow_context,
                override_flow_context=flow_context,
                policy=policy,
            )
            if not is_competitive:
                unresolved_override_policies.append(
                    {
                        "policy_name": policy.get("policy_name"),
                        "destination_prefix": policy.get("destination_prefix"),
                        "gateway_class_id": self._gateway_class_id(
                            zone_id=zone_id,
                            gateway_id=str(flow_context["selected_gateway_id"]),
                        ),
                        "resolution_state": competitive_reason,
                        "default_gateway_id": default_flow_context["selected_gateway_id"],
                        "default_satellite_hops": self._satellite_hop_count(
                            default_flow_context
                        ),
                        "override_satellite_hops": self._satellite_hop_count(flow_context),
                    }
                )
                continue
            override_flow_contexts.append(flow_context)
        gateway_selection = self._gateway_selection_for_ut(
            zone_id=zone_id,
            default_flow_context=default_flow_context,
            override_flow_contexts=override_flow_contexts,
            unresolved_override_policies=unresolved_override_policies,
        )
        return {
            "default_flow_context": default_flow_context,
            "override_flow_contexts": override_flow_contexts,
            "gateway_selection": gateway_selection,
        }

    def build_runtime_update_plan(
        self,
        topology_snapshot: TopologySnapshot,
    ) -> RuntimeUpdatePlan:
        self._reset_runtime_build_state()
        actions_by_node: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)

        for ut_id, zone_id in sorted(topology_snapshot.ut_zone_membership.items()):
            zone_resolution = self._zone_resolution_context(
                topology_snapshot,
                ut_id=ut_id,
                zone_id=zone_id,
            )
            if not bool(zone_resolution["is_resolved"]):
                resolution_state = str(zone_resolution["resolution_state"])
                if not bool(zone_resolution.get("service_planning_enabled", True)):
                    resolution_state = "zone_service_not_active"
                actions_by_node[ut_id].append(
                    self._node_action(
                        action_id=f"ut-classification:{ut_id}",
                        action_type="traffic_classification",
                        node_id=ut_id,
                        sequence=100,
                        intent={
                            "match": {
                                "traffic_source": {
                                    "entity_type": "user_terminal",
                                    "entity_id": ut_id,
                                },
                                "destination_scope": "internet_egress",
                            },
                            "outcome": {
                                "zone_id": zone_id,
                                "serving_satellite_id": None,
                                "traffic_role": "ut_internet_egress_unavailable",
                                "service_state": "temporarily_unavailable",
                                "resolution_state": resolution_state,
                            },
                            "classification_scope": "node_local_ingress",
                        },
                        expected_state=(
                            f"{ut_id} traffic remains blocked until {zone_id} resolves"
                        ),
                        metadata={
                            "ut_id": ut_id,
                            "zone_id": zone_id,
                            "zone_service_role": zone_resolution.get("service_role"),
                            "zone_active_service": zone_resolution.get("active_service"),
                            "planner_semantics": "ut_to_zone_partition_unresolved",
                            "resolution_state": resolution_state,
                        },
                    )
                )
                continue
            flow_contexts = self._core_flow_contexts(
                topology_snapshot=topology_snapshot,
                ut_id=ut_id,
                zone_id=zone_id,
            )
            default_flow_context = dict(flow_contexts["default_flow_context"])
            gateway_selection = dict(flow_contexts["gateway_selection"])
            all_flow_contexts = [
                default_flow_context,
                *[
                    dict(flow_context)
                    for flow_context in flow_contexts["override_flow_contexts"]
                ],
            ]
            serving_satellite_id = str(default_flow_context["serving_satellite_id"])
            gateway_id = str(default_flow_context["selected_gateway_id"])

            actions_by_node[ut_id].append(
                self._node_action(
                    action_id=f"ut-classification:{ut_id}",
                    action_type="traffic_classification",
                    node_id=ut_id,
                    sequence=100,
                    intent={
                        "match": {
                            "traffic_source": {
                                "entity_type": "user_terminal",
                                "entity_id": ut_id,
                            },
                            "destination_scope": "internet_egress",
                        },
                        "outcome": {
                            "zone_id": zone_id,
                            "serving_satellite_id": serving_satellite_id,
                            "default_gateway_class_id": gateway_selection[
                                "default_gateway_class_id"
                            ],
                            "special_prefix_overrides": list(
                                gateway_selection["special_prefix_overrides"]
                            ),
                            "traffic_role": "ut_internet_egress",
                        },
                        "classification_scope": "node_local_ingress",
                    },
                    expected_state=f"{ut_id} traffic maps to {gateway_id}",
                    metadata={
                        "ut_id": ut_id,
                        "zone_id": zone_id,
                        "planner_semantics": "ut_to_internet_flow_selection",
                    },
                )
            )
            actions_by_node[ut_id].append(
                self._node_action(
                    action_id=f"flow-forwarding:{ut_id}:ut-ingress",
                    action_type="forwarding_entry",
                    node_id=ut_id,
                    sequence=110,
                    intent={
                        "forwarding_plane": "ut_gateway_class_resolution",
                        "match": {
                            "gateway_class_id": gateway_selection["default_gateway_class_id"],
                            "node_role": "ut_ingress",
                        },
                        "outcome": {
                            "gateway_selection": dict(gateway_selection),
                            "local_role": "ut_ingress",
                        },
                    },
                    expected_state=f"{ut_id} forwards via {serving_satellite_id}",
                    metadata={
                        "flow_id": default_flow_context["flow_id"],
                        "gateway_class_id": gateway_selection["default_gateway_class_id"],
                        "ut_contract_scope": "lightweight_gateway_selection_only",
                    },
                )
            )

            actions_by_node[serving_satellite_id].append(
                self._node_action(
                    action_id=f"zone-service:{zone_id}",
                    action_type="service_binding",
                    node_id=serving_satellite_id,
                    sequence=100,
                    intent={
                        "binding_kind": "zone_service",
                        "subject": {
                            "entity_type": "zone",
                            "entity_id": zone_id,
                        },
                        "target": {
                            "entity_type": "satellite",
                            "entity_id": serving_satellite_id,
                            "role": "active_responsible_serving_satellite",
                        },
                        "binding_scope": "slice_local",
                        "return_delivery_semantics": {
                            "intent_kind": "zone_return_delivery",
                            "zone_id": zone_id,
                            "zone_serving_satellite_id": serving_satellite_id,
                            "serving_satellite_delivery_scope": "local_ut_delivery",
                            "current_linux_realization": "per_ut_flow_compatibility",
                            "scale_target": "shared_zone_reachability_before_local_ut_delivery",
                        },
                    },
                    expected_state=f"{zone_id} served by {serving_satellite_id}",
                    metadata={
                        "flow_id": default_flow_context["flow_id"],
                        "return_path_boundary": "zone_return_intent",
                    },
                )
            )
            for flow_context in all_flow_contexts:
                gateway_satellite_id = str(flow_context["selected_gateway_satellite_id"])
                selected_gateway_id = str(flow_context["selected_gateway_id"])
                flow_serving_satellite_id = str(flow_context["serving_satellite_id"])
                satellite_path = [
                    str(node_id) for node_id in flow_context.get("satellite_path", [])
                ]
                flow_sequence_suffix = str(flow_context["flow_id"]).replace(":", "-")
                actions_by_node[flow_serving_satellite_id].append(
                    self._node_action(
                        action_id=f"flow-forwarding:{ut_id}:{flow_sequence_suffix}:serving-satellite",
                        action_type="forwarding_entry",
                        node_id=flow_serving_satellite_id,
                        sequence=110 + len(actions_by_node[flow_serving_satellite_id]) * 10,
                        intent={
                            "forwarding_plane": "label_switched_internet_egress",
                            "match": {
                                "flow_id": flow_context["flow_id"],
                                "node_role": "serving_satellite",
                            },
                            "outcome": {
                                "flow_context": dict(flow_context),
                                "local_role": (
                                    "direct_gateway_egress"
                                    if flow_serving_satellite_id == gateway_satellite_id
                                    else "transit_toward_gateway"
                                ),
                            },
                        },
                        expected_state=(
                            f"{flow_serving_satellite_id} hosts {flow_context['flow_id']}"
                        ),
                        metadata={
                            "flow_id": flow_context["flow_id"],
                            "realization_family": "experiment_label_switched_egress",
                            "traffic_scope": flow_context.get("traffic_scope"),
                        },
                    )
                )
                for path_index, satellite_node_id in enumerate(satellite_path[1:], start=1):
                    actions_by_node[satellite_node_id].append(
                        self._node_action(
                            action_id=(
                                f"flow-forwarding:{ut_id}:{flow_sequence_suffix}:"
                                f"satellite-hop-{path_index}"
                            ),
                            action_type="forwarding_entry",
                            node_id=satellite_node_id,
                            sequence=(
                                110
                                + len(actions_by_node[satellite_node_id]) * 10
                            ),
                            intent={
                                "forwarding_plane": "label_switched_internet_egress",
                                "match": {
                                    "flow_id": flow_context["flow_id"],
                                    "node_role": "satellite_path_member",
                                    "path_index": path_index,
                                },
                                "outcome": {
                                    "flow_context": dict(flow_context),
                                    "local_role": (
                                        "gateway_satellite_handoff"
                                        if satellite_node_id == gateway_satellite_id
                                        else "transit_label_swap"
                                    ),
                                },
                            },
                            expected_state=(
                                f"{satellite_node_id} forwards {flow_context['flow_id']}"
                            ),
                            metadata={
                                "flow_id": flow_context["flow_id"],
                                "realization_family": "experiment_label_switched_egress",
                                "traffic_scope": flow_context.get("traffic_scope"),
                                "path_index": path_index,
                            },
                        )
                    )

                actions_by_node[selected_gateway_id].append(
                    self._node_action(
                        action_id=(
                            f"gateway-service:{selected_gateway_id}:"
                            f"{flow_context['flow_id']}"
                        ),
                        action_type="service_binding",
                        node_id=selected_gateway_id,
                        sequence=100 + len(actions_by_node[selected_gateway_id]) * 10,
                        intent={
                            "binding_kind": "gateway_egress",
                            "subject": {
                                "entity_type": "gateway",
                                "entity_id": selected_gateway_id,
                            },
                            "target": {
                                "entity_type": "satellite",
                                "entity_id": gateway_satellite_id,
                                "role": "attached_gateway_satellite",
                            },
                            "binding_scope": "slice_local",
                        },
                        expected_state=(
                            f"{selected_gateway_id} attached to {gateway_satellite_id}"
                        ),
                        metadata={
                            "flow_id": flow_context["flow_id"],
                            "traffic_scope": flow_context.get("traffic_scope"),
                        },
                    )
                )
                actions_by_node[selected_gateway_id].append(
                    self._node_action(
                        action_id=(
                            f"flow-forwarding:{ut_id}:{flow_sequence_suffix}:gateway-egress"
                        ),
                        action_type="forwarding_entry",
                        node_id=selected_gateway_id,
                        sequence=110 + len(actions_by_node[selected_gateway_id]) * 10,
                        intent={
                            "forwarding_plane": "gateway_internet_egress",
                            "match": {
                                "flow_id": flow_context["flow_id"],
                                "node_role": "gateway_egress",
                            },
                            "outcome": {
                                "flow_context": dict(flow_context),
                                "local_role": "gateway_nat_and_return_ingress",
                            },
                        },
                        expected_state=(
                            f"{selected_gateway_id} realizes {flow_context['flow_id']}"
                        ),
                        metadata={
                            "flow_id": flow_context["flow_id"],
                            "realization_family": "experiment_label_switched_egress",
                            "traffic_scope": flow_context.get("traffic_scope"),
                        },
                    )
                )

        node_subplans: Dict[str, Dict[str, Any]] = {}
        for node_id in sorted(actions_by_node):
            node_subplans[node_id] = {
                "target_node_id": node_id,
                "target_node_type": topology_snapshot.nodes[node_id].get("node_type"),
                "apply_mode": "ordered_local_actions",
                "actions": sorted(
                    actions_by_node[node_id],
                    key=lambda action: int(action.get("sequence", 0)),
                ),
                "validation_expectations": {
                    "post_apply_validation_expected": True,
                    "validation_scope": "experiment_flow_and_contract",
                },
            }

        plan_seed = {
            "snapshot_id": topology_snapshot.snapshot_id,
            "planner": self.metadata(),
            "node_ids": sorted(node_subplans),
            "flow_ids": sorted(
                {
                    str(action.get("metadata", {}).get("flow_id"))
                    for subplan in node_subplans.values()
                    for action in subplan.get("actions", [])
                    if action.get("metadata", {}).get("flow_id") is not None
                }
            ),
        }
        plan_id = f"rtplan-{_fingerprint(plan_seed)}"
        return RuntimeUpdatePlan(
            schema_version=RUNTIME_UPDATE_PLAN_SCHEMA_VERSION,
            plan_id=plan_id,
            target_snapshot={
                "snapshot_id": topology_snapshot.snapshot_id,
                "slice_id": topology_snapshot.time_slice.get("slice_id"),
            },
            planner={
                **self.metadata(),
                "source": "iteration2_satellite_experiment",
            },
            apply_model={
                "state_model": "full_state_intent",
                "action_model": RUNTIME_ACTION_MODEL_VERSION,
                "execution_projection": "node_local_subplans",
                "agent_policy_visibility": "none",
            },
            node_subplans=node_subplans,
            replacement_semantics={
                "replayable": True,
                "idempotency_scope": "snapshot_id_and_target_node",
                "rollback_on_failure": "best_effort_cleanup_required",
            },
            validation_expectations={
                "planner_side_validation": "required_before_publish",
                "agent_side_validation": "required_before_local_apply",
                "installer_side_validation": "required_after_local_apply",
            },
        )


def build_mock_planner_flow(
    planner: Optional[Planner] = None,
) -> Dict[str, Any]:
    planner = DefaultPlannerSkeleton() if planner is None else planner
    topology_snapshot = build_mock_topology_snapshot()
    runtime_update_plan = planner.plan(topology_snapshot)
    planner_validation = planner.validate_flow(topology_snapshot, runtime_update_plan)
    return {
        "planner": planner.metadata(),
        "topology_snapshot": topology_snapshot.to_dict(),
        "runtime_update_plan": runtime_update_plan.to_dict(),
        "planner_validation": planner_validation.to_dict(),
        "validation_summary": {
            "all_valid": planner_validation.is_valid,
            "planner_flow": planner_validation.to_dict(),
            "contracts": [
                topology_snapshot.validation_result().to_dict(),
                runtime_update_plan.validation_result().to_dict(),
            ],
        },
    }
