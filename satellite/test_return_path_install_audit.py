#!/usr/bin/env python3
# encoding: utf-8

import unittest

from return_path_audit import _return_path_migration_blockers
from return_path_install_audit import (
    render_return_path_migration_worklist_summary,
    render_return_path_install_points_summary,
    summarize_return_path_migration_worklist,
    summarize_return_path_install_points,
)


class ReturnPathInstallAuditTests(unittest.TestCase):
    def test_install_point_audit_covers_return_path_migration_blockers(self) -> None:
        blocker_report = _return_path_migration_blockers(
            {
                "gateway_ut32_return_route_count": 1,
                "reverse_label_count": 2,
                "reverse_transit_lfib_entry_count": 1,
                "serving_satellite_ut32_delivery_route_count": 1,
                "gateway_nat_forward_rule_count": 3,
            }
        )
        expected_blocker_ids = {
            str(blocker["blocker_id"])
            for blocker in blocker_report
            if blocker.get("present")
        }

        report = summarize_return_path_install_points()
        install_blocker_ids = {
            str(blocker["blocker_id"])
            for blocker in report["blockers"]
        }

        self.assertEqual(install_blocker_ids, expected_blocker_ids)
        self.assertEqual(
            report["current_linux_realization"],
            "per_ut_flow_compatibility",
        )
        self.assertEqual(
            report["target_backbone_realization"],
            "shared_gateway_zone_or_zone_return_reachability",
        )
        self.assertEqual(report["backbone_blocker_count"], 3)
        self.assertGreater(report["install_point_count"], report["blocker_count"])

    def test_install_point_audit_keeps_backbone_and_edge_categories_separate(self) -> None:
        report = summarize_return_path_install_points()
        rendered = render_return_path_install_points_summary(report)

        self.assertEqual(
            report["category_totals"]["backbone_return"]["blocker_ids"],
            [
                "gateway_ut32_return_route",
                "reverse_lfib_label_stack",
                "reverse_transit_lfib_entry",
            ],
        )
        self.assertEqual(
            report["category_totals"]["edge_local_delivery"]["blocker_ids"],
            ["serving_satellite_ut32_delivery_route"],
        )
        self.assertEqual(
            report["category_totals"]["gateway_edge_policy"]["blocker_ids"],
            ["gateway_nat_forward_rules"],
        )
        self.assertIn("blocker=gateway_ut32_return_route", rendered)
        self.assertIn("target_scope=gateway_zone_or_zone_return_target", rendered)
        self.assertIn("blocker=gateway_nat_forward_rules", rendered)

    def test_migration_worklist_combines_artifact_counts_with_install_points(self) -> None:
        report = summarize_return_path_migration_worklist(
            {
                "snapshot_count": 3,
                "total_flow_count": 36,
                "total_current_backbone_return_state_item_count": 72,
                "total_target_shared_backbone_return_state_item_count": 18,
                "current_to_target_backbone_state_ratio": 4.0,
                "shared_backbone_return_reachability_preview": {
                    "candidate_shared_zone_route_count": 18,
                    "candidate_zone_route_coverage_ratio": 0.0,
                    "candidate_zone_route_complete": False,
                    "candidate_shared_label_stack_count": 18,
                    "candidate_metadata_coverage_ratio": 0.0,
                    "candidate_metadata_complete": False,
                },
                "return_path_migration_blockers": [
                    {
                        "blocker_id": "gateway_nat_forward_rules",
                        "migration_category": "gateway_edge_policy",
                        "present": True,
                        "total_current_item_count": 108,
                        "max_current_item_count_per_snapshot": 36,
                        "snapshot_count_present": 3,
                    },
                    {
                        "blocker_id": "gateway_ut32_return_route",
                        "migration_category": "backbone_return",
                        "present": True,
                        "total_current_item_count": 36,
                        "max_current_item_count_per_snapshot": 12,
                        "snapshot_count_present": 3,
                    },
                    {
                        "blocker_id": "reverse_lfib_label_stack",
                        "migration_category": "backbone_return",
                        "present": True,
                        "total_current_item_count": 36,
                        "max_current_item_count_per_snapshot": 12,
                        "snapshot_count_present": 3,
                    },
                ],
                "selected_runtime_scope": {
                    "snapshot_count": 2,
                    "total_flow_count": 24,
                    "total_current_backbone_return_state_item_count": 48,
                    "total_target_shared_backbone_return_state_item_count": 12,
                    "current_to_target_backbone_state_ratio": 4.0,
                    "shared_backbone_return_reachability_preview": {
                        "candidate_shared_zone_route_count": 12,
                        "candidate_zone_route_coverage_ratio": 0.0,
                        "candidate_zone_route_complete": False,
                        "candidate_shared_label_stack_count": 12,
                        "candidate_metadata_coverage_ratio": 0.0,
                        "candidate_metadata_complete": False,
                    },
                    "return_path_migration_blockers": [
                        {
                            "blocker_id": "gateway_nat_forward_rules",
                            "migration_category": "gateway_edge_policy",
                            "present": True,
                            "total_current_item_count": 72,
                        },
                        {
                            "blocker_id": "gateway_ut32_return_route",
                            "migration_category": "backbone_return",
                            "present": True,
                            "total_current_item_count": 24,
                        },
                        {
                            "blocker_id": "reverse_lfib_label_stack",
                            "migration_category": "backbone_return",
                            "present": True,
                            "total_current_item_count": 24,
                        },
                    ],
                },
            }
        )
        rendered = render_return_path_migration_worklist_summary(report)

        self.assertEqual(report["scope_count"], 2)
        full_scope = report["scopes"][0]
        selected_scope = report["scopes"][1]
        self.assertEqual(full_scope["scope_name"], "full")
        self.assertEqual(selected_scope["scope_name"], "selected_runtime")
        self.assertEqual(
            full_scope["top_backbone_work_item"],
            "reverse_lfib_label_stack",
        )
        self.assertEqual(
            selected_scope["top_backbone_work_item"],
            "reverse_lfib_label_stack",
        )
        self.assertEqual(
            full_scope["work_items"][0]["migration_category"],
            "backbone_return",
        )
        self.assertEqual(
            full_scope["work_items"][0]["install_point_count"],
            4,
        )
        self.assertFalse(full_scope["metadata_boundary_ready_for_backbone_migration"])
        self.assertEqual(full_scope["candidate_shared_zone_route_count"], 18)
        self.assertEqual(full_scope["candidate_zone_route_coverage_ratio"], 0.0)
        self.assertEqual(
            full_scope["next_backbone_migration_gate"],
            "complete_planner_candidate_zone_route_and_label_metadata_first",
        )
        self.assertIn("scope=selected_runtime flows=24", rendered)
        self.assertIn("candidate_zone_route_complete=False", rendered)
        self.assertIn(
            "next_gate=complete_planner_candidate_zone_route_and_label_metadata_first",
            rendered,
        )
        self.assertIn("blocker=reverse_lfib_label_stack", rendered)
        self.assertIn("blocker=gateway_nat_forward_rules", rendered)

    def test_migration_worklist_marks_metadata_ready_but_install_blockers_remaining(self) -> None:
        report = summarize_return_path_migration_worklist(
            {
                "snapshot_count": 1,
                "total_flow_count": 2,
                "total_current_backbone_return_state_item_count": 4,
                "total_target_shared_backbone_return_state_item_count": 2,
                "current_to_target_backbone_state_ratio": 2.0,
                "shared_backbone_return_reachability_preview": {
                    "candidate_shared_zone_route_count": 2,
                    "candidate_zone_route_coverage_ratio": 1.0,
                    "candidate_zone_route_complete": True,
                    "candidate_shared_label_stack_count": 2,
                    "candidate_metadata_coverage_ratio": 1.0,
                    "candidate_metadata_complete": True,
                },
                "return_path_migration_blockers": [
                    {
                        "blocker_id": "gateway_ut32_return_route",
                        "migration_category": "backbone_return",
                        "present": True,
                        "total_current_item_count": 2,
                    },
                    {
                        "blocker_id": "reverse_lfib_label_stack",
                        "migration_category": "backbone_return",
                        "present": True,
                        "total_current_item_count": 2,
                    },
                ],
            }
        )
        rendered = render_return_path_migration_worklist_summary(report)
        full_scope = report["scopes"][0]

        self.assertTrue(full_scope["metadata_boundary_ready_for_backbone_migration"])
        self.assertEqual(full_scope["candidate_shared_zone_route_count"], 2)
        self.assertEqual(full_scope["candidate_zone_route_coverage_ratio"], 1.0)
        self.assertEqual(
            full_scope["next_backbone_migration_gate"],
            "replace_current_backbone_install_points_with_shared_zone_return_installation",
        )
        self.assertIn("candidate_zone_route_complete=True", rendered)
        self.assertIn(
            "next_gate=replace_current_backbone_install_points_with_shared_zone_return_installation",
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
