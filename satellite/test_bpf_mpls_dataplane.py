#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

import unittest

from satellite.bpf_mpls_dataplane import (
    G_TO_Z,
    Z_TO_G,
    assess_future_pre_attached_interface_inertness,
    audit_future_interface_physical_visibility,
    compile_bpf_owned_mpls_maps,
    logical_ifindex,
    simulate_core_lfib_lookup,
    simulate_egress_handoff_lookup,
    simulate_ingress_lookup,
    stable_u32,
)


def _segment(
    link_id: str,
    source_node_id: str,
    source_interface: str,
    destination_node_id: str,
    destination_interface: str,
    label: int,
) -> dict[str, object]:
    return {
        "link_id": link_id,
        "source_node_id": source_node_id,
        "source_interface": source_interface,
        "destination_node_id": destination_node_id,
        "destination_interface": destination_interface,
        "label": label,
    }


def _context(
    *,
    flow_id: str = "ut-internet-egress:ut42",
    target_ip: str = "10.154.42.129",
    selected_gateway_id: str = "gw5",
    traffic_scope: str = "default_gateway_class",
    label_offset: int = 0,
) -> dict[str, object]:
    return {
        "flow_id": flow_id,
        "fec_id": f"zone-test:{selected_gateway_id}:stl-a:{flow_id}",
        "ut_id": "ut42",
        "zone_id": "zone-test",
        "serving_satellite_id": "stl-a",
        "selected_gateway_id": selected_gateway_id,
        "target_ip": target_ip,
        "traffic_scope": traffic_scope,
        "ut_access_interface": "ua-u42s0",
        "ut_access_ip": "10.90.42.2",
        "forward_segments": (
            _segment("a-b", "stl-a", "ab0", "stl-b", "ab0", 1000 + label_offset),
            _segment("b-c", "stl-b", "bc0", "stl-c", "bc0", 1001 + label_offset),
            _segment(
                "c-gw",
                "stl-c",
                f"ga-{selected_gateway_id}s0",
                selected_gateway_id,
                f"ga-{selected_gateway_id}s0",
                1002 + label_offset,
            ),
        ),
        "reverse_segments": (
            _segment(
                "gw-c",
                selected_gateway_id,
                f"ga-{selected_gateway_id}s0",
                "stl-c",
                f"ga-{selected_gateway_id}s0",
                2000 + label_offset,
            ),
            _segment("c-b", "stl-c", "cb0", "stl-b", "cb0", 2001 + label_offset),
            _segment("b-a", "stl-b", "ba0", "stl-a", "ba0", 2002 + label_offset),
        ),
    }


