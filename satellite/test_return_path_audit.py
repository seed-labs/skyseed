#!/usr/bin/env python3
# encoding: utf-8

import unittest

from return_path_audit import (
    audit_flow_contexts,
    audit_planner_preview,
    render_return_path_audit_summary,
)


def _context(
    *,
    flow_id: str,
    ut_id: str,
    zone_id: str = "zone-a",
    gateway_id: str = "gw1",
    serving_satellite_id: str = "stl-950001",
    reverse_labels: list[int] | None = None,
    shared_backbone_return_target: dict | None = None,
    candidate_shared_zone_return_route: dict | None = None,
    candidate_shared_return_label_stack: dict | None = None,
    current_linux_realization_mode: str = "per_ut_flow_compatibility",
) -> dict:
    return_path_intent = {
        "intent_kind": "zone_return_delivery",
        "zone_id": zone_id,
        "ut_id": ut_id,
        "selected_gateway_id": gateway_id,
        "zone_serving_satellite_id": serving_satellite_id,
        "current_linux_realization": {
            "mode": current_linux_realization_mode,
        },
    }
    if shared_backbone_return_target is not None:
        return_path_intent["shared_backbone_return_target"] = dict(
            shared_backbone_return_target
        )
    if candidate_shared_return_label_stack is not None:
        return_path_intent["candidate_shared_return_label_stack"] = dict(
            candidate_shared_return_label_stack
        )
    if candidate_shared_zone_return_route is not None:
        return_path_intent["candidate_shared_zone_return_route"] = dict(
            candidate_shared_zone_return_route
        )
    return {
        "flow_id": flow_id,
        "ut_id": ut_id,
        "zone_id": zone_id,
        "selected_gateway_id": gateway_id,
        "selected_gateway_satellite_id": "stl-950010",
        "serving_satellite_id": serving_satellite_id,
        "gateway_class_id": f"{zone_id}:default-gateway:{gateway_id}",
        "reverse_labels": list(reverse_labels if reverse_labels is not None else [200000]),
        "return_path_intent": return_path_intent,
    }


