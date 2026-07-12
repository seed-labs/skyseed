#!/usr/bin/env python3
# encoding: utf-8

from copy import deepcopy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from contracts import TOPOLOGY_SNAPSHOT_SCHEMA_VERSION, TopologySnapshot
from linux_mpls_runtime import _realization_contexts, _zone_return_install_intents
from planner import UtInternetExperimentPlanner
from temporal_planning import _runtime_plan_dict, diff_gz_fec_plans, gz_fec_plan


def _snapshot() -> TopologySnapshot:
    return TopologySnapshot(
        schema_version=TOPOLOGY_SNAPSHOT_SCHEMA_VERSION,
        snapshot_id="topsnap-return-intent-test",
        time_slice={
            "slice_id": "return-intent-slice-0001",
            "state_model": "full_state",
        },
        provenance={"source": "test_planner_return_path_intent"},
        nodes={
            "stl-950001": {
                "node_id": "stl-950001",
                "node_type": "satellite",
            },
            "gw1": {
                "node_id": "gw1",
                "node_type": "gateway",
            },
            "gw2": {
                "node_id": "gw2",
                "node_type": "gateway",
            },
            "ut101": {
                "node_id": "ut101",
                "node_type": "user_terminal",
                "attachment": {
                    "state": "attached",
                    "slot_name": "ut101-access-slot0",
                    "active_serving_satellite_id": "stl-950001",
                },
            },
        },
        active_links=[
            {
                "link_id": "gw1-gateway-slot0",
                "slot_type": "gw_access",
                "occupied": True,
                "binding": {
                    "gateway_id": "gw1",
                    "satellite_id": "stl-950001",
                },
                "endpoints": [
                    {
                        "node_id": "gw1",
                        "node_type": "gateway",
                        "interface_name": "ga-g1s0",
                        "ip_address": "10.0.0.1/30",
                    },
                    {
                        "node_id": "stl-950001",
                        "node_type": "satellite",
                        "interface_name": "ga-g1s0",
                        "ip_address": "10.0.0.2/30",
                    },
                ],
            },
            {
                "link_id": "gw2-gateway-slot0",
                "slot_type": "gw_access",
                "occupied": True,
                "binding": {
                    "gateway_id": "gw2",
                    "satellite_id": "stl-950001",
                },
                "endpoints": [
                    {
                        "node_id": "gw2",
                        "node_type": "gateway",
                        "interface_name": "ga-g2s0",
                        "ip_address": "10.0.0.5/30",
                    },
                    {
                        "node_id": "stl-950001",
                        "node_type": "satellite",
                        "interface_name": "ga-g2s0",
                        "ip_address": "10.0.0.6/30",
                    },
                ],
            },
            {
                "link_id": "ut101-access-slot0",
                "slot_type": "ut_access",
                "occupied": True,
                "binding": {
                    "ut_id": "ut101",
                    "satellite_id": "stl-950001",
                },
                "endpoints": [
                    {
                        "node_id": "ut101",
                        "node_type": "user_terminal",
                        "interface_name": "ua-u101s0",
                        "ip_address": "10.10.0.101/30",
                    },
                    {
                        "node_id": "stl-950001",
                        "node_type": "satellite",
                        "interface_name": "ua-u101s0",
                        "ip_address": "10.10.0.1/30",
                    },
                ],
            },
        ],
        zones={
            "zone-a": {
                "zone_id": "zone-a",
                "membership_mode": "explicit_test_zone",
                "ut_ids": ["ut101"],
                "return_prefixes": ["10.10.0.101/32"],
                "active_serving_satellite_id": "stl-950001",
                "candidate_serving_satellite_ids": ["stl-950001"],
            }
        },
        ut_zone_membership={"ut101": "zone-a"},
        planner_hints={
            "gateway_ids": ["gw1", "gw2"],
            "satellite_ids": ["stl-950001"],
            "gateway_service_catalog": {
                "gw1": {
                    "gateway_id": "gw1",
                    "gateway_selector_label": 16,
                    "gateway_service_ipv4": "198.18.1.1/32",
                },
                "gw2": {
                    "gateway_id": "gw2",
                    "gateway_selector_label": 17,
                    "gateway_service_ipv4": "198.18.2.1/32",
                },
            },
            "ut_gateway_policies": {
                "ut101": {
                    "ut_id": "ut101",
                    "zone_id": "zone-a",
                    "selection_model": "longest_prefix_match",
                    "immutable_for_run": True,
                    "rules": [
                        {
                            "prefix": "0.0.0.0/0",
                            "gateway_id": "gw1",
                            "gateway_selector_label": 16,
                        },
                        {
                            "prefix": "198.18.2.1/32",
                            "gateway_id": "gw2",
                            "gateway_selector_label": 17,
                        },
                    ],
                }
            },
        },
    )


