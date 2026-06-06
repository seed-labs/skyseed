#!/usr/bin/env python3
# encoding: utf-8

"""Legacy-backed runtime bridge for the first Iteration 2 satellite experiment."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from _path_setup import bootstrap_legacy_import_path

SATELLITE_DIR = bootstrap_legacy_import_path()

from controller import SatelliteController
from contracts import RuntimeUpdatePlan, TopologySnapshot
from mpls_control import (
    ActiveLink,
    ActiveLinkEndpoint,
    UtEgressMplsPlan,
    get_ut_egress_intent,
    resolve_ut_egress_ping_target,
    topology_internet_egress_exclusion_prefixes,
    topology_ut_egress_guardrail_prefixes,
    ut_access_probe_command,
    ut_egress_cleanup_specs,
    ut_egress_install_specs,
    ut_egress_ping_command,
    ut_egress_policy_preparation_plan,
    ut_egress_policy_prepare_specs,
    ut_egress_route_show_specs,
)


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _controller_args(
    *,
    topology_path: Path,
    link_state_path: Path,
) -> argparse.Namespace:
    return argparse.Namespace(
        topology_config=str(topology_path),
        link_state=str(link_state_path),
        dry_run=False,
        timing=False,
        fast_runtime_apply=False,
    )


def _active_link_from_snapshot(
    topology_snapshot: TopologySnapshot,
    link_id: str,
) -> ActiveLink:
    link = next(
        entry
        for entry in topology_snapshot.active_links
        if str(entry.get("link_id")) == link_id
    )
    endpoints = tuple(
        ActiveLinkEndpoint(
            node_id=str(endpoint.get("node_id")),
            interface_name=str(endpoint.get("interface_name")),
            ip_address=str(endpoint.get("ip_address")),
            logical_direction=None
            if endpoint.get("logical_direction") is None
            else str(endpoint.get("logical_direction")),
        )
        for endpoint in link.get("endpoints", [])
    )
    if len(endpoints) != 2:
        raise RuntimeError(f"expected exactly two endpoints for {link_id}")
    return ActiveLink(
        slot_name=str(link.get("link_id")),
        slot_type=str(link.get("slot_type")),
        subnet=str(link.get("subnet")),
        binding={str(key): str(value) for key, value in dict(link.get("binding", {})).items()},
        endpoints=(endpoints[0], endpoints[1]),
    )


def _occupied_dynamic_slots(link_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sorted(
        [
            dict(slot)
            for slot in link_state.get("slots", [])
            if bool(slot.get("occupied", False))
            and str(slot.get("slot_type")) in {"ut_access", "gw_access", "cross_plane_isl"}
        ],
        key=lambda entry: str(entry.get("slot_name")),
    )


def _runtime_matches_desired_state(
    *,
    current_state: Dict[str, Any],
    desired_state: Dict[str, Any],
) -> bool:
    return _occupied_dynamic_slots(current_state) == _occupied_dynamic_slots(desired_state)


def _restore_runtime_bindings(
    controller: SatelliteController,
    desired_slots: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    applied: List[Dict[str, Any]] = []
    for slot in sorted(desired_slots, key=lambda entry: str(entry.get("slot_name"))):
        slot_name = str(slot.get("slot_name"))
        slot_type = str(slot.get("slot_type"))
        binding = dict(slot.get("binding") or {})
        if slot_type == "ut_access":
            controller.attach_ut(
                f"{binding['ut_id']}:{binding['satellite_id']}",
                requested_slot_name=slot_name,
            )
        elif slot_type == "gw_access":
            controller.attach_gateway(
                f"{binding['gateway_id']}:{binding['satellite_id']}",
                requested_slot_name=slot_name,
            )
        elif slot_type == "cross_plane_isl":
            controller.connect_cross_plane(
                (
                    f"{binding['satellite_a_id']}:{binding['satellite_b_id']}:"
                    f"{binding['satellite_a_direction']}:{binding['satellite_b_direction']}"
                ),
                requested_slot_name=slot_name,
            )
        else:
            continue
        applied.append(
            {
                "slot_name": slot_name,
                "slot_type": slot_type,
                "binding": binding,
            }
        )
    return applied


def _flow_context_from_plan(runtime_update_plan: RuntimeUpdatePlan) -> Dict[str, Any]:
    for subplan in runtime_update_plan.node_subplans.values():
        for action in subplan.get("actions", []):
            if str(action.get("action_type")) != "forwarding_entry":
                continue
            match = dict(action.get("intent", {}).get("match", {}))
            if str(match.get("node_role")) != "ut_ingress":
                continue
            outcome = dict(action.get("intent", {}).get("outcome", {}))
            flow_context = dict(outcome.get("flow_context", {}))
            if flow_context:
                return flow_context
    raise RuntimeError("unable to locate experiment flow context in runtime plan")


def _legacy_ut_egress_plan(
    *,
    topology: Dict[str, Any],
    topology_snapshot: TopologySnapshot,
    flow_context: Dict[str, Any],
) -> UtEgressMplsPlan:
    ut_id = str(flow_context["ut_id"])
    ut_intent = get_ut_egress_intent(ut_id)
    ut_slot = _active_link_from_snapshot(
        topology_snapshot,
        str(flow_context["ut_access_link_id"]),
    )
    gateway_slot = _active_link_from_snapshot(
        topology_snapshot,
        str(flow_context["gateway_access_link_id"]),
    )
    forward_links = tuple(
        _active_link_from_snapshot(topology_snapshot, str(link_id))
        for link_id in flow_context.get("forward_link_ids", [])
    )
    reverse_links = tuple(
        _active_link_from_snapshot(topology_snapshot, str(link_id))
        for link_id in flow_context.get("reverse_link_ids", [])
    )
    return UtEgressMplsPlan(
        intent=ut_intent,
        ut_slot=ut_slot,
        gateway_slot=gateway_slot,
        satellite_path=tuple(str(node_id) for node_id in flow_context.get("satellite_path", [])),
        forward_links=forward_links,
        reverse_links=reverse_links,
        forward_labels=tuple(int(value) for value in flow_context.get("forward_labels", [])),
        reverse_labels=tuple(int(value) for value in flow_context.get("reverse_labels", [])),
        local_exclusion_prefixes=topology_internet_egress_exclusion_prefixes(topology),
        policy_guardrail_prefixes=topology_ut_egress_guardrail_prefixes(topology),
        policy_table_id=int(flow_context["policy_table_id"]),
        policy_rule_priority=int(flow_context["policy_rule_priority"]),
    )


@dataclass(frozen=True)
class RuntimeBridgeExecutionResult:
    bridge_name: str
    execution_mode: str
    restored_runtime_bindings: List[Dict[str, Any]]
    preparation_result: Dict[str, Any]
    install_result: Dict[str, Any]
    verify_result: Dict[str, Any]
    ping_result: Dict[str, Any]
    cleanup_result: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bridge_name": self.bridge_name,
            "execution_mode": self.execution_mode,
            "restored_runtime_bindings": list(self.restored_runtime_bindings),
            "preparation_result": dict(self.preparation_result),
            "install_result": dict(self.install_result),
            "verify_result": dict(self.verify_result),
            "ping_result": dict(self.ping_result),
            "cleanup_result": dict(self.cleanup_result),
        }


class LegacyUtEgressRuntimeBridge:
    """Minimal bridge from Iteration 2 plans to the current legacy runtime."""

    bridge_name = "legacy_ut_egress_runtime_bridge"

    def __init__(
        self,
        *,
        topology_path: Path,
        link_state_path: Path,
    ) -> None:
        self.topology_path = topology_path
        self.link_state_path = link_state_path

    def ensure_runtime_topology(self) -> List[Dict[str, Any]]:
        desired_state = _load_json(self.link_state_path)
        desired_slots = _occupied_dynamic_slots(desired_state)
        controller = SatelliteController(
            _controller_args(
                topology_path=self.topology_path,
                link_state_path=self.link_state_path,
            )
        )
        if bool(controller.link_state.get("runtime_initialized", False)) and _runtime_matches_desired_state(
            current_state=controller.link_state,
            desired_state=desired_state,
        ):
            return [
                {
                    "slot_name": str(slot.get("slot_name")),
                    "slot_type": str(slot.get("slot_type")),
                    "binding": dict(slot.get("binding") or {}),
                }
                for slot in desired_slots
            ]
        controller.initialize_slot_runtime()
        return _restore_runtime_bindings(controller, desired_slots)

    def execute(
        self,
        *,
        topology_snapshot: TopologySnapshot,
        runtime_update_plan: RuntimeUpdatePlan,
    ) -> RuntimeBridgeExecutionResult:
        restored_runtime_bindings = self.ensure_runtime_topology()
        controller = SatelliteController(
            _controller_args(
                topology_path=self.topology_path,
                link_state_path=self.link_state_path,
            )
        )
        topology = dict(controller.topology)
        flow_context = _flow_context_from_plan(runtime_update_plan)
        legacy_plan = _legacy_ut_egress_plan(
            topology=topology,
            topology_snapshot=topology_snapshot,
            flow_context=flow_context,
        )
        controller._require_ut_egress_ready(legacy_plan)
        preparation_plan = ut_egress_policy_preparation_plan(topology, legacy_plan.ut_id)
        preparation_result = controller._execute_ut_egress_specs(
            action_name=f"iteration2-prepare-ut-egress-policy:{legacy_plan.ut_id}",
            specs=ut_egress_policy_prepare_specs(preparation_plan),
        )
        controller._execute_ut_egress_specs(
            action_name=f"iteration2-cleanup-ut-egress-mpls:{legacy_plan.profile_id}",
            specs=ut_egress_cleanup_specs(legacy_plan),
        )
        install_result = controller._execute_ut_egress_specs(
            action_name=f"iteration2-install-ut-egress-mpls:{legacy_plan.profile_id}",
            specs=ut_egress_install_specs(legacy_plan),
        )
        verify_result = controller._execute_ut_egress_specs(
            action_name=f"iteration2-verify-ut-egress-mpls:{legacy_plan.profile_id}",
            specs=ut_egress_route_show_specs(legacy_plan),
        )
        ping_target = resolve_ut_egress_ping_target(
            legacy_plan,
            str(flow_context.get("ping_target_ip")),
        )
        ping_result = {
            "ut_id": legacy_plan.ut_id,
            "ping_target_ip": ping_target,
            "target_host_node_id": flow_context.get("target_host_node_id"),
            "probe_returncode": None,
            "ping_returncode": None,
            "probe_stdout": "",
            "ping_stdout": "",
            "success": False,
        }
        try:
            probe = controller._docker_exec(
                legacy_plan.ut_id,
                ut_access_probe_command(legacy_plan),
                failure_stage=f"probe access link from {legacy_plan.ut_id}",
            )
            ping = controller._docker_exec(
                legacy_plan.ut_id,
                ut_egress_ping_command(legacy_plan, ping_target),
                failure_stage=f"ping {ping_target} from {legacy_plan.ut_id}",
            )
            ping_result = {
                "ut_id": legacy_plan.ut_id,
                "ping_target_ip": ping_target,
                "target_host_node_id": flow_context.get("target_host_node_id"),
                "probe_returncode": probe.returncode,
                "ping_returncode": ping.returncode,
                "probe_stdout": probe.stdout.strip() if probe.stdout else "",
                "ping_stdout": ping.stdout.strip() if ping.stdout else "",
                "success": probe.returncode == 0 and ping.returncode == 0,
            }
        finally:
            cleanup_result = controller._execute_ut_egress_specs(
                action_name=f"iteration2-cleanup-ut-egress-mpls:{legacy_plan.profile_id}",
                specs=ut_egress_cleanup_specs(legacy_plan),
            )
        if not ping_result["success"]:
            raise RuntimeError(
                f"ping failed for {legacy_plan.ut_id} -> {ping_target}"
            )
        return RuntimeBridgeExecutionResult(
            bridge_name=self.bridge_name,
            execution_mode="real_minimal_runtime_mutation",
            restored_runtime_bindings=restored_runtime_bindings,
            preparation_result=preparation_result,
            install_result=install_result,
            verify_result=verify_result,
            ping_result=ping_result,
            cleanup_result=cleanup_result,
        )


def docker_compose_up(
    *,
    compose_dir: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker-compose", "up", "-d"],
        cwd=str(compose_dir),
        capture_output=True,
        text=True,
        check=False,
    )
