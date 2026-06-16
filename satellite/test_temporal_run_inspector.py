#!/usr/bin/env python3
# encoding: utf-8

import json
import tempfile
import unittest
from pathlib import Path

from temporal_run_inspector import inspect_temporal_run, render_temporal_run_summary


class TemporalRunInspectorTests(unittest.TestCase):
    def test_inspector_summarizes_runtime_zone_return_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "runtime_progress.json").write_text(
                json.dumps({"current_phase": "completed"}),
                encoding="utf-8",
            )
            (run_dir / "timing.json").write_text(
                json.dumps({"finalized": True, "rows": []}),
                encoding="utf-8",
            )
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "finalized": False,
                        "iterations": [
                            {
                                "epoch_type": "runtime_epoch",
                                "node_local_execution": {
                                    "timing": {
                                        "zone_return_install_boundary": {
                                            "boundary_kind": "derived_shared_zone_return_targets",
                                            "install_behavior": "current_per_ut_flow_compatibility",
                                            "all_intent_count": 2,
                                            "reapplied_intent_count": 1,
                                            "scale_summary": {
                                                "all": {
                                                    "current_backbone_return_state_item_count": 8,
                                                    "target_shared_backbone_return_state_item_count": 2,
                                                    "candidate_shared_zone_route_count": 2,
                                                    "candidate_shared_label_stack_count": 2,
                                                    "current_to_target_backbone_state_ratio": 4.0,
                                                    "current_reverse_labels_to_candidate_label_stack_ratio": 2.0,
                                                },
                                                "reapplied": {
                                                    "current_backbone_return_state_item_count": 2,
                                                    "target_shared_backbone_return_state_item_count": 1,
                                                    "candidate_shared_zone_route_count": 1,
                                                    "candidate_shared_label_stack_count": 1,
                                                    "current_to_target_backbone_state_ratio": 2.0,
                                                    "current_reverse_labels_to_candidate_label_stack_ratio": 1.0,
                                                },
                                            },
                                            "all_intents": [
                                                {"target_key": "gw1:zone-a:stl-950001"},
                                                {"target_key": "gw2:zone-b:stl-950002"},
                                            ],
                                            "reapplied_intents": [
                                                {"target_key": "gw1:zone-a:stl-950001"}
                                            ],
                                        }
                                    }
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = inspect_temporal_run(run_dir)
            rendered = render_temporal_run_summary(report)

        boundary = report["runtime_zone_return_boundary_summary"]
        self.assertTrue(boundary["present"])
        self.assertEqual(boundary["boundary_record_count"], 1)
        self.assertEqual(boundary["max_all_intent_count"], 2)
        self.assertEqual(boundary["max_reapplied_intent_count"], 1)
        self.assertEqual(boundary["unique_all_target_key_count"], 2)
        self.assertEqual(
            boundary["install_behaviors"],
            ["current_per_ut_flow_compatibility"],
        )
        self.assertEqual(
            boundary["max_all_current_backbone_return_state_item_count"],
            8,
        )
        self.assertEqual(
            boundary["max_all_target_shared_backbone_return_state_item_count"],
            2,
        )
        self.assertEqual(boundary["max_all_current_to_target_backbone_state_ratio"], 4.0)
        self.assertEqual(boundary["max_all_candidate_shared_zone_route_count"], 2)
        self.assertEqual(boundary["max_all_candidate_shared_label_stack_count"], 2)
        self.assertEqual(
            boundary["max_all_reverse_labels_to_candidate_label_stack_ratio"],
            2.0,
        )
        self.assertEqual(
            boundary["max_reapplied_current_to_target_backbone_state_ratio"],
            2.0,
        )
        self.assertEqual(
            boundary["max_reapplied_candidate_shared_label_stack_count"],
            1,
        )
        self.assertEqual(
            boundary["max_reapplied_candidate_shared_zone_route_count"],
            1,
        )
        self.assertEqual(
            boundary["max_reapplied_reverse_labels_to_candidate_label_stack_ratio"],
            1.0,
        )
        self.assertIn("Runtime zone-return boundary: present=True", rendered)
        self.assertIn("all_backbone_ratio=4.0", rendered)
        self.assertIn("all_candidate_zone_routes=2", rendered)
        self.assertIn("all_candidate_label_stacks=2", rendered)

    def test_inspector_tolerates_missing_runtime_zone_return_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "runtime_progress.json").write_text(
                json.dumps({"current_phase": "completed"}),
                encoding="utf-8",
            )
            report = inspect_temporal_run(run_dir)

        boundary = report["runtime_zone_return_boundary_summary"]
        self.assertFalse(boundary["present"])
        self.assertEqual(boundary["boundary_record_count"], 0)
        self.assertEqual(boundary["max_all_intent_count"], 0)

    def test_inspector_summarizes_runtime_ping_validation_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "runtime_progress.json").write_text(
                json.dumps({"current_phase": "completed"}),
                encoding="utf-8",
            )
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "iterations": [
                            {
                                "node_local_execution": {
                                    "preflight": {
                                        "ping_validation_selection": {
                                            "validation_scope": "reapplied_gateway_class_sample",
                                            "sample_source": "reapplied_gateway_class_sample",
                                            "active_flow_count": 12,
                                            "reapplied_flow_count": 6,
                                            "sample_source_flow_count": 6,
                                            "sample_source_gateway_class_count": 3,
                                            "selected_flow_count": 3,
                                            "selected_gateway_class_count": 3,
                                            "gateway_class_sample_reduction_count": 3,
                                            "selected_to_sample_source_flow_ratio": 0.5,
                                            "target_flow_ids": [
                                                "flow-a",
                                                "flow-c",
                                                "flow-e",
                                            ],
                                        }
                                    }
                                }
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = inspect_temporal_run(run_dir)
            rendered = render_temporal_run_summary(report)

        validation = report["runtime_ping_validation_summary"]
        self.assertTrue(validation["present"])
        self.assertEqual(validation["selection_record_count"], 1)
        self.assertEqual(
            validation["validation_scopes"],
            ["reapplied_gateway_class_sample"],
        )
        self.assertEqual(validation["max_active_flow_count"], 12)
        self.assertEqual(validation["max_reapplied_flow_count"], 6)
        self.assertEqual(validation["max_selected_flow_count"], 3)
        self.assertEqual(validation["max_gateway_class_sample_reduction_count"], 3)
        self.assertEqual(validation["min_selected_to_sample_source_flow_ratio"], 0.5)
        self.assertIn("Runtime ping validation: present=True", rendered)
        self.assertIn("selected_max=3", rendered)
        self.assertIn("reduction_max=3", rendered)

    def test_inspector_summarizes_incremental_runtime_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "runtime_progress.json").write_text(
                json.dumps({"current_phase": "completed"}),
                encoding="utf-8",
            )
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
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
                                    }
                                }
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = inspect_temporal_run(run_dir)
            rendered = render_temporal_run_summary(report)

        reuse = report["runtime_incremental_reuse_summary"]
        self.assertTrue(reuse["present"])
        self.assertEqual(reuse["slice_record_count"], 1)
        self.assertEqual(reuse["max_active_flow_count"], 4)
        self.assertEqual(reuse["max_reapplied_flow_count"], 1)
        self.assertEqual(reuse["max_held_flow_count"], 3)
        self.assertEqual(reuse["max_reapply_reduction_count"], 3)
        self.assertEqual(reuse["slice_count_with_held_flows"], 1)
        self.assertEqual(reuse["min_reapplied_to_active_flow_ratio"], 0.25)
        self.assertEqual(reuse["max_held_to_active_flow_ratio"], 0.75)
        self.assertIn("Runtime incremental reuse: present=True", rendered)
        self.assertIn("reapplied_max=1", rendered)
        self.assertIn("held_max=3", rendered)

    def test_inspector_summarizes_failed_runtime_reuse_and_validation_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "runtime_progress.json").write_text(
                json.dumps({"current_phase": "runtime_rollback_failed"}),
                encoding="utf-8",
            )
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "iterations": [
                            {
                                "error": {
                                    "cleanup_session": {
                                        "target_slice": {
                                            "active_flow_count": 4,
                                            "reapplied_flow_count": 2,
                                            "held_flow_count": 2,
                                            "incremental_cleanup_flow_count": 1,
                                            "executed_flow_ids": [
                                                "flow-a",
                                                "flow-b",
                                                "flow-c",
                                                "flow-d",
                                            ],
                                            "reapplied_flow_ids": [
                                                "flow-c",
                                                "flow-d",
                                            ],
                                            "held_flow_ids": [
                                                "flow-a",
                                                "flow-b",
                                            ],
                                            "incremental_cleanup_flow_ids": [
                                                "flow-old",
                                            ],
                                        },
                                        "preflight": {
                                            "ping_validation_selection": {
                                                "validation_scope": "reapplied_gateway_class_sample",
                                                "sample_source": "reapplied_gateway_class_sample",
                                                "active_flow_count": 4,
                                                "reapplied_flow_count": 2,
                                                "sample_source_flow_count": 2,
                                                "sample_source_gateway_class_count": 1,
                                                "selected_flow_count": 1,
                                                "selected_gateway_class_count": 1,
                                                "gateway_class_sample_reduction_count": 1,
                                                "selected_to_sample_source_flow_ratio": 0.5,
                                                "target_flow_ids": ["flow-c"],
                                            }
                                        },
                                    }
                                }
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = inspect_temporal_run(run_dir)

        reuse = report["runtime_incremental_reuse_summary"]
        validation = report["runtime_ping_validation_summary"]
        self.assertTrue(reuse["present"])
        self.assertEqual(reuse["max_active_flow_count"], 4)
        self.assertEqual(reuse["max_reapplied_flow_count"], 2)
        self.assertEqual(reuse["max_held_flow_count"], 2)
        self.assertEqual(reuse["max_incremental_cleanup_flow_count"], 1)
        self.assertEqual(reuse["max_reapply_reduction_count"], 2)
        self.assertEqual(reuse["slice_count_with_incremental_cleanup"], 1)
        self.assertTrue(validation["present"])
        self.assertEqual(validation["max_selected_flow_count"], 1)
        self.assertEqual(validation["max_gateway_class_sample_reduction_count"], 1)

    def test_inspector_summarizes_teardown_cleanup_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "runtime_progress.json").write_text(
                json.dumps({"current_phase": "completed"}),
                encoding="utf-8",
            )
            (run_dir / "timing.json").write_text(
                json.dumps(
                    {
                        "phase_timing": {
                            "post_run_preflight_wall_seconds": 8.5,
                        },
                        "rows": [],
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
                                "session_cleanup_count": 2,
                                "rollback_results": [{}, {}, {}],
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
                            "post_cleanup_direct_backend_artifacts": {
                                "interfaces": [],
                                "bridges": [],
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            report = inspect_temporal_run(run_dir)
            rendered = render_temporal_run_summary(report)

        cleanup = report["teardown_cleanup_summary"]
        self.assertTrue(cleanup["present"])
        self.assertTrue(cleanup["all_cleanup_success"])
        self.assertEqual(cleanup["node_local_rollback_wall_seconds"], 12.25)
        self.assertEqual(cleanup["node_local_session_cleanup_count"], 2)
        self.assertEqual(cleanup["node_local_rollback_result_count"], 3)
        self.assertEqual(cleanup["host_rollback_count"], 2)
        self.assertEqual(cleanup["host_rollback_wall_seconds"], 7.5)
        self.assertEqual(cleanup["host_rollback_wall_max_seconds"], 4.5)
        self.assertEqual(cleanup["host_artifact_cleanup_wall_seconds"], 1.75)
        self.assertEqual(cleanup["post_run_preflight_wall_seconds"], 8.5)
        self.assertEqual(cleanup["known_teardown_cleanup_wall_seconds"], 28.25)
        self.assertEqual(cleanup["teardown_cleanup_wall_seconds"], 30.0)
        self.assertTrue(cleanup["teardown_cleanup_wall_time_complete"])
        self.assertIn("Teardown cleanup: present=True", rendered)
        self.assertIn("known_wall=28.25", rendered)
        self.assertIn("wall=30.0", rendered)
        self.assertIn("host_artifact_cleanup=1.75", rendered)

    def test_inspector_derives_missing_steady_runtime_source_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "runtime_progress.json").write_text(
                json.dumps({"current_phase": "completed"}),
                encoding="utf-8",
            )
            (run_dir / "timing.json").write_text(
                json.dumps(
                    {
                        "finalized": True,
                        "timing_analysis": {
                            "steady_state_runtime_epochs": {
                                "count": 2,
                                "average_transition_wall_seconds": 7.0,
                            }
                        },
                        "rows": [
                            {
                                "epoch_type": "runtime_epoch",
                                "activation_lag_seconds": 0.0,
                                "transition_wall_seconds": 60.0,
                            },
                            {
                                "epoch_type": "runtime_epoch",
                                "activation_lag_seconds": 0.0,
                                "transition_wall_seconds": 6.0,
                            },
                            {
                                "epoch_type": "runtime_epoch",
                                "activation_lag_seconds": 0.0,
                                "transition_wall_seconds": 8.0,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = inspect_temporal_run(run_dir)

        steady = report["timing_analysis"]["steady_state_runtime_epochs"]
        self.assertEqual(steady["source_runtime_epoch_count"], 3)
        self.assertEqual(steady["excluded_initial_runtime_epoch_count"], 1)

    def test_inspector_backfills_initial_activation_timing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "runtime_progress.json").write_text(
                json.dumps({"current_phase": "completed"}),
                encoding="utf-8",
            )
            (run_dir / "timing.json").write_text(
                json.dumps(
                    {
                        "finalized": True,
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

            report = inspect_temporal_run(run_dir)
            rendered = render_temporal_run_summary(report)

        initial_activation = report["timing_analysis"]["initial_activation"]
        self.assertEqual(
            initial_activation["classification"],
            "bootstrap_plus_initial_runtime_epoch",
        )
        self.assertEqual(
            initial_activation["initial_activation_wall_seconds"],
            195.0,
        )
        self.assertEqual(
            initial_activation[
                "first_runtime_epoch_execution_after_prepare_reconcile_wall_seconds"
            ],
            50.0,
        )
        self.assertTrue(
            initial_activation["steady_runtime_excludes_initial_activation"]
        )
        self.assertIn("Initial activation timing:", rendered)
        self.assertIn("wall=195.0", rendered)

    def test_inspector_summarizes_return_path_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "runtime_progress.json").write_text(
                json.dumps({"current_phase": "completed"}),
                encoding="utf-8",
            )
            (run_dir / "return_path_audit.json").write_text(
                json.dumps(
                    {
                        "snapshot_count": 1,
                        "total_flow_count": 12,
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
                                "total_current_item_count": 36,
                            },
                            {
                                "blocker_id": "reverse_lfib_label_stack",
                                "migration_category": "backbone_return",
                                "total_current_item_count": 12,
                            },
                        ],
                        "return_path_migration_category_totals": {
                            "backbone_return": {
                                "total_current_item_count": 12,
                            },
                            "gateway_edge_policy": {
                                "total_current_item_count": 36,
                            },
                            "edge_local_delivery": {
                                "total_current_item_count": 12,
                            },
                        },
                        "scale_projection": {
                            "projections": {
                                "10000": {
                                    "current_per_flow_compatibility_backbone_items": 20000,
                                    "target_shared_backbone_items_if_target_count_constant": 6,
                                    "target_shared_backbone_items_if_observed_target_ratio_scales": 5000,
                                    "current_to_constant_target_ratio": 3333.333333,
                                    "current_to_ratio_scaled_target_ratio": 4.0,
                                }
                            }
                        },
                        "selected_runtime_scope": {
                            "snapshot_count": 1,
                            "total_flow_count": 12,
                            "total_current_backbone_return_state_item_count": 24,
                            "total_target_shared_backbone_return_state_item_count": 6,
                            "scale_projection": {
                                "projections": {
                                    "10000": {
                                        "current_per_flow_compatibility_backbone_items": 20000,
                                        "target_shared_backbone_items_if_target_count_constant": 6,
                                        "current_to_constant_target_ratio": 3333.333333,
                                    }
                                }
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = inspect_temporal_run(run_dir)
            rendered = render_temporal_run_summary(report)

        audit = report["return_path_audit_summary"]
        self.assertEqual(audit["return_path_migration_blocker_count"], 4)
        self.assertEqual(
            audit["return_path_top_migration_blocker"],
            "gateway_nat_forward_rules:36",
        )
        self.assertEqual(
            audit["return_path_top_backbone_migration_blocker"],
            "reverse_lfib_label_stack:12",
        )
        self.assertEqual(audit["return_path_backbone_return_item_count"], 12)
        self.assertEqual(audit["return_path_gateway_edge_policy_item_count"], 36)
        self.assertEqual(audit["return_path_edge_local_delivery_item_count"], 12)
        self.assertFalse(audit["return_path_shared_backbone_ready"])
        self.assertTrue(audit["return_path_backbone_migration_required"])
        self.assertEqual(audit["return_path_backbone_blocker_item_count"], 12)
        self.assertEqual(
            audit["return_path_current_realization"],
            "per_ut_flow_compatibility",
        )
        self.assertEqual(
            audit["return_path_target_realization"],
            "shared_gateway_zone_or_zone_return_reachability",
        )
        self.assertFalse(audit["selected_runtime_shared_backbone_ready"])
        self.assertTrue(audit["selected_runtime_backbone_migration_required"])
        self.assertEqual(audit["selected_runtime_backbone_blocker_item_count"], 24)
        self.assertEqual(audit["projection_10000_current_backbone_items"], 20000)
        self.assertEqual(audit["projection_10000_ratio_constant"], 3333.333333)
        self.assertEqual(
            audit["selected_runtime_projection_10000_current_backbone_items"],
            20000,
        )
        self.assertIn("projection_10000_current=20000", rendered)
        self.assertIn("shared_backbone_ready=False", rendered)
        self.assertIn("backbone_migration_required=True", rendered)
        self.assertIn("backbone_blocker_items=12", rendered)
        self.assertIn("migration_blockers=4", rendered)
        self.assertIn("top_migration_blocker=gateway_nat_forward_rules:36", rendered)
        self.assertIn(
            "top_backbone_migration_blocker=reverse_lfib_label_stack:12",
            rendered,
        )
        self.assertIn("backbone_return_items=12", rendered)
        self.assertIn("gateway_edge_policy_items=36", rendered)
        self.assertIn("edge_local_delivery_items=12", rendered)

    def test_inspector_backfills_return_path_migration_blockers_for_old_audits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "runtime_progress.json").write_text(
                json.dumps({"current_phase": "completed"}),
                encoding="utf-8",
            )
            (run_dir / "return_path_audit.json").write_text(
                json.dumps(
                    {
                        "snapshot_count": 1,
                        "total_flow_count": 12,
                        "snapshot_reports": [
                            {
                                "per_flow_return_state": {
                                    "gateway_ut32_return_route_count": 12,
                                    "serving_satellite_ut32_delivery_route_count": 12,
                                    "reverse_label_count": 12,
                                    "reverse_transit_lfib_entry_count": 0,
                                    "gateway_nat_forward_rule_count": 36,
                                }
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = inspect_temporal_run(run_dir)
            rendered = render_temporal_run_summary(report)

        audit = report["return_path_audit_summary"]
        self.assertEqual(audit["return_path_migration_blocker_count"], 4)
        self.assertEqual(
            audit["return_path_top_migration_blocker"],
            "gateway_nat_forward_rules:36",
        )
        self.assertEqual(
            audit["return_path_top_backbone_migration_blocker"],
            "reverse_lfib_label_stack:12",
        )
        self.assertEqual(audit["return_path_backbone_return_item_count"], 24)
        self.assertEqual(audit["return_path_gateway_edge_policy_item_count"], 36)
        self.assertEqual(audit["return_path_edge_local_delivery_item_count"], 12)
        self.assertFalse(audit["return_path_shared_backbone_ready"])
        self.assertTrue(audit["return_path_backbone_migration_required"])
        self.assertEqual(audit["return_path_backbone_blocker_item_count"], 24)
        self.assertIn("migration_blockers=4", rendered)
        self.assertIn("shared_backbone_ready=False", rendered)
        self.assertIn("backbone_migration_required=True", rendered)
        self.assertIn("backbone_blocker_items=24", rendered)
        self.assertIn("top_migration_blocker=gateway_nat_forward_rules:36", rendered)
        self.assertIn(
            "top_backbone_migration_blocker=reverse_lfib_label_stack:12",
            rendered,
        )
        self.assertIn("backbone_return_items=24", rendered)
        self.assertIn("gateway_edge_policy_items=36", rendered)
        self.assertIn("edge_local_delivery_items=12", rendered)


if __name__ == "__main__":
    unittest.main()