def _snapshot_with_two_uts_same_zone() -> TopologySnapshot:
    base = _snapshot()
    nodes = deepcopy(base.nodes)
    active_links = deepcopy(base.active_links)
    zones = deepcopy(base.zones)
    nodes["ut102"] = {
        "node_id": "ut102",
        "node_type": "user_terminal",
        "attachment": {
            "state": "attached",
            "slot_name": "ut102-access-slot0",
            "active_serving_satellite_id": "stl-950001",
        },
    }
    active_links.append(
        {
            "link_id": "ut102-access-slot0",
            "slot_type": "ut_access",
            "occupied": True,
            "binding": {
                "ut_id": "ut102",
                "satellite_id": "stl-950001",
            },
            "endpoints": [
                {
                    "node_id": "ut102",
                    "node_type": "user_terminal",
                    "interface_name": "ua-u102s0",
                    "ip_address": "10.10.0.102/30",
                },
                {
                    "node_id": "stl-950001",
                    "node_type": "satellite",
                    "interface_name": "ua-u102s0",
                    "ip_address": "10.10.0.1/30",
                },
            ],
        }
    )
    zones["zone-a"]["ut_ids"] = ["ut101", "ut102"]
    zones["zone-a"]["return_prefixes"] = ["10.10.0.101/32", "10.10.0.102/32"]
    return TopologySnapshot(
        schema_version=base.schema_version,
        snapshot_id="topsnap-return-intent-two-uts-test",
        time_slice=dict(base.time_slice),
        provenance=dict(base.provenance),
        nodes=nodes,
        active_links=active_links,
        zones=zones,
        ut_zone_membership={"ut101": "zone-a", "ut102": "zone-a"},
        planner_hints={
            **deepcopy(base.planner_hints),
            "ut_gateway_policies": {
                **deepcopy(base.planner_hints["ut_gateway_policies"]),
                "ut102": {
                    **deepcopy(base.planner_hints["ut_gateway_policies"]["ut101"]),
                    "ut_id": "ut102",
                },
            },
        },
    )


def _flow_contexts(plan: Any) -> Dict[str, Dict[str, Any]]:
    contexts: Dict[str, Dict[str, Any]] = {}
    for subplan in plan.node_subplans.values():
        for action in subplan.get("actions", []):
            outcome = dict(action.get("intent", {}).get("outcome", {}))
            flow_context = dict(outcome.get("flow_context", {}))
            if flow_context:
                contexts[str(flow_context["flow_id"])] = flow_context
    return contexts


def _actions_for_node(plan: Any, node_id: str) -> List[Dict[str, Any]]:
    return list(plan.node_subplans[node_id].get("actions", []))


