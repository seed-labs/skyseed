#!/usr/bin/env python3
# encoding: utf-8

import unittest
from types import SimpleNamespace
from typing import Any

from linux_mpls_runtime import (
    ActiveRuntimeSession,
    DirectGatewayExperimentContext,
    MinimalRealLinuxMplsInstaller,
    MplsPathSegment,
    _default_preflight_state,
    _parse_probe_outputs,
    _select_incremental_runtime_contexts,
    _select_runtime_ping_validation_contexts,
    _shared_zone_return_apply_specs,
    _shared_zone_return_rollback_specs_for_contexts,
    _subset_active_runtime_session,
    _zone_return_install_intents_for_contexts,
    _zone_return_install_intents,
    _zone_return_install_boundary_payload,
)


def _segment(
    *,
    link_id: str,
    source_node_id: str,
    source_node_type: str,
    source_interface: str,
    source_ip: str,
    destination_node_id: str,
    destination_node_type: str,
    destination_interface: str,
    destination_ip: str,
    label: int,
) -> MplsPathSegment:
    return MplsPathSegment(
        link_id=link_id,
        source_node_id=source_node_id,
        source_node_type=source_node_type,
        source_interface=source_interface,
        source_ip=source_ip,
        destination_node_id=destination_node_id,
        destination_node_type=destination_node_type,
        destination_interface=destination_interface,
        destination_ip=destination_ip,
        label=label,
    )


def _context(
    *,
    flow_id: str,
    ut_id: str,
    serving_satellite_id: str,
    selected_gateway_id: str,
    selected_gateway_satellite_id: str,
    forward_label: int,
    reverse_label: int,
    zone_id: str | None = None,
    zone_return_prefixes: tuple[str, ...] = (),
    gateway_class_id: str | None = None,
    shared_backbone_return_target: dict[str, Any] | None = None,
    candidate_shared_zone_return_route: dict[str, Any] | None = None,
    candidate_shared_return_label_stack: dict[str, Any] | None = None,
    current_linux_realization_mode: str = "per_ut_flow_compatibility",
) -> DirectGatewayExperimentContext:
    forward_segment = _segment(
        link_id=f"{selected_gateway_id}-gateway-slot0",
        source_node_id=serving_satellite_id,
        source_node_type="satellite",
        source_interface="ga-g0s0",
        source_ip="10.0.0.2",
        destination_node_id=selected_gateway_id,
        destination_node_type="gateway",
        destination_interface="ga-g0s0",
        destination_ip="10.0.0.1",
        label=forward_label,
    )
    reverse_segment = _segment(
        link_id=f"{selected_gateway_id}-gateway-slot0",
        source_node_id=selected_gateway_id,
        source_node_type="gateway",
        source_interface="ga-g0s0",
        source_ip="10.0.0.1",
        destination_node_id=serving_satellite_id,
        destination_node_type="satellite",
        destination_interface="ga-g0s0",
        destination_ip="10.0.0.2",
        label=reverse_label,
    )
    return_path_intent: dict[str, Any] = {}
    if zone_id:
        return_path_intent = {
            "intent_kind": "zone_return_delivery",
            "zone_id": zone_id,
            "ut_id": ut_id,
            "selected_gateway_id": selected_gateway_id,
            "zone_serving_satellite_id": serving_satellite_id,
            "current_linux_realization": {
                "mode": current_linux_realization_mode,
            },
        }
    if shared_backbone_return_target is not None:
        return_path_intent.setdefault("intent_kind", "zone_return_delivery")
        return_path_intent["shared_backbone_return_target"] = dict(
            shared_backbone_return_target
        )
    if candidate_shared_return_label_stack is not None:
        return_path_intent.setdefault("intent_kind", "zone_return_delivery")
        return_path_intent["candidate_shared_return_label_stack"] = dict(
            candidate_shared_return_label_stack
        )
    if candidate_shared_zone_return_route is not None:
        return_path_intent.setdefault("intent_kind", "zone_return_delivery")
        return_path_intent["candidate_shared_zone_return_route"] = dict(
            candidate_shared_zone_return_route
        )

    return DirectGatewayExperimentContext(
        flow_id=flow_id,
        ut_id=ut_id,
        zone_id=zone_id,
        serving_satellite_id=serving_satellite_id,
        selected_gateway_id=selected_gateway_id,
        selected_gateway_satellite_id=selected_gateway_satellite_id,
        zone_return_prefixes=tuple(zone_return_prefixes),
        ping_target_ip="10.154.0.129",
        target_host_node_id="as154-host-new",
        policy_table_id=1000 + int(ut_id.removeprefix("ut")),
        policy_rule_priority=1000 + int(ut_id.removeprefix("ut")),
        satellite_path=(serving_satellite_id,),
        forward_labels=(forward_label,),
        reverse_labels=(reverse_label,),
        ut_access_interface="ut0",
        ut_access_ip=f"10.10.0.{int(ut_id.removeprefix('ut'))}",
        ut_access_peer_ip="10.10.0.1",
        gateway_access_interface="ga-g0s0",
        gateway_access_ip="10.0.0.1",
        gateway_access_peer_ip="10.0.0.2",
        gateway_nat_interface="gw-nat0",
        gateway_nat_source_ip="10.200.0.1",
        forward_segments=(forward_segment,),
        reverse_segments=(reverse_segment,),
        validation_targets=(
            {
                "node_id": "as154-host-new",
                "ip_address": "10.154.0.129",
            },
        ),
        gateway_class_id=gateway_class_id,
        return_path_intent=return_path_intent,
    )