class ReturnPathAuditTests(unittest.TestCase):
    def test_audit_marks_per_flow_return_state_scale_gap(self) -> None:
        report = audit_flow_contexts(
            [
                _context(flow_id="flow-a", ut_id="ut101", reverse_labels=[200000, 200001]),
                _context(flow_id="flow-b", ut_id="ut102", reverse_labels=[200000, 200001]),
            ],
            scope_id="test-scope",
        )

        self.assertEqual(report["flow_count"], 2)
        self.assertEqual(report["ut_count"], 2)
        self.assertEqual(report["shared_return_targets"]["gateway_zone_return_target_count"], 1)
        self.assertEqual(report["per_flow_return_state"]["gateway_ut32_return_route_count"], 2)
        self.assertEqual(report["per_flow_return_state"]["reverse_label_count"], 4)
        self.assertEqual(report["per_flow_return_state"]["reverse_transit_lfib_entry_count"], 2)
        self.assertEqual(report["per_flow_return_state"]["gateway_nat_forward_rule_count"], 6)
        blockers = {
            blocker["blocker_id"]: blocker
            for blocker in report["return_path_migration_blockers"]
        }
        self.assertEqual(
            sorted(blockers),
            [
                "gateway_nat_forward_rules",
                "gateway_ut32_return_route",
                "reverse_lfib_label_stack",
                "reverse_transit_lfib_entry",
                "serving_satellite_ut32_delivery_route",
            ],
        )
        self.assertEqual(
            blockers["gateway_ut32_return_route"]["target_scope"],
            "gateway_zone_or_zone_return_target",
        )
        self.assertEqual(
            blockers["gateway_ut32_return_route"]["migration_category"],
            "backbone_return",
        )
        self.assertEqual(
            blockers["reverse_lfib_label_stack"]["current_item_count"],
            4,
        )
        self.assertEqual(
            blockers["serving_satellite_ut32_delivery_route"]["target_scope"],
            "local_ut_delivery",
        )
        self.assertTrue(blockers["gateway_nat_forward_rules"]["present"])
        self.assertEqual(
            report["return_state_scale_model"][
                "current_backbone_return_state_item_count"
            ],
            8,
        )
        self.assertEqual(
            report["return_state_scale_model"][
                "target_shared_backbone_return_state_item_count"
            ],
            1,
        )
        self.assertTrue(
            report["return_state_scale_model"]["backbone_state_reduction_available"]
        )
        self.assertEqual(
            report["return_state_scale_model"][
                "current_to_target_backbone_state_ratio"
            ],
            8.0,
        )
        projection_10000 = report["return_state_scale_model"]["scale_projection"][
            "projections"
        ]["10000"]
        self.assertEqual(
            projection_10000["current_per_flow_compatibility_backbone_items"],
            40000,
        )
        self.assertEqual(
            projection_10000["target_shared_backbone_items_if_target_count_constant"],
            1,
        )
        self.assertEqual(
            projection_10000[
                "target_shared_backbone_items_if_observed_target_ratio_scales"
            ],
            5000,
        )
        self.assertEqual(projection_10000["current_to_constant_target_ratio"], 40000.0)
        self.assertEqual(projection_10000["current_to_ratio_scaled_target_ratio"], 8.0)
        self.assertTrue(report["scale_gap"]["current_per_flow_return_state_present"])
        self.assertTrue(report["scale_gap"]["current_return_state_scales_with_flow_count"])
        self.assertTrue(report["scale_gap"]["shared_target_reduction_available"])
        self.assertEqual(report["scale_gap"]["flow_to_gateway_zone_return_target_ratio"], 2.0)
        reachability = report["shared_backbone_return_reachability_preview"]
        self.assertEqual(
            reachability["target_kind"],
            "gateway_zone_return_reachability",
        )
        self.assertEqual(reachability["target_group_count"], 1)
        self.assertEqual(reachability["current_model"], "per_flow_reverse_lfib")
        self.assertEqual(reachability["current_reverse_label_count"], 4)
        self.assertEqual(reachability["legacy_compatibility_reverse_label_count"], 4)
        self.assertEqual(reachability["actual_shared_reverse_lfib_entry_count"], 0)
        self.assertEqual(reachability["candidate_shared_zone_route_count"], 1)
        self.assertEqual(reachability["explicit_candidate_zone_route_flow_count"], 0)
        self.assertEqual(reachability["fallback_candidate_zone_route_flow_count"], 2)
        self.assertEqual(reachability["candidate_zone_route_coverage_ratio"], 0.0)
        self.assertFalse(reachability["candidate_zone_route_complete"])
        self.assertEqual(reachability["candidate_shared_label_stack_count"], 1)
        self.assertEqual(reachability["explicit_candidate_metadata_flow_count"], 0)
        self.assertEqual(
            reachability["fallback_reverse_path_signature_flow_count"],
            2,
        )
        self.assertEqual(reachability["candidate_metadata_coverage_ratio"], 0.0)
        self.assertFalse(reachability["candidate_metadata_complete"])
        self.assertTrue(reachability["reverse_label_reduction_available"])
        self.assertEqual(reachability["current_to_candidate_label_stack_ratio"], 4.0)
        self.assertEqual(reachability["target_groups"][0]["flow_count"], 2)
        self.assertEqual(
            reachability["target_groups"][0][
                "fallback_reverse_path_signature_flow_count"
            ],
            2,
        )
        self.assertEqual(
            reachability["target_groups"][0]["unique_reverse_path_count"],
            1,
        )

    def test_shared_gz_backbone_realization_removes_backbone_blockers(self) -> None:
        report = audit_flow_contexts(
            [
                _context(
                    flow_id="flow-a",
                    ut_id="ut101",
                    reverse_labels=[200000, 200001],
                    candidate_shared_zone_return_route={
                        "target_key": "gw1:zone-a:stl-950001:zone-route",
                    },
                    candidate_shared_return_label_stack={
                        "target_key": "gw1:zone-a:stl-950001:reverse-path:r1-r2",
                    },
                    current_linux_realization_mode="shared_gz_backbone",
                ),
                _context(
                    flow_id="flow-b",
                    ut_id="ut102",
                    reverse_labels=[200000, 200001],
                    candidate_shared_zone_return_route={
                        "target_key": "gw1:zone-a:stl-950001:zone-route",
                    },
                    candidate_shared_return_label_stack={
                        "target_key": "gw1:zone-a:stl-950001:reverse-path:r1-r2",
                    },
                    current_linux_realization_mode="shared_gz_backbone",
                ),
            ],
            scope_id="shared-gz",
        )

        self.assertTrue(report["shared_gz_backbone_active"])
        self.assertFalse(report["scale_gap"]["current_return_state_scales_with_flow_count"])
        self.assertEqual(report["scale_gap"]["current_backbone_state"], "shared_gz_backbone")
        self.assertEqual(report["per_flow_return_state"]["gateway_ut32_return_route_count"], 0)
        self.assertEqual(report["per_flow_return_state"]["reverse_label_count"], 0)
        self.assertEqual(report["per_flow_return_state"]["reverse_transit_lfib_entry_count"], 0)
        self.assertEqual(
            report["legacy_compatibility_projection"]["gateway_ut32_return_route_count"],
            2,
        )
        self.assertEqual(
            report["return_state_scale_model"]["current_backbone_return_state_item_count"],
            1,
        )
        reachability = report["shared_backbone_return_reachability_preview"]
        self.assertEqual(reachability["current_model"], "shared_gz_backbone")
        self.assertEqual(reachability["current_reverse_label_count"], 0)
        self.assertEqual(reachability["legacy_compatibility_reverse_label_count"], 4)
        self.assertEqual(reachability["actual_shared_reverse_lfib_entry_count"], 1)
        self.assertEqual(
            reachability["target_groups"][0]["current_reverse_label_count"],
            0,
        )
        self.assertEqual(
            reachability["target_groups"][0][
                "legacy_compatibility_reverse_label_count"
            ],
            4,
        )
        self.assertFalse(
            report["return_state_scale_model"]["current_backbone_state_scales_with_flow_count"]
        )
        blockers = {
            blocker["blocker_id"]: blocker
            for blocker in report["return_path_migration_blockers"]
        }
        self.assertFalse(blockers["gateway_ut32_return_route"]["present"])
        self.assertFalse(blockers["reverse_lfib_label_stack"]["present"])
        self.assertFalse(blockers["reverse_transit_lfib_entry"]["present"])

    def test_explicit_candidate_metadata_coverage_is_reported(self) -> None:
        candidate_stack = {
            "target_kind": "gateway_zone_return_label_stack_candidate",
            "target_key": "gw1:zone-a:stl-950001:reverse-path:r1-r2",
            "shared_backbone_return_target_key": "gw1:zone-a:stl-950001",
            "reverse_path_signature": "r1:r2",
            "state_owner": "satellite_backbone",
            "state_model": "candidate_shared_gateway_zone_return_label_stack",
            "install_behavior": "not_installed_current_linux_compatibility_metadata_only",
        }
        report = audit_flow_contexts(
            [
                _context(
                    flow_id="flow-a",
                    ut_id="ut101",
                    reverse_labels=[200000, 200001],
                    candidate_shared_zone_return_route={
                        "target_key": "gw1:zone-a:stl-950001:zone-route",
                    },
                    candidate_shared_return_label_stack=candidate_stack,
                ),
                _context(
                    flow_id="flow-b",
                    ut_id="ut102",
                    reverse_labels=[200000, 200001],
                    candidate_shared_zone_return_route={
                        "target_key": "gw1:zone-a:stl-950001:zone-route",
                    },
                    candidate_shared_return_label_stack=candidate_stack,
                ),
            ],
            scope_id="explicit-candidate-metadata",
        )

        reachability = report["shared_backbone_return_reachability_preview"]
        self.assertEqual(reachability["candidate_shared_zone_route_count"], 1)
        self.assertEqual(reachability["explicit_candidate_zone_route_flow_count"], 2)
        self.assertEqual(reachability["fallback_candidate_zone_route_flow_count"], 0)
        self.assertEqual(reachability["candidate_zone_route_coverage_ratio"], 1.0)
        self.assertTrue(reachability["candidate_zone_route_complete"])
        self.assertEqual(reachability["candidate_shared_label_stack_count"], 1)
        self.assertEqual(reachability["explicit_candidate_metadata_flow_count"], 2)
        self.assertEqual(
            reachability["fallback_reverse_path_signature_flow_count"],
            0,
        )
        self.assertEqual(reachability["candidate_metadata_coverage_ratio"], 1.0)
        self.assertTrue(reachability["candidate_metadata_complete"])
        self.assertEqual(
            reachability["target_groups"][0]["candidate_metadata_coverage_ratio"],
            1.0,
        )

    def test_planner_preview_audit_summarizes_snapshots(self) -> None:
        preview = {
            "snapshots": [
                {
                    "slice_id": "slice-0001",
                    "runtime_update_plan": {
                        "node_subplans": {
                            "gw1": {
                                "actions": [
                                    {
                                        "intent": {
                                            "outcome": {
                                                "flow_context": _context(
                                                    flow_id="flow-a",
                                                    ut_id="ut101",
                                                )
                                            }
                                        }
                                    }
                                ]
                            }
                        }
                    },
                }
            ]
        }

        report = audit_planner_preview(preview, selected_scope_ids=["slice-0001"])

        self.assertEqual(report["snapshot_count"], 1)
        self.assertEqual(report["total_flow_count"], 1)
        self.assertEqual(report["total_return_path_intent_count"], 1)
        self.assertTrue(report["current_return_state_scales_with_flow_count"])
        self.assertFalse(report["shared_target_reduction_available"])
        self.assertEqual(report["snapshot_reports"][0]["scope_id"], "slice-0001")
        self.assertEqual(report["return_path_migration_blocker_count"], 4)
        self.assertEqual(
            report["return_path_migration_blocker_ids"],
            [
                "gateway_nat_forward_rules",
                "gateway_ut32_return_route",
                "reverse_lfib_label_stack",
                "serving_satellite_ut32_delivery_route",
            ],
        )
        aggregate_blockers = {
            blocker["blocker_id"]: blocker
            for blocker in report["return_path_migration_blockers"]
        }
        self.assertEqual(
            aggregate_blockers["gateway_nat_forward_rules"][
                "total_current_item_count"
            ],
            3,
        )
        self.assertEqual(
            aggregate_blockers["gateway_nat_forward_rules"][
                "max_current_item_count_per_snapshot"
            ],
            3,
        )
        self.assertEqual(
            aggregate_blockers["gateway_nat_forward_rules"][
                "snapshot_count_present"
            ],
            1,
        )
        self.assertEqual(
            report["return_path_migration_category_totals"]["backbone_return"][
                "total_current_item_count"
            ],
            2,
        )
        self.assertEqual(
            report["return_path_migration_category_totals"]["gateway_edge_policy"][
                "total_current_item_count"
            ],
            3,
        )
        readiness = report["return_path_migration_readiness"]
        self.assertFalse(readiness["shared_backbone_return_ready"])
        self.assertTrue(readiness["backbone_return_migration_required"])
        self.assertTrue(readiness["current_backbone_state_scales_with_flow_count"])
        self.assertTrue(readiness["target_backbone_state_independent_of_ut_count"])
        self.assertEqual(
            readiness["current_realization"],
            "per_ut_flow_compatibility",
        )
        self.assertEqual(
            readiness["target_realization"],
            "shared_gateway_zone_or_zone_return_reachability",
        )
        self.assertEqual(readiness["backbone_return_blocker_item_count"], 2)
        self.assertEqual(readiness["gateway_edge_policy_item_count"], 3)
        self.assertEqual(readiness["edge_local_delivery_item_count"], 1)
        self.assertEqual(
            readiness["backbone_return_blocker_ids"],
            ["gateway_ut32_return_route", "reverse_lfib_label_stack"],
        )
        self.assertTrue(readiness["non_backbone_items_are_separated"])
        self.assertEqual(report["selected_runtime_scope"]["snapshot_count"], 1)
        self.assertEqual(report["selected_runtime_scope"]["total_flow_count"], 1)
        self.assertEqual(report["selected_runtime_scope"]["scope_ids"], ["slice-0001"])
        self.assertEqual(
            report["selected_runtime_scope"][
                "total_current_backbone_return_state_item_count"
            ],
            2,
        )
        self.assertEqual(
            report["selected_runtime_scope"][
                "total_target_shared_backbone_return_state_item_count"
            ],
            1,
        )
        self.assertEqual(
            report["selected_runtime_scope"]["scale_projection"]["projections"]["10000"][
                "current_per_flow_compatibility_backbone_items"
            ],
            20000,
        )
        summary = render_return_path_audit_summary(report)
        self.assertIn("projection_10000_current=20000", summary)
        self.assertIn("projection_10000_ratio_scaled=2.0", summary)
        self.assertIn("shared_backbone_ready=False", summary)
        self.assertIn("backbone_migration_required=True", summary)
        self.assertIn("backbone_blocker_items=2", summary)
        self.assertIn("migration_blockers=4", summary)
        self.assertIn("backbone_return_items=2", summary)
        self.assertIn("gateway_edge_policy_items=3", summary)
        self.assertIn("edge_local_delivery_items=1", summary)
        self.assertIn("top_migration_blocker=gateway_nat_forward_rules:3", summary)
        self.assertIn(
            "top_backbone_return_migration_blocker=reverse_lfib_label_stack:1",
            summary,
        )
        self.assertIn("shared_candidate_label_stacks=1", summary)
        self.assertIn("shared_label_ratio=1.0", summary)
        self.assertIn("shared_candidate_zone_routes=1", summary)
        self.assertIn("candidate_zone_route_coverage=0.0", summary)
        self.assertIn("candidate_metadata_coverage=0.0", summary)
        self.assertIn("candidate_metadata_complete=False", summary)
        self.assertIn("fallback_reverse_path_signature_flows=1", summary)

    def test_planner_preview_audit_reports_shared_gz_readiness(self) -> None:
        preview = {
            "snapshots": [
                {
                    "slice_id": "slice-0001",
                    "runtime_update_plan": {
                        "node_subplans": {
                            "gw1": {
                                "actions": [
                                    {
                                        "intent": {
                                            "outcome": {
                                                "flow_context": _context(
                                                    flow_id="flow-a",
                                                    ut_id="ut101",
                                                    reverse_labels=[200000, 200001],
                                                    candidate_shared_zone_return_route={
                                                        "target_key": (
                                                            "gw1:zone-a:stl-950001:zone-route"
                                                        ),
                                                    },
                                                    candidate_shared_return_label_stack={
                                                        "target_key": (
                                                            "gw1:zone-a:stl-950001:"
                                                            "reverse-path:r1-r2"
                                                        ),
                                                    },
                                                    current_linux_realization_mode=(
                                                        "shared_gz_backbone"
                                                    ),
                                                )
                                            }
                                        }
                                    },
                                    {
                                        "intent": {
                                            "outcome": {
                                                "flow_context": _context(
                                                    flow_id="flow-b",
                                                    ut_id="ut102",
                                                    reverse_labels=[200000, 200001],
                                                    candidate_shared_zone_return_route={
                                                        "target_key": (
                                                            "gw1:zone-a:stl-950001:zone-route"
                                                        ),
                                                    },
                                                    candidate_shared_return_label_stack={
                                                        "target_key": (
                                                            "gw1:zone-a:stl-950001:"
                                                            "reverse-path:r1-r2"
                                                        ),
                                                    },
                                                    current_linux_realization_mode=(
                                                        "shared_gz_backbone"
                                                    ),
                                                )
                                            }
                                        }
                                    },
                                ]
                            }
                        }
                    },
                }
            ]
        }

        report = audit_planner_preview(preview, selected_scope_ids=["slice-0001"])

        self.assertFalse(report["current_return_state_scales_with_flow_count"])
        self.assertEqual(report["total_current_backbone_return_state_item_count"], 1)
        self.assertEqual(report["total_target_shared_backbone_return_state_item_count"], 1)
        self.assertEqual(report["return_path_migration_blocker_count"], 2)
        reachability = report["shared_backbone_return_reachability_preview"]
        self.assertEqual(reachability["current_model"], "shared_gz_backbone")
        self.assertEqual(reachability["current_reverse_label_count"], 0)
        self.assertEqual(reachability["legacy_compatibility_reverse_label_count"], 4)
        self.assertEqual(reachability["actual_shared_reverse_lfib_entry_count"], 1)
        self.assertNotIn(
            "backbone_return",
            report["return_path_migration_category_totals"],
        )
        readiness = report["return_path_migration_readiness"]
        self.assertTrue(readiness["shared_backbone_return_ready"])
        self.assertFalse(readiness["backbone_return_migration_required"])
        self.assertFalse(readiness["current_backbone_state_scales_with_flow_count"])
        self.assertEqual(readiness["current_realization"], "shared_gz_backbone")
        self.assertEqual(readiness["backbone_return_blocker_item_count"], 0)

    def test_selected_runtime_scope_can_be_smaller_than_all_snapshots(self) -> None:
        preview = {
            "snapshots": [
                {
                    "slice_id": "runtime-slice",
                    "runtime_update_plan": {
                        "node_subplans": {
                            "gw1": {
                                "actions": [
                                    {
                                        "intent": {
                                            "outcome": {
                                                "flow_context": _context(
                                                    flow_id="flow-a",
                                                    ut_id="ut101",
                                                )
                                            }
                                        }
                                    }
                                ]
                            }
                        }
                    },
                },
                {
                    "slice_id": "parameter-only-slice",
                    "runtime_update_plan": {
                        "node_subplans": {
                            "gw1": {
                                "actions": [
                                    {
                                        "intent": {
                                            "outcome": {
                                                "flow_context": _context(
                                                    flow_id="flow-b",
                                                    ut_id="ut102",
                                                )
                                            }
                                        }
                                    }
                                ]
                            }
                        }
                    },
                },
            ]
        }

        report = audit_planner_preview(preview, selected_scope_ids=["runtime-slice"])

        self.assertEqual(report["snapshot_count"], 2)
        self.assertEqual(report["total_flow_count"], 2)
        self.assertEqual(report["selected_runtime_scope"]["snapshot_count"], 1)
        self.assertEqual(report["selected_runtime_scope"]["total_flow_count"], 1)
        self.assertEqual(report["selected_runtime_scope"]["scope_ids"], ["runtime-slice"])

    def test_one_zone_per_ut_still_reports_per_flow_state(self) -> None:
        report = audit_flow_contexts(
            [
                _context(flow_id="flow-a", ut_id="ut101", zone_id="zone-a"),
                _context(flow_id="flow-b", ut_id="ut102", zone_id="zone-b"),
            ],
            scope_id="one-zone-per-ut",
        )

        self.assertTrue(report["scale_gap"]["current_per_flow_return_state_present"])
        self.assertTrue(report["scale_gap"]["current_return_state_scales_with_flow_count"])
        self.assertFalse(report["scale_gap"]["shared_target_reduction_available"])
        self.assertEqual(report["scale_gap"]["flow_to_gateway_zone_return_target_ratio"], 1.0)
        self.assertTrue(
            report["return_state_scale_model"]["backbone_state_reduction_available"]
        )

    def test_explicit_shared_target_overrides_compatibility_flow_fields(self) -> None:
        shared_target = {
            "target_kind": "gateway_zone_return",
            "target_key": "gw-shared:zone-shared:stl-shared",
            "gateway_id": "gw-shared",
            "zone_id": "zone-shared",
            "zone_serving_satellite_id": "stl-shared",
            "state_owner": "satellite_backbone",
            "state_model": "shared_gateway_zone_or_zone_return_reachability",
        }
        report = audit_flow_contexts(
            [
                _context(
                    flow_id="flow-a",
                    ut_id="ut101",
                    zone_id="legacy-zone-a",
                    gateway_id="legacy-gw-a",
                    serving_satellite_id="legacy-stl-a",
                    shared_backbone_return_target=shared_target,
                ),
                _context(
                    flow_id="flow-b",
                    ut_id="ut102",
                    zone_id="legacy-zone-b",
                    gateway_id="legacy-gw-b",
                    serving_satellite_id="legacy-stl-b",
                    shared_backbone_return_target=shared_target,
                ),
            ],
            scope_id="explicit-shared-target",
        )

        self.assertEqual(report["flow_count"], 2)
        self.assertEqual(
            report["shared_return_targets"]["zone_return_target_count"],
            1,
        )
        self.assertEqual(
            report["shared_return_targets"]["gateway_zone_return_target_count"],
            1,
        )
        self.assertEqual(
            report["return_state_scale_model"][
                "target_shared_backbone_return_state_item_count"
            ],
            1,
        )
        self.assertEqual(
            report["scale_gap"]["flow_to_gateway_zone_return_target_ratio"],
            2.0,
        )


if __name__ == "__main__":
    unittest.main()
