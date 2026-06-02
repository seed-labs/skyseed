#!/usr/bin/env python3
# encoding: utf-8

"""Planner abstractions and the default Iteration 2 skeleton planner."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Tuple

from contracts import (
    RUNTIME_UPDATE_PLAN_SCHEMA_VERSION,
    RuntimeUpdatePlan,
    TopologySnapshot,
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
        node_id: str,
        sequence: int,
        logical_kind: str,
        expected_state: str,
        argv: Iterable[str],
        shared_key: str,
        details: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "sequence": sequence,
            "target_node_id": node_id,
            "logical_kind": logical_kind,
            "category": "planner_skeleton",
            "command_family": "planner_placeholder",
            "argv": list(argv),
            "shared_key": shared_key,
            "allow_failure": False,
            "expected_state": expected_state,
            "details": details,
        }

    def build_runtime_update_plan(
        self,
        topology_snapshot: TopologySnapshot,
    ) -> RuntimeUpdatePlan:
        actions_by_node: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)

        for zone_id, zone in sorted(topology_snapshot.zones.items()):
            serving_satellite_id = str(zone.get("active_serving_satellite_id"))
            actions_by_node[serving_satellite_id].append(
                self._node_action(
                    node_id=serving_satellite_id,
                    sequence=100 + len(actions_by_node[serving_satellite_id]) * 10,
                    logical_kind="zone_serving_placeholder",
                    expected_state=f"zone {zone_id} resolves to {serving_satellite_id}",
                    argv=(
                        "planner-placeholder",
                        "zone-serving",
                        zone_id,
                        serving_satellite_id,
                    ),
                    shared_key=f"zone-serving:{zone_id}",
                    details={
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
                    node_id=ut_id,
                    sequence=100 + len(actions_by_node[ut_id]) * 10,
                    logical_kind="ut_ingress_placeholder",
                    expected_state=f"{ut_id} remains mapped to {zone_id}",
                    argv=("planner-placeholder", "ut-ingress", ut_id, zone_id),
                    shared_key=f"ut-zone-membership:{ut_id}",
                    details={
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
                    node_id=gateway_id,
                    sequence=100 + len(actions_by_node[gateway_id]) * 10,
                    logical_kind="gateway_egress_placeholder",
                    expected_state=f"{gateway_id} observed on {satellite_id}",
                    argv=(
                        "planner-placeholder",
                        "gateway-egress",
                        gateway_id,
                        satellite_id,
                    ),
                    shared_key=f"gateway-egress:{gateway_id}",
                    details={
                        "gateway_id": gateway_id,
                        "attached_satellite_id": satellite_id,
                        "link_id": link.get("link_id"),
                        "planner_semantics": "placeholder_for_future_gateway_egress_policy",
                    },
                )
            )
            actions_by_node[satellite_id].append(
                self._node_action(
                    node_id=satellite_id,
                    sequence=100 + len(actions_by_node[satellite_id]) * 10,
                    logical_kind="gateway_attachment_placeholder",
                    expected_state=f"{satellite_id} exposes {gateway_id} egress",
                    argv=(
                        "planner-placeholder",
                        "gateway-attachment",
                        satellite_id,
                        gateway_id,
                    ),
                    shared_key=f"gateway-attachment:{satellite_id}:{gateway_id}",
                    details={
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