def _session(*contexts: DirectGatewayExperimentContext) -> ActiveRuntimeSession:
    return ActiveRuntimeSession(
        session_id="test-session",
        backend={},
        target_slice={},
        host_runtime_boundary={},
        preflight={},
        apply_results=[],
        verify_results=[],
        ping_results=[
            {
                "flow_id": context.flow_id,
                "ut_id": context.ut_id,
                "success": True,
            }
            for context in contexts
        ],
        timing={},
        contexts=tuple(contexts),
        context_preflights=tuple(
            {"ut_id": context.ut_id, "flow_id": context.flow_id} for context in contexts
        ),
    )


class IncrementalRuntimeReuseTests(unittest.TestCase):
    def test_zone_return_install_intents_group_by_gateway_zone_and_serving_satellite(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            zone_id="zone-a",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
            zone_return_prefixes=("10.10.0.0/29",),
            gateway_class_id="zone-a:default-gateway:gw1",
            candidate_shared_zone_return_route={
                "target_key": "gw1:zone-a:stl-950117:zone-route",
                "route_prefixes": ["10.10.0.0/29"],
            },
            candidate_shared_return_label_stack={
                "target_key": "gw1:zone-a:stl-950117:reverse-path:gw1-gateway-slot0",
            },
        )
        flow_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            zone_id="zone-a",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10256,
            reverse_label=200256,
            zone_return_prefixes=("10.10.0.0/29",),
            gateway_class_id="zone-a:default-gateway:gw1",
            candidate_shared_zone_return_route={
                "target_key": "gw1:zone-a:stl-950117:zone-route",
                "route_prefixes": ["10.10.0.0/29"],
            },
            candidate_shared_return_label_stack={
                "target_key": "gw1:zone-a:stl-950117:reverse-path:gw1-gateway-slot0",
            },
        )

        intents = _zone_return_install_intents((flow_a, flow_b))

        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].target_key, "gw1:zone-a:stl-950117")
        self.assertEqual(intents[0].flow_ids, ("flow-a", "flow-b"))
        self.assertEqual(intents[0].ut_ids, ("ut101", "ut102"))
        self.assertEqual(
            intents[0].current_linux_realization_mode,
            "per_ut_flow_compatibility",
        )

    def test_zone_return_install_intents_do_not_merge_legacy_unknown_zones(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
        )
        flow_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10256,
            reverse_label=200256,
        )

        intents = _zone_return_install_intents((flow_a, flow_b))

        self.assertEqual(len(intents), 2)
        self.assertEqual(
            [intent.target_key for intent in intents],
            ["gw1:legacy-ut:ut101:stl-950117", "gw1:legacy-ut:ut102:stl-950117"],
        )

    def test_zone_return_install_intents_prefer_explicit_shared_target(self) -> None:
        shared_target = {
            "target_kind": "gateway_zone_return",
            "target_key": "legacy-gw-a:zone-shared:legacy-stl-a",
            "gateway_id": "legacy-gw-a",
            "zone_id": "zone-shared",
            "zone_serving_satellite_id": "legacy-stl-a",
            "state_owner": "satellite_backbone",
            "state_model": "shared_gateway_zone_or_zone_return_reachability",
        }
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            zone_id="legacy-zone-a",
            serving_satellite_id="legacy-stl-a",
            selected_gateway_id="legacy-gw-a",
            selected_gateway_satellite_id="legacy-stl-a",
            forward_label=10000,
            reverse_label=200000,
            gateway_class_id="legacy-zone-a:default-gateway:legacy-gw-a",
            shared_backbone_return_target=shared_target,
        )
        flow_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            zone_id="legacy-zone-b",
            serving_satellite_id="legacy-stl-a",
            selected_gateway_id="legacy-gw-a",
            selected_gateway_satellite_id="legacy-stl-a",
            forward_label=10256,
            reverse_label=200256,
            gateway_class_id="legacy-zone-b:default-gateway:legacy-gw-a",
            shared_backbone_return_target=shared_target,
        )

        intents = _zone_return_install_intents((flow_a, flow_b))

        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].target_key, "legacy-gw-a:zone-shared:legacy-stl-a")
        self.assertEqual(intents[0].selected_gateway_id, "legacy-gw-a")
        self.assertEqual(intents[0].zone_id, "zone-shared")
        self.assertEqual(intents[0].zone_serving_satellite_id, "legacy-stl-a")
        self.assertEqual(intents[0].flow_ids, ("flow-a", "flow-b"))
        self.assertEqual(intents[0].ut_ids, ("ut101", "ut102"))

    def test_zone_return_install_boundary_payload_counts_current_vs_shared_state(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            zone_id="zone-a",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
            zone_return_prefixes=("10.10.0.0/29",),
            gateway_class_id="zone-a:default-gateway:gw1",
            candidate_shared_zone_return_route={
                "target_key": "gw1:zone-a:stl-950117:zone-route",
                "route_prefixes": ["10.10.0.0/29"],
            },
            candidate_shared_return_label_stack={
                "target_key": "gw1:zone-a:stl-950117:reverse-path:gw1-gateway-slot0",
            },
        )
        flow_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            zone_id="zone-a",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10256,
            reverse_label=200256,
            zone_return_prefixes=("10.10.0.0/29",),
            gateway_class_id="zone-a:default-gateway:gw1",
            candidate_shared_zone_return_route={
                "target_key": "gw1:zone-a:stl-950117:zone-route",
                "route_prefixes": ["10.10.0.0/29"],
            },
            candidate_shared_return_label_stack={
                "target_key": "gw1:zone-a:stl-950117:reverse-path:gw1-gateway-slot0",
            },
        )
        intents = _zone_return_install_intents((flow_a, flow_b))

        payload = _zone_return_install_boundary_payload(
            all_contexts=(flow_a, flow_b),
            reapplied_contexts=(flow_b,),
            all_intents=intents,
            reapplied_intents=_zone_return_install_intents((flow_b,)),
        )

        self.assertEqual(payload["all_intent_count"], 1)
        self.assertEqual(payload["reapplied_intent_count"], 1)
        self.assertEqual(
            payload["candidate_shared_label_stack_behavior"],
            "installed_as_shared_zone_return_backbone",
        )
        self.assertEqual(
            payload["candidate_shared_zone_route_behavior"],
            "installed_as_shared_zone_return_backbone",
        )
        self.assertEqual(payload["all_candidate_shared_zone_route_count"], 1)
        self.assertEqual(payload["reapplied_candidate_shared_zone_route_count"], 1)
        self.assertEqual(
            payload["shared_zone_return_install_plan_behavior"],
            "installed_shared_zone_return_backbone",
        )
        self.assertEqual(payload["all_shared_zone_return_install_plan_count"], 1)
        self.assertEqual(payload["reapplied_shared_zone_return_install_plan_count"], 1)
        shared_plan = payload["all_shared_zone_return_install_plans"][0]
        self.assertEqual(shared_plan["target_key"], "gw1:zone-a:stl-950117")
        self.assertEqual(shared_plan["route_prefixes"], ("10.10.0.0/29",))
        self.assertEqual(shared_plan["flow_ids"], ("flow-a", "flow-b"))
        self.assertEqual(shared_plan["ut_ids"], ("ut101", "ut102"))
        self.assertEqual(shared_plan["planned_owner"], "satellite_backbone")
        self.assertEqual(
            shared_plan["planned_route_model"],
            "shared_gateway_zone_or_zone_return_reachability",
        )
        self.assertEqual(
            shared_plan["replaces_current_install_points"],
            (
                "gateway_ut32_return_route",
                "reverse_lfib_label_stack",
                "reverse_transit_lfib_entry",
            ),
        )
        self.assertEqual(
            shared_plan["preserves_edge_install_points"],
            (
                "serving_satellite_ut32_delivery_route",
                "gateway_nat_forward_rules",
            ),
        )
        self.assertEqual(
            payload["reapplied_shared_zone_return_install_plans"][0]["flow_ids"],
            ("flow-b",),
        )
        self.assertEqual(
            payload["all_candidate_shared_zone_route_keys"],
            ["gw1:zone-a:stl-950117:zone-route"],
        )
        self.assertEqual(payload["all_candidate_shared_label_stack_count"], 1)
        self.assertEqual(payload["reapplied_candidate_shared_label_stack_count"], 1)
        self.assertEqual(
            payload["all_candidate_shared_label_stack_keys"],
            ["gw1:zone-a:stl-950117:reverse-path:gw1-gateway-slot0"],
        )
        self.assertEqual(
            payload["scale_summary"]["all"][
                "candidate_shared_zone_route_count"
            ],
            1,
        )
        self.assertEqual(
            payload["scale_summary"]["all"][
                "current_backbone_return_state_item_count"
            ],
            4,
        )
        self.assertEqual(
            payload["scale_summary"]["all"][
                "target_shared_backbone_return_state_item_count"
            ],
            1,
        )
        self.assertEqual(
            payload["scale_summary"]["all"][
                "current_to_target_backbone_state_ratio"
            ],
            4.0,
        )
        self.assertEqual(
            payload["scale_summary"]["all"][
                "current_reverse_labels_to_candidate_label_stack_ratio"
            ],
            2.0,
        )
        self.assertEqual(
            payload["scale_summary"]["reapplied"][
                "current_backbone_return_state_item_count"
            ],
            2,
        )

    def test_apply_specs_install_shared_zone_return_backbone_not_per_flow_reverse_backbone(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            zone_id="zone-a",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
            zone_return_prefixes=("10.10.0.0/29",),
            gateway_class_id="zone-a:default-gateway:gw1",
            candidate_shared_zone_return_route={
                "target_key": "gw1:zone-a:stl-950117:zone-route",
                "route_prefixes": ["10.10.0.0/29"],
            },
        )
        installer = MinimalRealLinuxMplsInstaller()
        per_flow_specs = installer._apply_specs(
            flow_a,
            _default_preflight_state(flow_a),
        )
        per_flow_descriptions = {
            spec.description for spec in per_flow_specs
        }

        self.assertNotIn("install gateway reverse ingress route", per_flow_descriptions)
        self.assertNotIn("install serving-satellite reverse pop entry", per_flow_descriptions)
        self.assertIn("install serving-satellite UT delivery route", per_flow_descriptions)
        ut_apply_specs = [spec for spec in per_flow_specs if spec.node_id == flow_a.ut_id]
        self.assertTrue(ut_apply_specs)
        self.assertTrue(
            any(spec.description == "install UT default route" for spec in ut_apply_specs)
        )
        self.assertFalse(
            any("mpls" in " ".join(spec.command) for spec in ut_apply_specs)
        )
        self.assertFalse(
            any("encap" in " ".join(spec.command) for spec in ut_apply_specs)
        )
        gateway_iptables_apply_commands = [
            " ".join(spec.command)
            for spec in per_flow_specs
            if spec.node_id == flow_a.selected_gateway_id
            and "iptables" in " ".join(spec.command)
        ]
        self.assertTrue(gateway_iptables_apply_commands)
        self.assertTrue(
            all("iptables -w 10 " in command for command in gateway_iptables_apply_commands)
        )

        shared_specs = _shared_zone_return_apply_specs(
            _zone_return_install_intents((flow_a,))
        )
        shared_commands = [spec.command for spec in shared_specs]
        shared_descriptions = [spec.description for spec in shared_specs]

        self.assertFalse(any(spec.node_id == flow_a.ut_id for spec in shared_specs))
        self.assertIn(
            (
                "ip",
                "route",
                "replace",
                "10.10.0.0/29",
                "encap",
                "mpls",
                "200000",
                "via",
                "inet",
                "10.0.0.2",
                "dev",
                "ga-g0s0",
            ),
            shared_commands,
        )
        self.assertTrue(
            any(
                spec.node_id == flow_a.serving_satellite_id
                and spec.command == ("ip", "-f", "mpls", "route", "replace", "200000", "dev", "lo")
                for spec in shared_specs
            )
        )
        self.assertTrue(
            any(
                description.startswith(
                    "install shared serving-satellite reverse pop entry"
                )
                for description in shared_descriptions
            )
        )

    def test_zone_return_boundary_mapping_reports_actual_shared_gz_install(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            zone_id="zone-a",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
            zone_return_prefixes=("10.10.0.0/29",),
            gateway_class_id="zone-a:default-gateway:gw1",
            candidate_shared_zone_return_route={
                "target_key": "gw1:zone-a:stl-950117:zone-route",
                "route_prefixes": ["10.10.0.0/29"],
            },
            candidate_shared_return_label_stack={
                "target_key": "gw1:zone-a:stl-950117:reverse-path:gw1-gateway-slot0",
            },
            current_linux_realization_mode="shared_gz_backbone",
        )
        flow_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            zone_id="zone-a",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10256,
            reverse_label=200256,
            zone_return_prefixes=("10.10.0.0/29",),
            gateway_class_id="zone-a:default-gateway:gw1",
            candidate_shared_zone_return_route={
                "target_key": "gw1:zone-a:stl-950117:zone-route",
                "route_prefixes": ["10.10.0.0/29"],
            },
            candidate_shared_return_label_stack={
                "target_key": "gw1:zone-a:stl-950117:reverse-path:gw1-gateway-slot0",
            },
            current_linux_realization_mode="shared_gz_backbone",
        )
        intents = _zone_return_install_intents((flow_a, flow_b))

        payload = _zone_return_install_boundary_payload(
            all_contexts=(flow_a, flow_b),
            reapplied_contexts=(flow_a, flow_b),
            all_intents=intents,
            reapplied_intents=intents,
        )

        self.assertEqual(payload["install_behavior"], "shared_gz_backbone")
        self.assertIn("state_count_model", payload)
        self.assertEqual(
            payload["scale_summary"]["all"]["gateway_ut32_return_route_count"],
            0,
        )
        self.assertEqual(payload["scale_summary"]["all"]["reverse_label_count"], 0)
        self.assertEqual(
            payload["scale_summary"]["all"]["reverse_transit_lfib_entry_count"],
            0,
        )
        self.assertEqual(
            payload["scale_summary"]["all"]["legacy_compatibility_projection"][
                "gateway_ut32_return_route_count"
            ],
            2,
        )
        self.assertEqual(
            payload["scale_summary"]["all"]["shared_gz_backbone_group_count"],
            1,
        )
        self.assertEqual(
            payload["scale_summary"]["all"][
                "shared_gz_conceptual_backbone_state_item_count"
            ],
            1,
        )
        self.assertEqual(
            payload["scale_summary"]["all"]["forbidden_legacy_backbone_state_item_count"],
            0,
        )
        self.assertEqual(
            payload["scale_summary"]["all"]["linux_shared_zone_route_count"],
            1,
        )
        self.assertEqual(
            payload["scale_summary"]["all"]["linux_shared_reverse_lfib_entry_count"],
            1,
        )
        self.assertEqual(
            payload["scale_summary"]["all"][
                "linux_shared_reverse_pop_lfib_entry_count"
            ],
            1,
        )
        self.assertEqual(
            payload["scale_summary"]["all"][
                "linux_shared_backbone_command_state_item_count"
            ],
            2,
        )
        mapping = payload["install_mapping"]
        self.assertEqual(mapping["current_linux_realization"], "shared_gz_backbone")
        self.assertEqual(mapping["all_intent_count"], 1)
        self.assertTrue(mapping["all_shared_apply_specs"])
        self.assertTrue(
            all(
                item["actual_install_count"] == 0
                for item in mapping["forbidden_legacy_backbone_command_families"]
            )
        )
        rendered_commands = [
            " ".join(spec["command"]) for spec in mapping["all_shared_apply_specs"]
        ]
        self.assertTrue(
            any("ip route replace 10.10.0.0/29 encap mpls" in command for command in rendered_commands)
        )
        self.assertFalse(
            any("10.10.0.101/32 encap mpls" in command for command in rendered_commands)
        )

    def test_reapplied_zone_return_intent_reuses_stable_active_group_labels(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            zone_id="zone-a",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
            zone_return_prefixes=("10.10.0.0/29",),
            gateway_class_id="zone-a:default-gateway:gw1",
            candidate_shared_zone_return_route={
                "target_key": "gw1:zone-a:stl-950117:zone-route",
                "route_prefixes": ["10.10.0.0/29"],
            },
            candidate_shared_return_label_stack={
                "target_key": "gw1:zone-a:stl-950117:reverse-path:gw1-gateway-slot0",
            },
            current_linux_realization_mode="shared_gz_backbone",
        )
        flow_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            zone_id="zone-a",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10256,
            reverse_label=200256,
            zone_return_prefixes=("10.10.0.0/29",),
            gateway_class_id="zone-a:default-gateway:gw1",
            candidate_shared_zone_return_route={
                "target_key": "gw1:zone-a:stl-950117:zone-route",
                "route_prefixes": ["10.10.0.0/29"],
            },
            candidate_shared_return_label_stack={
                "target_key": "gw1:zone-a:stl-950117:reverse-path:gw1-gateway-slot0",
            },
            current_linux_realization_mode="shared_gz_backbone",
        )
        all_intents = _zone_return_install_intents((flow_a, flow_b))
        reapplied_intents = _zone_return_install_intents_for_contexts(
            all_intents,
            (flow_b,),
        )

        self.assertEqual(len(all_intents), 1)
        self.assertEqual(len(reapplied_intents), 1)
        self.assertEqual(reapplied_intents[0].reverse_labels, (200000,))
        rendered_commands = [
            " ".join(spec.command)
            for spec in _shared_zone_return_apply_specs(reapplied_intents)
        ]
        self.assertTrue(any("encap mpls 200000" in command for command in rendered_commands))
        self.assertFalse(any("encap mpls 200256" in command for command in rendered_commands))

    def test_incremental_cleanup_preserves_active_shared_zone_return_group(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            zone_id="zone-a",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
            zone_return_prefixes=("10.10.0.0/29",),
            gateway_class_id="zone-a:default-gateway:gw1",
            candidate_shared_zone_return_route={
                "target_key": "gw1:zone-a:stl-950117:zone-route",
                "route_prefixes": ["10.10.0.0/29"],
            },
            candidate_shared_return_label_stack={
                "target_key": "gw1:zone-a:stl-950117:reverse-path:gw1-gateway-slot0",
            },
            current_linux_realization_mode="shared_gz_backbone",
        )
        flow_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            zone_id="zone-a",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10256,
            reverse_label=200256,
            zone_return_prefixes=("10.10.0.0/29",),
            gateway_class_id="zone-a:default-gateway:gw1",
            candidate_shared_zone_return_route={
                "target_key": "gw1:zone-a:stl-950117:zone-route",
                "route_prefixes": ["10.10.0.0/29"],
            },
            candidate_shared_return_label_stack={
                "target_key": "gw1:zone-a:stl-950117:reverse-path:gw1-gateway-slot0",
            },
            current_linux_realization_mode="shared_gz_backbone",
        )
        target_key = _zone_return_install_intents((flow_a, flow_b))[0].target_key

        preserved_specs = _shared_zone_return_rollback_specs_for_contexts(
            (flow_a,),
            preserve_target_keys=(target_key,),
        )
        final_specs = _shared_zone_return_rollback_specs_for_contexts((flow_a,))

        self.assertEqual(preserved_specs, [])
        self.assertTrue(final_specs)
        self.assertTrue(
            any(
                spec.command == ("ip", "route", "del", "10.10.0.0/29")
                for spec in final_specs
            )
        )
        self.assertTrue(
            any(
                spec.command == ("ip", "-f", "mpls", "route", "del", "200000")
                for spec in final_specs
            )
        )

    def test_shared_gz_per_flow_rollback_skips_legacy_reverse_backbone_cleanup(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            zone_id="zone-a",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
            zone_return_prefixes=("10.10.0.0/29",),
            gateway_class_id="zone-a:default-gateway:gw1",
            candidate_shared_zone_return_route={
                "target_key": "gw1:zone-a:stl-950117:zone-route",
                "route_prefixes": ["10.10.0.0/29"],
            },
            candidate_shared_return_label_stack={
                "target_key": "gw1:zone-a:stl-950117:reverse-path:gw1-gateway-slot0",
            },
            current_linux_realization_mode="shared_gz_backbone",
        )
        specs = MinimalRealLinuxMplsInstaller()._rollback_specs(
            flow_a,
            _default_preflight_state(flow_a),
        )
        descriptions = {spec.description for spec in specs}

        self.assertNotIn("delete gateway reverse ingress route", descriptions)
        self.assertNotIn("delete serving-satellite reverse pop entry", descriptions)
        self.assertFalse(
            any(description.startswith("delete reverse LFIB") for description in descriptions)
        )
        self.assertIn("delete serving-satellite UT delivery route", descriptions)

    def test_rollback_verification_plan_checks_shared_zone_return_group_cleanup(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            zone_id="zone-a",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
            zone_return_prefixes=("10.10.0.0/29",),
            gateway_class_id="zone-a:default-gateway:gw1",
            candidate_shared_zone_return_route={
                "target_key": "gw1:zone-a:stl-950117:zone-route",
                "route_prefixes": ["10.10.0.0/29"],
            },
            candidate_shared_return_label_stack={
                "target_key": "gw1:zone-a:stl-950117:reverse-path:gw1-gateway-slot0",
            },
            current_linux_realization_mode="shared_gz_backbone",
        )

        plan = MinimalRealLinuxMplsInstaller()._rollback_verification_probe_plan(
            (flow_a,)
        )
        group_probe = plan["shared_zone_return_group_probes"][0]

        self.assertFalse(group_probe["shared_group_cleanup_verification_skipped"])
        self.assertEqual(group_probe["route_probes"].keys(), {"10.10.0.0/29"})
        self.assertEqual(
            group_probe["mpls_label_probes"].keys(),
            {"stl-950117:200000"},
        )

    def test_rollback_verification_plan_skips_preserved_shared_zone_return_group(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            zone_id="zone-a",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
            zone_return_prefixes=("10.10.0.0/29",),
            gateway_class_id="zone-a:default-gateway:gw1",
            candidate_shared_zone_return_route={
                "target_key": "gw1:zone-a:stl-950117:zone-route",
                "route_prefixes": ["10.10.0.0/29"],
            },
            candidate_shared_return_label_stack={
                "target_key": "gw1:zone-a:stl-950117:reverse-path:gw1-gateway-slot0",
            },
            current_linux_realization_mode="shared_gz_backbone",
        )
        target_key = _zone_return_install_intents((flow_a,))[0].target_key

        plan = MinimalRealLinuxMplsInstaller()._rollback_verification_probe_plan(
            (flow_a,),
            preserve_shared_zone_return_target_keys=(target_key,),
        )
        group_probe = plan["shared_zone_return_group_probes"][0]

        self.assertTrue(group_probe["shared_group_cleanup_verification_skipped"])
        self.assertEqual(
            group_probe["skip_reason"],
            "shared_zone_return_target_preserved_for_active_flows",
        )
        self.assertEqual(group_probe["route_probes"], {})
        self.assertEqual(group_probe["mpls_label_probes"], {})

    def test_selection_allows_full_hold_when_every_flow_is_unchanged(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
        )
        flow_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            serving_satellite_id="stl-950118",
            selected_gateway_id="gw2",
            selected_gateway_satellite_id="stl-950118",
            forward_label=10256,
            reverse_label=200256,
        )

        selection = _select_incremental_runtime_contexts(
            all_contexts=(flow_a, flow_b),
            existing_session=_session(flow_a, flow_b),
        )

        self.assertEqual(selection["unchanged_flow_ids"], {"flow-a", "flow-b"})
        self.assertEqual(selection["incremental_cleanup_flow_ids"], [])
        self.assertEqual(selection["changed_contexts"], ())

    def test_selection_holds_unchanged_flows_and_reapplies_changed_ones(self) -> None:
        old_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
            gateway_class_id="zone-a:default-gateway:gw1",
        )
        old_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            serving_satellite_id="stl-950118",
            selected_gateway_id="gw2",
            selected_gateway_satellite_id="stl-950118",
            forward_label=10256,
            reverse_label=200256,
        )
        new_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
            gateway_class_id="zone-a:default-gateway:gw1",
        )
        new_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            serving_satellite_id="stl-950119",
            selected_gateway_id="gw2",
            selected_gateway_satellite_id="stl-950119",
            forward_label=10512,
            reverse_label=200512,
        )

        selection = _select_incremental_runtime_contexts(
            all_contexts=(new_a, new_b),
            existing_session=_session(old_a, old_b),
        )

        self.assertEqual(selection["unchanged_flow_ids"], {"flow-a"})
        self.assertEqual(selection["incremental_cleanup_flow_ids"], ["flow-b"])
        self.assertEqual(
            [context.flow_id for context in selection["changed_contexts"]],
            ["flow-b"],
        )

    def test_selection_marks_removed_flows_for_cleanup(self) -> None:
        old_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
        )
        old_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            serving_satellite_id="stl-950118",
            selected_gateway_id="gw2",
            selected_gateway_satellite_id="stl-950118",
            forward_label=10256,
            reverse_label=200256,
        )
        selection = _select_incremental_runtime_contexts(
            all_contexts=(old_a,),
            existing_session=_session(old_a, old_b),
        )

        self.assertEqual(selection["unchanged_flow_ids"], {"flow-a"})
        self.assertEqual(selection["incremental_cleanup_flow_ids"], ["flow-b"])
        self.assertEqual(selection["changed_contexts"], ())

    def test_ping_validation_scope_samples_reapplied_gateway_classes(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
            gateway_class_id="zone-a:default-gateway:gw1",
        )
        flow_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            serving_satellite_id="stl-950118",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950118",
            forward_label=10256,
            reverse_label=200256,
            gateway_class_id="zone-a:default-gateway:gw1",
        )
        flow_c = _context(
            flow_id="flow-c",
            ut_id="ut103",
            serving_satellite_id="stl-950119",
            selected_gateway_id="gw2",
            selected_gateway_satellite_id="stl-950119",
            forward_label=10512,
            reverse_label=200512,
            gateway_class_id="zone-a:default-gateway:gw2",
        )

        selection = _select_runtime_ping_validation_contexts(
            all_contexts=(flow_a, flow_b, flow_c),
            reapplied_contexts=(flow_a, flow_b, flow_c),
            validation_scope="reapplied_gateway_class_sample",
        )

        self.assertEqual(selection["target_flow_ids"], ["flow-a", "flow-c"])
        self.assertEqual(
            selection["target_gateway_class_keys"],
            ["zone-a:default-gateway:gw1", "zone-a:default-gateway:gw2"],
        )
        self.assertEqual(
            selection["representative_flow_by_gateway_class"],
            {
                "zone-a:default-gateway:gw1": "flow-a",
                "zone-a:default-gateway:gw2": "flow-c",
            },
        )
        self.assertEqual(selection["active_flow_count"], 3)
        self.assertEqual(selection["reapplied_flow_count"], 3)
        self.assertEqual(selection["sample_source_flow_count"], 3)
        self.assertEqual(selection["selected_flow_count"], 2)
        self.assertEqual(selection["sample_source_gateway_class_count"], 2)
        self.assertEqual(selection["selected_gateway_class_count"], 2)
        self.assertEqual(selection["gateway_class_sample_reduction_count"], 1)
        self.assertEqual(selection["selected_to_sample_source_flow_ratio"], 0.666667)

    def test_ping_validation_scope_falls_back_when_no_flows_reapplied(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
        )
        flow_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            serving_satellite_id="stl-950118",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950118",
            forward_label=10256,
            reverse_label=200256,
        )

        selection = _select_runtime_ping_validation_contexts(
            all_contexts=(flow_a, flow_b),
            reapplied_contexts=(),
            validation_scope="reapplied_gateway_class_sample",
        )

        self.assertEqual(selection["target_flow_ids"], ["flow-a"])
        self.assertEqual(selection["fallback_reason"], "no_reapplied_flows")
        self.assertEqual(selection["sample_source"], "all_active_flows_fallback")
        self.assertEqual(selection["active_flow_count"], 2)
        self.assertEqual(selection["reapplied_flow_count"], 0)
        self.assertEqual(selection["selected_flow_count"], 1)
        self.assertEqual(selection["gateway_class_sample_reduction_count"], 1)

    def test_ping_validation_scope_can_keep_all_active_flows(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
        )
        flow_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            serving_satellite_id="stl-950118",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950118",
            forward_label=10256,
            reverse_label=200256,
        )

        selection = _select_runtime_ping_validation_contexts(
            all_contexts=(flow_a, flow_b),
            reapplied_contexts=(flow_a,),
            validation_scope="all_active_flows",
        )

        self.assertEqual(selection["target_flow_ids"], ["flow-a", "flow-b"])
        self.assertEqual(selection["selected_flow_count"], 2)
        self.assertEqual(selection["gateway_class_sample_reduction_count"], 0)
        self.assertEqual(selection["selected_to_sample_source_flow_ratio"], 1.0)

    def test_subset_session_filters_contexts_and_ping_results(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
        )
        flow_b = _context(
            flow_id="flow-b",
            ut_id="ut102",
            serving_satellite_id="stl-950118",
            selected_gateway_id="gw2",
            selected_gateway_satellite_id="stl-950118",
            forward_label=10256,
            reverse_label=200256,
        )
        subset = _subset_active_runtime_session(
            _session(flow_a, flow_b),
            flow_ids=("flow-b",),
        )

        self.assertEqual([context.flow_id for context in subset.contexts], ["flow-b"])
        self.assertEqual(
            [result["flow_id"] for result in subset.ping_results],
            ["flow-b"],
        )
        self.assertEqual(subset.target_slice["executed_flow_ids"], ["flow-b"])
        self.assertEqual(subset.target_slice["executed_ut_ids"], ["ut102"])
        self.assertTrue(subset.target_slice["contains_multi_hop"] is False)

    def test_probe_output_parser_extracts_marked_command_output(self) -> None:
        outputs = _parse_probe_outputs(
            [
                SimpleNamespace(
                    stdout=(
                        "[rollback_verify] verify a\n"
                        "__SKYSEED_PROBE_BEGIN_p0000__\n"
                        "default via 10.0.0.1 dev ut0\n"
                        "__SKYSEED_PROBE_END_p0000__\n"
                        "[rollback_verify] verify b\n"
                        "__SKYSEED_PROBE_BEGIN_p0001__\n"
                        "\n"
                        "__SKYSEED_PROBE_END_p0001__"
                    )
                )
            ],
            probe_ids=("p0000", "p0001", "p9999"),
        )

        self.assertEqual(outputs["p0000"], "default via 10.0.0.1 dev ut0")
        self.assertEqual(outputs["p0001"], "")
        self.assertEqual(outputs["p9999"], "")

    def test_rollback_verification_probe_plan_deduplicates_identical_probes(self) -> None:
        flow_a = _context(
            flow_id="flow-a",
            ut_id="ut101",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
        )
        flow_b = _context(
            flow_id="flow-b",
            ut_id="ut101",
            serving_satellite_id="stl-950117",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="stl-950117",
            forward_label=10000,
            reverse_label=200000,
        )
        installer = MinimalRealLinuxMplsInstaller()

        plan = installer._rollback_verification_probe_plan((flow_a, flow_b))

        self.assertEqual(len(plan["context_probes"]), 2)
        self.assertEqual(len(plan["specs"]), 3)
        self.assertEqual(
            plan["context_probes"][0]["ut_default_route_probe"],
            plan["context_probes"][1]["ut_default_route_probe"],
        )
        self.assertEqual(
            plan["context_probes"][0]["sat_policy_default_probe"],
            plan["context_probes"][1]["sat_policy_default_probe"],
        )
        self.assertEqual(
            plan["context_probes"][0]["gw_forward_pop_probe"],
            plan["context_probes"][1]["gw_forward_pop_probe"],
        )


if __name__ == "__main__":
    unittest.main()