class BpfMplsDataplaneTest(unittest.TestCase):
    def test_ingress_classifies_packet_to_fec_and_initial_label(self) -> None:
        context = _context()
        compiled = compile_bpf_owned_mpls_maps([context], generation=7)
        flow_key = stable_u32(
            "ut42|zone-test|gw5|ut-internet-egress:ut42|default_gateway_class"
        )
        result = simulate_ingress_lookup(
            compiled,
            generation=7,
            ingress_ifindex=logical_ifindex("stl-a", "ua-u42s0"),
            flow_key=flow_key,
            target_ip="10.154.42.129",
            service_class="default_gateway_class",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "push")
        self.assertEqual(result["direction"], G_TO_Z)
        self.assertEqual(result["initial_label"], 1000)
        self.assertEqual(result["selected_gateway_id"], "gw5")

    def test_intermediate_hop_uses_label_not_destination_ip(self) -> None:
        compiled = compile_bpf_owned_mpls_maps([_context()], generation=0)
        ingress_ifindex = logical_ifindex("stl-b", "ab0")
        with_original_dst = simulate_core_lfib_lookup(
            compiled,
            generation=0,
            ingress_ifindex=ingress_ifindex,
            label=1000,
            direction=G_TO_Z,
            dst_ip="10.154.42.129",
        )
        with_different_dst = simulate_core_lfib_lookup(
            compiled,
            generation=0,
            ingress_ifindex=ingress_ifindex,
            label=1000,
            direction=G_TO_Z,
            dst_ip="203.0.113.77",
        )

        self.assertTrue(with_original_dst["ok"])
        self.assertTrue(with_different_dst["ok"])
        self.assertEqual(with_original_dst["out_label"], 1001)
        self.assertEqual(with_original_dst, with_different_dst)
        self.assertFalse(with_original_dst["dst_ip_considered"])
        self.assertFalse(
            any("dst_ip" in entry["key"] for entry in compiled["core_lfib_map"])
        )

    def test_return_handoff_uses_label_and_fec_context(self) -> None:
        compiled = compile_bpf_owned_mpls_maps([_context()], generation=0)
        handoff = simulate_egress_handoff_lookup(
            compiled,
            generation=0,
            ingress_ifindex=logical_ifindex("stl-a", "ba0"),
            label=2002,
            direction=Z_TO_G,
        )

        self.assertTrue(handoff["ok"])
        self.assertEqual(handoff["action"], "pop_and_handoff")
        self.assertEqual(handoff["handoff"], "ut_return")
        self.assertEqual(handoff["new_dst_ip"], "10.90.42.2")

    def test_generation_switch_changes_lfib_behavior(self) -> None:
        compiled = compile_bpf_owned_mpls_maps([_context()], generation=0)
        compiled["core_lfib_map"].extend(
            compile_bpf_owned_mpls_maps(
                [_context(label_offset=500)],
                generation=1,
            )["core_lfib_map"]
        )
        old_generation = simulate_core_lfib_lookup(
            compiled,
            generation=0,
            ingress_ifindex=logical_ifindex("stl-b", "ab0"),
            label=1000,
            direction=G_TO_Z,
        )
        new_generation = simulate_core_lfib_lookup(
            compiled,
            generation=1,
            ingress_ifindex=logical_ifindex("stl-b", "ab0"),
            label=1500,
            direction=G_TO_Z,
        )
        wrong_label_after_switch = simulate_core_lfib_lookup(
            compiled,
            generation=1,
            ingress_ifindex=logical_ifindex("stl-b", "ab0"),
            label=1000,
            direction=G_TO_Z,
        )

        self.assertEqual(old_generation["out_label"], 1001)
        self.assertEqual(new_generation["out_label"], 1501)
        self.assertFalse(wrong_label_after_switch["ok"])
        self.assertEqual(
            wrong_label_after_switch["reason"],
            "core_lfib_miss_strict_no_linux_fallback",
        )

    def test_missing_label_is_strict_failure_not_linux_fallback(self) -> None:
        compiled = compile_bpf_owned_mpls_maps([_context()], generation=0)
        result = simulate_core_lfib_lookup(
            compiled,
            generation=0,
            ingress_ifindex=logical_ifindex("stl-b", "ab0"),
            label=None,
            direction=G_TO_Z,
            dst_ip="10.154.42.129",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "drop")
        self.assertEqual(result["reason"], "missing_label_strict_no_linux_fallback")
        self.assertFalse(result["linux_fallback"])

    def test_default_and_override_share_core_model_without_target_ip_core_keys(
        self,
    ) -> None:
        contexts = [
            _context(),
            _context(
                flow_id="ut-internet-egress:ut42:override:asn160_via_selected_secondary_gateway",
                target_ip="10.160.42.72",
                selected_gateway_id="gw9",
                traffic_scope="special_prefix_override",
                label_offset=100,
            ),
        ]
        compiled = compile_bpf_owned_mpls_maps(contexts, generation=0)

        prefixes = {
            entry["key"]["target_prefix"]
            for entry in compiled["ingress_classifier_map"]
        }
        self.assertEqual(prefixes, {"10.154.42.129/32", "10.160.42.0/24"})
        self.assertEqual(len(compiled["ingress_classifier_map"]), 2)
        self.assertGreaterEqual(len(compiled["core_lfib_map"]), 4)
        self.assertFalse(
            any(
                "dst_ipv4" in entry["key"] or "target_ip" in entry["key"]
                for entry in compiled["core_lfib_map"]
            )
        )
        self.assertFalse(compiled["strict_contract"]["linux_gz_fallback_enabled"])
        self.assertFalse(
            compiled["strict_contract"]["intermediate_core_lookup_uses_dst_ip"]
        )

    def test_future_mpls_only_pre_attach_is_not_semantically_inert(self) -> None:
        audit = assess_future_pre_attached_interface_inertness(
            bpf_attached=True,
            qdisc_clsact_attached=True,
            interface_roles=["mpls"],
            active_generation_entries_present=False,
            linux_bridge_forwarding=False,
            linux_ip_route=False,
            linux_mpls_route=False,
        )

        self.assertFalse(audit["safe"])
        self.assertIn(
            "tc_bpf_non_gz_ip_pass_allows_unclassified_ipv4",
            audit["blockers"],
        )
        self.assertEqual(
            audit["expected_counter_delta"],
            {"non_gz_ip_pass": 1, "pass": 1, "miss": 0, "drop": 0},
        )
        self.assertFalse(audit["explicit_inactive_drop_evidence"])

    def test_future_classifier_pre_attach_drops_before_activation(self) -> None:
        audit = assess_future_pre_attached_interface_inertness(
            bpf_attached=True,
            qdisc_clsact_attached=True,
            interface_roles=["classifier"],
            active_generation_entries_present=False,
            classifier_key_present_for_active_generation=False,
            linux_bridge_forwarding=False,
            linux_ip_route=False,
            linux_mpls_route=False,
        )

        self.assertTrue(audit["safe"])
        self.assertTrue(audit["explicit_inactive_drop_evidence"])
        self.assertEqual(audit["expected_counter_delta"], {"miss": 1, "drop": 1})
        self.assertEqual(audit["linux_gz_fallback_command_count"], 0)
        self.assertEqual(audit["eligible_core_fallback_to_linux"], 0)
        self.assertTrue(audit["active_generation_flip_required"])

    def test_active_generation_flip_is_required_for_future_core_connectivity(
        self,
    ) -> None:
        current_generation = 4
        future_generation = 5
        compiled = compile_bpf_owned_mpls_maps(
            [_context()],
            generation=current_generation,
        )
        future_maps = compile_bpf_owned_mpls_maps(
            [_context(label_offset=120, target_ip="10.154.42.229")],
            generation=future_generation,
        )
        compiled["core_lfib_map"].extend(future_maps["core_lfib_map"])
        future_ingress_ifindex = logical_ifindex("stl-b", "ab0")

        before_flip = simulate_core_lfib_lookup(
            compiled,
            generation=current_generation,
            ingress_ifindex=future_ingress_ifindex,
            label=1120,
            direction=G_TO_Z,
        )
        after_flip = simulate_core_lfib_lookup(
            compiled,
            generation=future_generation,
            ingress_ifindex=future_ingress_ifindex,
            label=1120,
            direction=G_TO_Z,
        )

        self.assertFalse(before_flip["ok"])
        self.assertEqual(
            before_flip["reason"],
            "core_lfib_miss_strict_no_linux_fallback",
        )
        self.assertFalse(before_flip["linux_fallback"])
        self.assertTrue(after_flip["ok"])
        self.assertEqual(after_flip["out_label"], 1121)

    def test_linux_or_bridge_bypass_blocks_future_pre_attach_proof(self) -> None:
        audit = assess_future_pre_attached_interface_inertness(
            bpf_attached=True,
            qdisc_clsact_attached=True,
            interface_roles=["classifier"],
            active_generation_entries_present=False,
            linux_bridge_forwarding=True,
            linux_ip_route=True,
            linux_mpls_route=True,
        )

        self.assertFalse(audit["safe"])
        self.assertIn("linux_bridge_l2_bypass_present", audit["blockers"])
        self.assertIn("linux_ip_route_fallback_present", audit["blockers"])
        self.assertIn("linux_mpls_route_fallback_present", audit["blockers"])
        self.assertGreater(audit["linux_gz_fallback_command_count"], 0)
        self.assertGreater(audit["eligible_core_fallback_to_linux"], 0)

    def test_one_side_unconnected_future_interface_is_physically_invisible(
        self,
    ) -> None:
        audit = audit_future_interface_physical_visibility(
            interface_ref="netns:stl-940120:xp120a",
            netns="stl-940120",
            dev="xp120a",
            interface_exists=True,
            link_up=True,
            attached_to_bridge=False,
            peer_exists=False,
            peer_link_up=False,
            peer_attached_to_reachable_path=False,
            active_generation_entry_present=False,
        )

        self.assertEqual(
            audit["early_attach_classification"],
            "EARLY_ATTACH_SAFE_PHYSICAL_INVISIBLE",
        )
        self.assertIn("peer_endpoint_absent", audit["invisibility_enforced_by"])
        self.assertIn("no_active_generation_entry", audit["invisibility_enforced_by"])
        self.assertFalse(audit["physically_visible_after_tc_act_ok"])
        self.assertFalse(audit["bpf_authorized_forwarding"])

    def test_bridge_peer_link_up_path_is_visible_risk(self) -> None:
        audit = audit_future_interface_physical_visibility(
            interface_ref="netns:stl-940120:xp120a",
            netns="stl-940120",
            dev="xp120a",
            interface_exists=True,
            link_up=True,
            attached_to_bridge=True,
            bridge_name="p003p004-stl-940120-stl-940178-cross-slot0",
            peer_exists=True,
            peer_link_up=True,
            peer_attached_to_reachable_path=True,
            peer_netns="stl-940178",
            peer_dev="xp120b",
            active_generation_entry_present=False,
        )

        self.assertEqual(
            audit["early_attach_classification"],
            "EARLY_ATTACH_UNSAFE_PHYSICAL_VISIBLE",
        )
        self.assertTrue(audit["bridge_can_forward_if_tc_bpf_returns_ok"])
        self.assertIn(
            "bridge_peer_link_up_path_visible_after_tc_act_ok",
            audit["blockers"],
        )
        self.assertEqual(
            audit["packet_after_tc_act_ok"],
            "linux_bridge_or_peer_reachable_path",
        )

    def test_linux_ip_or_mpls_fallback_blocks_physical_visibility_proof(
        self,
    ) -> None:
        audit = audit_future_interface_physical_visibility(
            interface_ref="netns:stl-940178:xp120b",
            interface_exists=True,
            link_up=False,
            linux_ip_routes_present=True,
            linux_mpls_routes_present=True,
            active_generation_entry_present=False,
        )

        self.assertEqual(
            audit["early_attach_classification"],
            "EARLY_ATTACH_UNSAFE_PHYSICAL_VISIBLE",
        )
        self.assertIn("linux_ip_route_fallback_present", audit["blockers"])
        self.assertIn("linux_mpls_route_fallback_present", audit["blockers"])

    def test_absent_active_generation_means_no_bpf_authorized_forwarding(
        self,
    ) -> None:
        audit = audit_future_interface_physical_visibility(
            interface_ref="netns:stl-940120:xp120a",
            interface_exists=False,
            active_generation_entry_present=False,
        )

        self.assertEqual(
            audit["early_attach_classification"],
            "EARLY_ATTACH_SAFE_PHYSICAL_INVISIBLE",
        )
        self.assertIn("interface_absent", audit["invisibility_enforced_by"])
        self.assertEqual(audit["packet_after_tc_act_ok"], "no_device_no_ingress_path")
        self.assertFalse(audit["bpf_authorized_forwarding"])
        self.assertFalse(audit["bpf_attach_alone_changes_visibility"])


if __name__ == "__main__":
    unittest.main()