class PlannerReturnPathIntentTests(unittest.TestCase):
    def test_flow_context_carries_shared_zone_return_and_edge_delivery_intent(self) -> None:
        plan = UtInternetExperimentPlanner().plan(_snapshot())

        contexts = _flow_contexts(plan)
        context = contexts["ut-internet-egress:ut101"]
        return_intent = dict(context.get("return_path_intent", {}))

        self.assertEqual(return_intent["intent_kind"], "zone_return_delivery")
        self.assertEqual(return_intent["zone_id"], "zone-a")
        self.assertEqual(return_intent["ut_id"], "ut101")
        self.assertEqual(
            return_intent["backbone_forwarding_target"]["resolution"],
            "active_responsible_serving_satellite",
        )
        self.assertEqual(
            return_intent["backbone_forwarding_target"]["target_kind"],
            "zone_responsible_satellite",
        )
        self.assertEqual(
            return_intent["backbone_forwarding_target"]["resolved_node_id"],
            "stl-950001",
        )
        self.assertEqual(
            return_intent["shared_backbone_return_target"],
            {
                "target_kind": "gateway_zone_return",
                "target_key": "gw1:zone-a:stl-950001",
                "gateway_id": "gw1",
                "zone_id": "zone-a",
                "zone_serving_satellite_id": "stl-950001",
                "state_owner": "satellite_backbone",
                "state_model": "shared_gateway_zone_or_zone_return_reachability",
            },
        )
        candidate_route = dict(
            return_intent.get("candidate_shared_zone_return_route", {})
        )
        self.assertEqual(
            candidate_route["target_kind"],
            "gateway_zone_return_route_candidate",
        )
        self.assertEqual(
            candidate_route["shared_backbone_return_target_key"],
            "gw1:zone-a:stl-950001",
        )
        self.assertEqual(
            candidate_route["route_scope"],
            "zone_or_gateway_zone_return_reachability",
        )
        self.assertNotIn("current_compatibility_route_scope", candidate_route)
        self.assertEqual(
            candidate_route["target_route_scope"],
            "zone_or_gateway_zone_return_target",
        )
        self.assertEqual(candidate_route["route_prefixes"], ["10.10.0.101/32"])
        self.assertEqual(
            candidate_route["install_behavior"],
            "installed_by_shared_gz_backbone_runtime",
        )
        candidate_stack = dict(
            return_intent.get("candidate_shared_return_label_stack", {})
        )
        self.assertEqual(
            candidate_stack["target_kind"],
            "gateway_zone_return_label_stack_candidate",
        )
        self.assertEqual(
            candidate_stack["shared_backbone_return_target_key"],
            "gw1:zone-a:stl-950001",
        )
        self.assertEqual(
            candidate_stack["reverse_path_signature"],
            "gw1-gateway-slot0",
        )
        self.assertEqual(
            candidate_stack["shared_backbone_label_scope_id"],
            context["shared_backbone_label_scope_id"],
        )
        self.assertEqual(
            candidate_stack["reverse_label_count"],
            1,
        )
        self.assertEqual(
            candidate_stack["reverse_labels"],
            [200000],
        )
        self.assertEqual(
            candidate_stack["state_model"],
            "candidate_shared_gateway_zone_return_label_stack",
        )
        self.assertEqual(
            candidate_stack["install_behavior"],
            "installed_by_shared_gz_backbone_runtime",
        )
        self.assertEqual(
            return_intent["serving_satellite_delivery_target"]["delivery_scope"],
            "serving_satellite_local_access",
        )
        self.assertEqual(
            return_intent["serving_satellite_delivery_target"][
                "local_delivery_model"
            ],
            "access_link_delivery_to_ut",
        )
        self.assertEqual(
            return_intent["edge_local_delivery_target"],
            {
                "target_kind": "serving_satellite_local_ut_delivery",
                "serving_satellite_id": "stl-950001",
                "ut_id": "ut101",
                "state_owner": "serving_satellite_edge",
                "state_model": "local_ut_delivery_not_backbone_reachability",
            },
        )
        self.assertEqual(
            return_intent["state_ownership"]["backbone"]["scale_dimension"],
            "gateway_zone_or_zone_target",
        )
        self.assertEqual(
            return_intent["state_ownership"]["edge"]["scale_dimension"],
            "local_ut_delivery",
        )
        self.assertEqual(
            return_intent["bpf_realization"]["mode"],
            "shared_gz_fec_with_zone_edge_delivery",
        )
        self.assertFalse(return_intent["bpf_realization"]["linux_mpls_fallback"])
        self.assertFalse(return_intent["bpf_realization"]["per_ut_core_state"])

        self.assertEqual(context["reverse_link_ids"], ["gw1-gateway-slot0"])
        self.assertEqual(context["reverse_labels"], [200000])
        self.assertEqual(context["label_allocation_mode"], "shared_backbone_path")

    def test_zone_service_binding_declares_return_delivery_boundary(self) -> None:
        plan = UtInternetExperimentPlanner().plan(_snapshot())

        zone_actions = [
            action
            for action in _actions_for_node(plan, "stl-950001")
            if action.get("action_id") == "zone-service:zone-a"
        ]
        self.assertEqual(len(zone_actions), 1)

        intent = dict(zone_actions[0]["intent"])
        semantics = dict(intent.get("return_delivery_semantics", {}))
        self.assertEqual(semantics["intent_kind"], "zone_return_delivery")
        self.assertEqual(semantics["zone_serving_satellite_id"], "stl-950001")
        self.assertEqual(
            semantics["scale_target"],
            "shared_zone_reachability_before_local_ut_delivery",
        )
        self.assertEqual(
            zone_actions[0]["metadata"]["return_path_boundary"],
            "zone_return_intent",
        )

    def test_ut_gateway_selection_stays_lightweight(self) -> None:
        plan = UtInternetExperimentPlanner().plan(_snapshot())

        ut_actions = [
            action
            for action in _actions_for_node(plan, "ut101")
            if action.get("action_type") == "forwarding_entry"
        ]
        self.assertEqual(len(ut_actions), 1)
        gateway_selection = dict(
            ut_actions[0]["intent"]["outcome"]["gateway_selection"]
        )

        self.assertNotIn("return_path_intent", gateway_selection)
        self.assertNotIn("reverse_link_ids", gateway_selection)
        self.assertNotIn("reverse_labels", gateway_selection)

    def test_runtime_context_preserves_planner_zone_return_intent(self) -> None:
        snapshot = _snapshot()
        plan = UtInternetExperimentPlanner().plan(snapshot)
        topology = {
            "gateways": [
                {
                    "gateway_id": "gw1",
                    "bootstrap_network": {
                        "endpoint": {
                            "ip_address": "10.200.0.1/24",
                        }
                    },
                    "internet_uplink": {
                        "network_name": "gw1-uplink",
                        "ip_address": "10.200.0.1/24",
                    },
                },
                {
                    "gateway_id": "gw2",
                    "bootstrap_network": {
                        "endpoint": {"ip_address": "10.201.0.1/24"}
                    },
                    "internet_uplink": {
                        "network_name": "gw2-uplink",
                        "ip_address": "10.201.0.1/24",
                    },
                },
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            topology_path = Path(temp_dir) / "topology.json"
            topology_path.write_text(json.dumps(topology), encoding="utf-8")

            contexts = _realization_contexts(
                topology_path=topology_path,
                topology_snapshot=snapshot,
                runtime_update_plan=plan,
            )

        self.assertEqual(len(contexts), 2)
        context = next(item for item in contexts if item.selected_gateway_id == "gw1")
        self.assertEqual(context.zone_id, "zone-a")
        self.assertEqual(context.zone_return_prefixes, ("10.10.0.101/32",))
        self.assertEqual(context.gateway_class_id, "zone-a:default-gateway:gw1")
        self.assertEqual(
            context.return_path_intent["intent_kind"],
            "zone_return_delivery",
        )
        self.assertEqual(
            context.return_path_intent["bpf_realization"]["mode"],
            "shared_gz_fec_with_zone_edge_delivery",
        )

        intents = _zone_return_install_intents(contexts)
        self.assertEqual(len(intents), 2)
        default_intent = next(item for item in intents if item.selected_gateway_id == "gw1")
        self.assertEqual(default_intent.target_key, "gw1:zone-a:stl-950001")
        self.assertEqual(default_intent.flow_ids, ("ut-internet-egress:ut101",))
        self.assertEqual(default_intent.ut_ids, ("ut101",))
        self.assertEqual(
            default_intent.target_backbone_state_model,
            "shared_gateway_zone_or_zone_return_reachability",
        )

    def test_gz_fec_plan_groups_same_zone_uts_without_core_ut_scaling(self) -> None:
        plan = UtInternetExperimentPlanner().plan(_snapshot_with_two_uts_same_zone())

        fec_plan = gz_fec_plan(plan)
        self.assertFalse(
            fec_plan["planning_scope"]["backbone_state_scales_with_ut_count"]
        )
        self.assertEqual(fec_plan["flow_count"], 4)
        self.assertEqual(fec_plan["forward_fec_count"], 2)
        self.assertEqual(fec_plan["return_fec_count"], 2)
        for fec_group in fec_plan["forward_fecs"] + fec_plan["return_fecs"]:
            self.assertNotIn("None", fec_group["affected_nodes"])

        return_fec = next(
            item for item in fec_plan["return_fecs"] if item["selected_gateway_id"] == "gw1"
        )
        self.assertEqual(return_fec["target_key"], "gw1:zone-a:stl-950001")
        self.assertEqual(return_fec["ut_ids"], ["ut101", "ut102"])
        self.assertEqual(
            return_fec["flow_ids"],
            ["ut-internet-egress:ut101", "ut-internet-egress:ut102"],
        )
        self.assertEqual(
            return_fec["return_prefixes"],
            ["10.10.0.101/32", "10.10.0.102/32"],
        )

        cold_diff = diff_gz_fec_plans(None, fec_plan)
        self.assertEqual(cold_diff["changed_fec_group_count"], 4)
        self.assertEqual(cold_diff["diff_scope"], "changed_fec_zone_path_groups_only")

        unchanged_diff = diff_gz_fec_plans(fec_plan, fec_plan)
        self.assertEqual(unchanged_diff["changed_fec_group_count"], 0)
        self.assertEqual(unchanged_diff["skipped_unchanged_fec_group_count"], 4)
        self.assertEqual(unchanged_diff["estimated_lfib_route_operation_items"], 0)

    def test_runtime_plan_conversion_never_reuses_process_identity_cache(self) -> None:
        plan = UtInternetExperimentPlanner().plan(_snapshot())
        first = _runtime_plan_dict(plan)
        node_id = next(iter(plan.node_subplans))
        replacement = deepcopy(plan.node_subplans[node_id])
        replacement["conversion_marker"] = "current-plan-content"
        plan.node_subplans[node_id] = replacement

        second = _runtime_plan_dict(plan)

        self.assertNotIn("conversion_marker", first["node_subplans"][node_id])
        self.assertEqual(
            second["node_subplans"][node_id]["conversion_marker"],
            "current-plan-content",
        )


if __name__ == "__main__":
    unittest.main()
