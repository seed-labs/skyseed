#!/usr/bin/env python3
# encoding: utf-8

import json
import tempfile
import unittest
from pathlib import Path

from temporal_run_comparator import (
    compare_temporal_runs,
    render_temporal_run_comparison_summary,
)


class TemporalRunComparatorTests(unittest.TestCase):
    def test_comparator_includes_runtime_validation_and_reuse_guards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run-a"
            run_dir.mkdir()
            (run_dir / "runtime_progress.json").write_text(
                json.dumps({"current_phase": "completed"}),
                encoding="utf-8",
            )
            (run_dir / "timing.json").write_text(
                json.dumps(
                    {
                        "finalized": True,
                        "phase_timing": {
                            "post_run_preflight_wall_seconds": 8.5,
                        },
                        "timing_analysis": {
                            "startup_preparation": {
                                "cold_start_before_first_epoch_execution_wall_seconds": 145.0,
                                "ready_to_execute_epochs_wall_seconds": 35.0,
                            },
                            "first_runtime_epoch": {
                                "transition_wall_seconds": 60.0,
                                "execution_after_prepare_reconcile_wall_seconds": 50.0,
                            },
                            "steady_state_runtime_epochs": {
                                "source_runtime_epoch_count": 3,
                                "excluded_initial_runtime_epoch_count": 1,
                                "count": 2,
                            },
                        },
                        "rows": [],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "return_path_audit.json").write_text(
                json.dumps(
                    {
                        "snapshot_count": 1,
                        "total_flow_count": 4,
                        "return_path_migration_blocker_count": 4,
                        "return_path_migration_blocker_ids": [
                            "gateway_nat_forward_rules",
                            "gateway_ut32_return_route",
                            "reverse_lfib_label_stack",
                            "serving_satellite_ut32_delivery_route",
                        ],
                        "return_path_migration_blockers": [
                            {
                                "blocker_id": "gateway_nat_forward_rules",
                                "migration_category": "gateway_edge_policy",
                                "total_current_item_count": 12,
                            },
                            {
                                "blocker_id": "reverse_lfib_label_stack",
                                "migration_category": "backbone_return",
                                "total_current_item_count": 4,
                            },
                        ],
                        "return_path_migration_category_totals": {
                            "backbone_return": {
                                "total_current_item_count": 4,
                            },
                            "gateway_edge_policy": {
                                "total_current_item_count": 12,
                            },
                            "edge_local_delivery": {
                                "total_current_item_count": 4,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "final_cleanup": {
                            "all_cleanup_success": True,
                            "node_local": {
                                "session_cleanup_count": 1,
                                "rollback_results": [{}, {}],
                                "timing": {
                                    "rollback_wall_seconds": 12.25,
                                },
                            },
                            "host_rollbacks": [
                                {"timing": {"rollback_wall_seconds": 3.0}},
                                {"timing": {"rollback_wall_seconds": 4.5}},
                            ],
                            "host_artifact_cleanup": {
                                "cleanup_completed": True,
                                "removed_host_interface_count": 9,
                                "removed_bridge_count": 4,
                                "remaining_host_interface_count": 0,
                                "remaining_bridge_count": 0,
                                "timing": {
                                    "cleanup_wall_seconds": 1.75,
                                },
                            },
                        },
                        "iterations": [
                            {
                                "node_local_execution": {
                                    "target_slice": {
                                        "executed_flow_ids": [
                                            "flow-a",
                                            "flow-b",
                                            "flow-c",
                                            "flow-d",
                                        ],
                                        "reapplied_flow_ids": ["flow-d"],
                                        "held_flow_ids": [
                                            "flow-a",
                                            "flow-b",
                                            "flow-c",
                                        ],
                                        "incremental_cleanup_flow_ids": [],
                                    },
                                    "preflight": {
                                        "ping_validation_selection": {
                                            "validation_scope": "reapplied_gateway_class_sample",
                                            "sample_source": "reapplied_gateway_class_sample",
                                            "active_flow_count": 4,
                                            "reapplied_flow_count": 1,
                                            "sample_source_flow_count": 1,
                                            "sample_source_gateway_class_count": 1,
                                            "selected_flow_count": 1,
                                            "selected_gateway_class_count": 1,
                                            "gateway_class_sample_reduction_count": 0,
                                            "selected_to_sample_source_flow_ratio": 1.0,
                                            "target_flow_ids": ["flow-d"],
                                        }
                                    },
                                }
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = compare_temporal_runs(
                [run_dir],
                stall_threshold_seconds=60.0,
            )
            rendered = render_temporal_run_comparison_summary(report)

        self.assertEqual(report["run_count"], 1)
        run = report["runs"][0]
        self.assertEqual(run["return_path_migration_blocker_count"], 4)
        self.assertEqual(
            run["return_path_top_migration_blocker"],
            "gateway_nat_forward_rules:12",
        )
        self.assertEqual(
            run["return_path_top_backbone_migration_blocker"],
            "reverse_lfib_label_stack:4",
        )
        self.assertEqual(run["return_path_backbone_return_items"], 4)
        self.assertEqual(run["return_path_gateway_edge_policy_items"], 12)
        self.assertEqual(run["return_path_edge_local_delivery_items"], 4)
        self.assertFalse(run["return_path_shared_backbone_ready"])
        self.assertTrue(run["return_path_backbone_migration_required"])
        self.assertEqual(run["return_path_backbone_blocker_items"], 4)
        self.assertTrue(run["runtime_ping_validation_present"])
        self.assertEqual(run["runtime_ping_validation_records"], 1)
        self.assertEqual(run["runtime_ping_validation_selected_max"], 1)
        self.assertEqual(run["runtime_ping_validation_reduction_max"], 0)
        self.assertTrue(run["runtime_incremental_reuse_present"])
        self.assertEqual(run["runtime_incremental_reuse_records"], 1)
        self.assertEqual(run["runtime_incremental_reuse_reapplied_max"], 1)
        self.assertEqual(run["runtime_incremental_reuse_held_max"], 3)
        self.assertEqual(run["runtime_incremental_reuse_reduction_max"], 3)
        self.assertEqual(run["runtime_incremental_reuse_reapplied_ratio_min"], 0.25)
        self.assertEqual(run["initial_activation_wall_seconds"], 195.0)
        self.assertEqual(run["initial_activation_cold_start_seconds"], 145.0)
        self.assertEqual(
            run["initial_activation_first_runtime_execution_seconds"],
            50.0,
        )
        self.assertTrue(run["initial_activation_steady_excludes_initial"])
        self.assertTrue(run["teardown_cleanup_present"])
        self.assertTrue(run["teardown_cleanup_success"])
        self.assertEqual(run["teardown_cleanup_known_wall_seconds"], 28.25)
        self.assertEqual(run["teardown_cleanup_wall_seconds"], 30.0)
        self.assertTrue(run["teardown_cleanup_wall_time_complete"])
        self.assertEqual(run["teardown_cleanup_node_local_rollback_seconds"], 12.25)
        self.assertEqual(run["teardown_cleanup_host_rollback_seconds"], 7.5)
        self.assertEqual(run["teardown_cleanup_host_rollback_count"], 2)
        self.assertEqual(run["teardown_cleanup_host_artifact_cleanup_seconds"], 1.75)
        self.assertEqual(run["teardown_cleanup_post_run_preflight_seconds"], 8.5)
        self.assertIn("return_path_migration_blockers=4", rendered)
        self.assertIn(
            "return_path_top_migration_blocker=gateway_nat_forward_rules:12",
            rendered,
        )
        self.assertIn(
            "return_path_top_backbone_migration_blocker=reverse_lfib_label_stack:4",
            rendered,
        )
        self.assertIn("return_path_backbone_return_items=4", rendered)
        self.assertIn("return_path_gateway_edge_policy_items=12", rendered)
        self.assertIn("return_path_edge_local_delivery_items=4", rendered)
        self.assertIn("return_path_shared_backbone_ready=False", rendered)
        self.assertIn("return_path_backbone_migration_required=True", rendered)
        self.assertIn("return_path_backbone_blocker_items=4", rendered)
        self.assertIn("runtime_ping_validation_selected_max=1", rendered)
        self.assertIn("runtime_incremental_reuse_held_max=3", rendered)
        self.assertIn("runtime_incremental_reuse_reapplied_ratio_min=0.25", rendered)
        self.assertIn("initial_activation_wall=195.0", rendered)
        self.assertIn("initial_activation_first_runtime_execution=50.0", rendered)
        self.assertIn("teardown_cleanup_known_wall=28.25", rendered)
        self.assertIn("teardown_cleanup_wall=30.0", rendered)
        self.assertIn("teardown_cleanup_host_artifact_cleanup=1.75", rendered)
        self.assertIn("teardown_cleanup_host_rollback_count=2", rendered)


if __name__ == "__main__":
    unittest.main()
