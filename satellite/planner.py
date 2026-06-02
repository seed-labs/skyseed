#!/usr/bin/env python3
# encoding: utf-8

"""Planner abstractions and the default Iteration 2 skeleton planner."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, DefaultDict, Deque, Dict, List, Optional, Tuple

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
        forward_label_seed=16000 + ut_index * 100,
        reverse_label_seed=17000 + ut_index * 100,
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
            serving_satellite_id = str(zone.get("active_serving_satellite_id"))
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
        if slot_type not in {"fixed_isl", "cross_plane_isl"}:
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

    def _flow_context(
        self,
        *,
        topology_snapshot: TopologySnapshot,
        ut_id: str,
        zone_id: str,
    ) -> Dict[str, Any]:
        attachment = dict(topology_snapshot.nodes[ut_id].get("attachment", {}))
        serving_satellite_id = str(attachment.get("active_serving_satellite_id"))
        intent = _experiment_flow_intent(ut_id)
        adjacency = _satellite_adjacency(topology_snapshot)
        candidate_paths: List[Tuple[int, str, List[str], Dict[str, Any]]] = []
        for gateway_id, gateway_satellite_id, gateway_link in self._gateway_candidates(
            topology_snapshot
        ):
            path = _shortest_satellite_path(
                adjacency=adjacency,
                start_node_id=serving_satellite_id,
                goal_node_id=gateway_satellite_id,
            )
            if path is None:
                continue
            candidate_paths.append((len(path) - 1, gateway_id, path, gateway_link))
        if not candidate_paths:
            raise PlannerError(f"no gateway path available for {ut_id}")
        _, selected_gateway_id, satellite_path, gateway_link = min(
            candidate_paths,
            key=lambda item: (item[0], item[1], item[2]),
        )
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
        return {
            "flow_id": intent.intent_id,
            "ut_id": ut_id,
            "zone_id": zone_id,
            "ping_target_ip": topology_snapshot.planner_hints.get(
                "internet_ping_target_ip",
                intent.default_ping_target,
            ),
            "target_host_node_id": topology_snapshot.planner_hints.get(
                "target_host_node_id",
                "as154-host-new",
            ),
            "serving_satellite_id": serving_satellite_id,
            "selected_gateway_id": selected_gateway_id,
            "selected_gateway_satellite_id": satellite_path[-1],
            "satellite_path": satellite_path,
            "ut_access_link_id": attachment.get("slot_name"),
            "gateway_access_link_id": gateway_link.get("link_id"),
            "forward_link_ids": forward_links,
            "reverse_link_ids": reverse_links,
            "forward_labels": [
                intent.forward_label_seed + offset for offset in range(label_count)
            ],
            "reverse_labels": [
                intent.reverse_label_seed + offset for offset in range(label_count)
            ],
            "policy_table_id": intent.policy_table_id,
            "policy_rule_priority": intent.policy_rule_priority,
        }

    def build_runtime_update_plan(
        self,
        topology_snapshot: TopologySnapshot,
    ) -> RuntimeUpdatePlan:
        actions_by_node: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)

        for ut_id, zone_id in sorted(topology_snapshot.ut_zone_membership.items()):
            flow_context = self._flow_context(
                topology_snapshot=topology_snapshot,
                ut_id=ut_id,
                zone_id=zone_id,
            )
            serving_satellite_id = str(flow_context["serving_satellite_id"])
            gateway_id = str(flow_context["selected_gateway_id"])
            gateway_satellite_id = str(flow_context["selected_gateway_satellite_id"])

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
                            "selected_gateway_id": gateway_id,
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
                        "forwarding_plane": "internet_egress_path",
                        "match": {
                            "flow_id": flow_context["flow_id"],
                            "node_role": "ut_ingress",
                        },
                        "outcome": {
                            "flow_context": dict(flow_context),
                            "local_role": "ut_ingress",
                        },
                    },
                    expected_state=f"{ut_id} forwards via {serving_satellite_id}",
                    metadata={
                        "flow_id": flow_context["flow_id"],
                        "realization_family": "experiment_label_switched_egress",
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
                    },
                    expected_state=f"{zone_id} served by {serving_satellite_id}",
                    metadata={
                        "flow_id": flow_context["flow_id"],
                    },
                )
            )
            actions_by_node[serving_satellite_id].append(
                self._node_action(
                    action_id=f"flow-forwarding:{ut_id}:serving-satellite",
                    action_type="forwarding_entry",
                    node_id=serving_satellite_id,
                    sequence=110,
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
                                if serving_satellite_id == gateway_satellite_id
                                else "transit_toward_gateway"
                            ),
                        },
                    },
                    expected_state=f"{serving_satellite_id} hosts {flow_context['flow_id']}",
                    metadata={
                        "flow_id": flow_context["flow_id"],
                        "realization_family": "experiment_label_switched_egress",
                    },
                )
            )

            actions_by_node[gateway_id].append(
                self._node_action(
                    action_id=f"gateway-service:{gateway_id}",
                    action_type="service_binding",
                    node_id=gateway_id,
                    sequence=100,
                    intent={
                        "binding_kind": "gateway_egress",
                        "subject": {
                            "entity_type": "gateway",
                            "entity_id": gateway_id,
                        },
                        "target": {
                            "entity_type": "satellite",
                            "entity_id": gateway_satellite_id,
                            "role": "attached_gateway_satellite",
                        },
                        "binding_scope": "slice_local",
                    },
                    expected_state=f"{gateway_id} attached to {gateway_satellite_id}",
                    metadata={"flow_id": flow_context["flow_id"]},
                )
            )
            actions_by_node[gateway_id].append(
                self._node_action(
                    action_id=f"flow-forwarding:{ut_id}:gateway-egress",
                    action_type="forwarding_entry",
                    node_id=gateway_id,
                    sequence=110,
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
                    expected_state=f"{gateway_id} realizes {flow_context['flow_id']}",
                    metadata={
                        "flow_id": flow_context["flow_id"],
                        "realization_family": "experiment_label_switched_egress",
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
