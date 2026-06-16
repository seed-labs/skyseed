#!/usr/bin/env python3
# encoding: utf-8

import unittest
from pathlib import Path
from unittest.mock import patch

from inspect_goal_evidence import (
    inspect_goal_evidence,
    render_goal_evidence_summary,
)


class GoalEvidenceInspectorTests(unittest.TestCase):
    def test_combines_goal_evidence_sections(self) -> None:
        distance_runs = [
            {
                "run_id": "near",
                "covered_runtime_ut_count": 2,
                "selected_ut_count": 2,
                "isl_hop_count_avg": 0.0,
                "isl_hop_count_max": 0,
                "runtime_update_action_count_avg": 6.0,
                "runtime_update_action_count_max": 6,
                "node_subplan_count_avg": 3.0,
                "node_subplan_count_max": 3,
                "selected_gateway_counts": {"gw1": 2},
            },
            {
                "run_id": "far",
                "covered_runtime_ut_count": 2,
                "selected_ut_count": 2,
                "isl_hop_count_avg": 2.5,
                "isl_hop_count_max": 5,
                "runtime_update_action_count_avg": 10.0,
                "runtime_update_action_count_max": 12,
                "node_subplan_count_avg": 5.0,
                "node_subplan_count_max": 7,
                "selected_gateway_counts": {"gw1": 1, "gw2": 1},
            },
        ]
        readiness = {
            "static_evidence_ready": True,
            "long_real_run_ready": False,
            "next_action": "run_host_prepare_then_recheck_preflight_before_real_run",
            "runtime_update_protection": {
                "ready": True,
                "default_validation_scope": "reapplied_gateway_class_sample",
                "test_coverage_present": True,
                "artifact_ping_validation_present": False,
                "artifact_incremental_reuse_present": True,
            },
            "return_path_migration": {
                "selected_runtime_candidate_shared_zone_route_count": 1,
                "selected_runtime_candidate_zone_route_coverage_ratio": 1.0,
                "selected_runtime_candidate_zone_route_complete": True,
            },
        }
        worklist = {
            "current_linux_realization": "per_ut_flow_compatibility",
            "target_backbone_realization": "shared_gateway_zone_or_zone_return_reachability",
            "source_file": "satellite/linux_mpls_runtime.py",
            "scopes": [
                {
                    "scope_name": "selected_runtime",
                    "total_flow_count": 2,
                    "total_current_backbone_return_state_item_count": 4,
                    "total_target_shared_backbone_return_state_item_count": 1,
                    "current_to_target_backbone_state_ratio": 4.0,
                    "top_backbone_work_item": "reverse_lfib_label_stack",
                    "work_items": [
                        {
                            "blocker_id": "reverse_lfib_label_stack",
                            "migration_category": "backbone_return",
                            "present_in_return_path_audit": True,
                        }
                    ],
                }
            ],
        }
        temporal = {
            "timing_analysis": {
                "initial_activation": {
                    "classification": "bootstrap_plus_initial_runtime_epoch",
                    "initial_activation_wall_seconds": 100.0,
                    "first_runtime_epoch_execution_after_prepare_reconcile_wall_seconds": 20.0,
                    "steady_runtime_excludes_initial_activation": True,
                },
                "steady_state_runtime_epochs": {
                    "excluded_initial_runtime_epoch_count": 1,
                    "count": 2,
                    "average_transition_wall_seconds": 5.0,
                    "average_node_local_apply_wall_seconds": 3.0,
                },
                "parameter_epochs": {
                    "count": 1,
                    "average_transition_wall_seconds": 1.0,
                },
            },
            "teardown_cleanup_summary": {"present": True},
        }

        audit = {
            "selected_scope_ids": ["slice-a"],
            "selected_runtime_scope": {
                "scale_projection": {
                    "projections": {
                        "10000": {
                            "current_per_flow_compatibility_backbone_items": 20000,
                            "target_shared_backbone_items_if_target_count_constant": 1,
                            "current_to_constant_target_ratio": 20000.0,
                        }
                    }
                }
            },
            "snapshot_reports": [
                {
                    "scope_id": "slice-a",
                    "flow_count": 2,
                    "shared_return_targets": {
                        "gateway_class_count": 1,
                    },
                },
                {
                    "scope_id": "slice-b",
                    "flow_count": 2,
                    "shared_return_targets": {
                        "gateway_class_count": 2,
                    },
                },
            ],
        }

        with patch(
            "inspect_goal_evidence.summarize_distance_matrix_run",
            side_effect=distance_runs,
        ), patch(
            "inspect_goal_evidence.summarize_goal_readiness",
            return_value=readiness,
        ), patch(
            "inspect_goal_evidence._load_json",
            return_value=audit,
        ), patch(
            "inspect_goal_evidence.summarize_return_path_migration_worklist",
            return_value=worklist,
        ), patch(
            "inspect_goal_evidence.inspect_temporal_run",
            return_value=temporal,
        ):
            report = inspect_goal_evidence(
                near_run=Path("near"),
                varied_runs=[Path("far")],
                return_audit_run=Path("return-audit"),
                baseline_real_run=Path("baseline"),
            )
            rendered = render_goal_evidence_summary(report)

        self.assertTrue(
            report["distance_diversity"]["one_hop_is_geography_specific"]
        )
        self.assertTrue(
            report["return_path_isolation"]["backbone_return_still_per_ut_or_per_flow"]
        )
        self.assertEqual(
            report["return_path_isolation"][
                "selected_candidate_shared_zone_route_count"
            ],
            1,
        )
        self.assertTrue(
            report["return_path_isolation"][
                "selected_candidate_zone_route_complete"
            ]
        )
        directional = report["directional_isolation"]
        self.assertEqual(directional["selected_flow_count"], 2)
        self.assertEqual(directional["forward_gateway_class_target_count"], 1)
        self.assertEqual(directional["forward_flow_to_gateway_class_ratio"], 2.0)
        self.assertTrue(directional["forward_state_bounded_by_gateway_classes"])
        self.assertEqual(directional["return_current_backbone_items_per_flow"], 2.0)
        self.assertEqual(
            directional["projection_10000_current_backbone_items"],
            20000,
        )
        self.assertEqual(
            report["runtime_update_protection"]["default_validation_scope"],
            "reapplied_gateway_class_sample",
        )
        self.assertTrue(
            report["timing_classification"][
                "initial_activation_separated_from_steady_runtime"
            ]
        )
        self.assertIn("one_hop_is_geography_specific=True", rendered)
        self.assertIn("profile=near run=near", rendered)
        self.assertIn("profile=mixed run=far", rendered)
        self.assertIn("top_backbone_work_item=reverse_lfib_label_stack", rendered)
        self.assertIn("candidate_zone_routes=1", rendered)
        self.assertIn("candidate_zone_route_coverage=1.0", rendered)
        self.assertIn("forward_model=ut_edge_gateway_class_selection", rendered)
        self.assertIn("forward_flow_to_gateway_class_ratio=2.0", rendered)
        self.assertIn("return_items_per_flow=2.0", rendered)
        self.assertIn("projection_10000_current=20000", rendered)
        self.assertIn("default_scope=reapplied_gateway_class_sample", rendered)
        self.assertIn("initial_separated=True", rendered)
        self.assertIn(
            "next_action=run_host_prepare_then_recheck_preflight_before_real_run",
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
