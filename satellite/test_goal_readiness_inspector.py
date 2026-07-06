#!/usr/bin/env python3
# encoding: utf-8

import json
import tempfile
import unittest
from pathlib import Path

from inspect_goal_readiness import (
    render_goal_readiness_summary,
    summarize_goal_readiness,
)


def _flow_context(
    *,
    flow_id: str,
    ut_id: str,
    gateway_id: str,
    satellite_path: list[str],
) -> dict:
    return {
        "flow_id": flow_id,
        "ut_id": ut_id,
        "selected_gateway_id": gateway_id,
        "gateway_selection_mode": "stable_preferred_gateway_order",
        "satellite_path": list(satellite_path),
    }


def _action(flow_context: dict) -> dict:
    return {"intent": {"outcome": {"flow_context": dict(flow_context)}}}


def _write_distance_run(
    run_dir: Path,
    *,
    run_name: str,
    satellite_paths: list[list[str]],
) -> Path:
    path = run_dir / run_name
    path.mkdir()
    (path / "ground_manifest.json").write_text(
        json.dumps(
            {
                "data_provenance": {
                    "ground": {
                        "ut_coordinates": [
                            {"ut_id": "ut1"},
                            {"ut_id": "ut2"},
                        ],
                        "gateway_coordinates": [{"gateway_id": "gw1"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (path / "runtime_update_plan_summary.json").write_text(
        json.dumps(
            [
                {
                    "slice_id": "slice-a",
                    "runtime_update_action_count": 6,
                    "node_subplan_count": 3,
                }
            ]
        ),
        encoding="utf-8",
    )
    actions = [
        _action(
            _flow_context(
                flow_id=f"flow-{index}",
                ut_id=f"ut{index + 1}",
                gateway_id="gw1",
                satellite_path=satellite_path,
            )
        )
        for index, satellite_path in enumerate(satellite_paths)
    ]
    (path / "planner_preview.json").write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "slice_id": "slice-a",
                        "runtime_update_plan": {
                            "node_subplans": {
                                "gw1": {"actions": actions},
                            }
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_return_audit_run(run_dir: Path) -> Path:
    path = run_dir / "return-audit"
    path.mkdir()
    readiness = {
        "current_realization": "per_ut_flow_compatibility",
        "target_realization": "shared_gateway_zone_or_zone_return_reachability",
        "shared_backbone_return_ready": False,
        "backbone_return_migration_required": True,
        "backbone_return_blocker_item_count": 8,
    }
    audit = {
        "snapshot_count": 1,
        "total_flow_count": 4,
        "total_return_path_intent_count": 4,
        "current_return_state_scales_with_flow_count": True,
        "shared_target_reduction_available": True,
        "total_current_backbone_return_state_item_count": 8,
        "total_target_shared_backbone_return_state_item_count": 2,
        "current_to_target_backbone_state_ratio": 4.0,
        "return_path_migration_blocker_count": 1,
        "return_path_migration_blockers": [
            {
                "blocker_id": "reverse_lfib_label_stack",
                "migration_category": "backbone_return",
                "total_current_item_count": 4,
            }
        ],
        "return_path_migration_category_totals": {
            "backbone_return": {"total_current_item_count": 8},
            "gateway_edge_policy": {"total_current_item_count": 12},
            "edge_local_delivery": {"total_current_item_count": 4},
        },
        "return_path_migration_readiness": readiness,
        "scale_projection": {
            "flows_10000": {
                "current_per_flow_compatibility_backbone_items": 20000,
                "target_shared_backbone_items_if_target_count_constant": 2,
                "current_to_constant_target_ratio": 10000.0,
            }
        },
    }
    selected = dict(audit)
    selected["total_flow_count"] = 2
    selected["total_current_backbone_return_state_item_count"] = 4
    selected["total_target_shared_backbone_return_state_item_count"] = 1
    selected["return_path_migration_readiness"] = dict(readiness)
    selected["return_path_migration_readiness"][
        "backbone_return_blocker_item_count"
    ] = 4
    audit["selected_runtime_scope"] = selected
    audit["selected_scope_ids"] = ["slice-a"]
    (path / "return_path_audit.json").write_text(json.dumps(audit), encoding="utf-8")
    return_context = {
        "zone_id": "zone-a",
        "selected_gateway_id": "gw1",
        "serving_satellite_id": "stl-1",
        "gateway_class_id": "zone-a:default-gateway:gw1",
        "reverse_labels": [200001],
        "return_path_intent": {
            "intent_kind": "zone_return_delivery",
            "zone_id": "zone-a",
            "selected_gateway_id": "gw1",
            "zone_serving_satellite_id": "stl-1",
            "candidate_shared_zone_return_route": {
                "target_kind": "gateway_zone_return_route_candidate",
                "target_key": "gw1:zone-a:stl-1:zone-route",
                "shared_backbone_return_target_key": "gw1:zone-a:stl-1",
                "route_scope": "zone_or_gateway_zone_return_reachability",
                "current_compatibility_route_scope": "ut_32_return_route",
                "target_route_scope": "zone_or_gateway_zone_return_target",
                "install_behavior": "not_installed_current_linux_compatibility_metadata_only",
            },
            "candidate_shared_return_label_stack": {
                "target_kind": "gateway_zone_return_label_stack_candidate",
                "target_key": "gw1:zone-a:stl-1:reverse-path:r1",
                "shared_backbone_return_target_key": "gw1:zone-a:stl-1",
                "reverse_path_signature": "r1",
                "state_owner": "satellite_backbone",
                "state_model": "candidate_shared_gateway_zone_return_label_stack",
                "install_behavior": "not_installed_current_linux_compatibility_metadata_only",
            },
            "current_linux_realization": {
                "mode": "per_ut_flow_compatibility",
            },
        },
    }
    (path / "planner_preview.json").write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "slice_id": "slice-a",
                        "runtime_update_plan": {
                            "node_subplans": {
                                "gw1": {
                                    "actions": [
                                        _action(
                                            {
                                                **return_context,
                                                "flow_id": "flow-a",
                                                "ut_id": "ut1",
                                            }
                                        ),
                                        _action(
                                            {
                                                **return_context,
                                                "flow_id": "flow-b",
                                                "ut_id": "ut2",
                                            }
                                        ),
                                    ]
                                }
                            }
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_baseline_real_run(run_dir: Path) -> Path:
    path = run_dir / "baseline-real"
    path.mkdir()
    (path / "timing.json").write_text(
        json.dumps(
            {
                "timing_analysis": {
                    "initial_activation": {
                        "classification": "bootstrap_plus_initial_runtime_epoch",
                        "initial_activation_wall_seconds": 100.0,
                        "cold_start_before_first_epoch_execution_wall_seconds": 70.0,
                        "first_runtime_epoch_execution_after_prepare_reconcile_wall_seconds": 30.0,
                        "steady_runtime_excludes_initial_activation": True,
                    },
                    "steady_state_runtime_epochs": {
                        "count": 2,
                        "source_runtime_epoch_count": 3,
                        "excluded_initial_runtime_epoch_count": 1,
                        "average_transition_wall_seconds": 5.5,
                        "average_host_mutation_apply_wall_seconds": 1.7,
                        "average_node_local_apply_wall_seconds": 3.5,
                    },
                    "parameter_epochs": {
                        "count": 2,
                        "average_transition_wall_seconds": 1.1,
                    },
                },
                "phase_timing": {"post_run_preflight_wall_seconds": 1.0},
            }
        ),
        encoding="utf-8",
    )
    (path / "real_runtime_report.json").write_text(
        json.dumps(
            {
                "final_cleanup": {
                    "all_cleanup_success": True,
                    "node_local": {"timing": {"rollback_wall_seconds": 2.0}},
                    "host_rollbacks": [
                        {"timing": {"rollback_wall_seconds": 3.0}},
                    ],
                    "host_artifact_cleanup": {
                        "cleanup_completed": True,
                        "timing": {"cleanup_wall_seconds": 4.0},
                    },
                },
                "node_local_execution": {
                    "target_slice": {
                        "active_flow_count": 4,
                        "reapplied_flow_count": 2,
                        "held_flow_count": 2,
                        "incremental_cleanup_flow_count": 0,
                        "executed_flow_ids": ["f1", "f2", "f3", "f4"],
                        "reapplied_flow_ids": ["f1", "f2"],
                        "held_flow_ids": ["f3", "f4"],
                        "incremental_cleanup_flow_ids": [],
                    },
                    "preflight": {
                        "ping_validation_selection": {
                            "validation_scope": "reapplied_gateway_class_sample",
                            "active_flow_count": 4,
                            "reapplied_flow_count": 2,
                            "sample_source_flow_count": 2,
                            "selected_flow_count": 1,
                            "sample_source_gateway_class_count": 1,
                            "selected_gateway_class_count": 1,
                            "gateway_class_sample_reduction_count": 1,
                            "selected_to_sample_source_flow_ratio": 0.5,
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return path


class GoalReadinessInspectorTests(unittest.TestCase):
    def test_missing_distance_artifact_fails_closed_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_near = root / "missing-near"
            far = _write_distance_run(
                root,
                run_name="far",
                satellite_paths=[["s1", "s2"], ["s3", "s4", "s5"]],
            )
            return_audit = _write_return_audit_run(root)
            baseline = _write_baseline_real_run(root)

            report = summarize_goal_readiness(
                near_run=missing_near,
                varied_runs=[far],
                return_audit_run=return_audit,
                baseline_real_run=baseline,
                protection_run=baseline,
            )

        self.assertFalse(report["static_evidence_ready"])
        self.assertFalse(report["distance_diversity"]["ready"])
        self.assertFalse(report["distance_diversity"]["artifact_ready"])
        self.assertIn(
            "distance_diversity_artifact_missing_or_unreadable",
            report["distance_diversity"]["blockers"],
        )
        self.assertFalse(report["distance_diversity"]["near_run"]["artifact_ready"])

    def test_summarizes_static_gate_without_live_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            near = _write_distance_run(
                root,
                run_name="near",
                satellite_paths=[["s1"], ["s2"]],
            )
            far = _write_distance_run(
                root,
                run_name="far",
                satellite_paths=[["s1", "s2"], ["s3", "s4", "s5"]],
            )
            return_audit = _write_return_audit_run(root)
            baseline = _write_baseline_real_run(root)

            report = summarize_goal_readiness(
                near_run=near,
                varied_runs=[far],
                return_audit_run=return_audit,
                baseline_real_run=baseline,
                protection_run=baseline,
            )
            rendered = render_goal_readiness_summary(report)

        self.assertTrue(report["static_evidence_ready"])
        self.assertFalse(report["long_real_run_ready"])
        self.assertEqual(
            report["next_action"],
            "run_seed310_preflight_before_spending_long_real_run",
        )
        self.assertTrue(report["distance_diversity"]["near_one_hop"])
        self.assertEqual(
            report["distance_diversity"]["varied_multihop_run_count"],
            1,
        )
        self.assertTrue(report["return_path_migration"]["audit_ready"])
        self.assertTrue(
            report["return_path_migration"]["install_point_audit_ready"]
        )
        self.assertEqual(
            report["return_path_migration"][
                "selected_runtime_shared_label_ratio"
            ],
            2.0,
        )
        self.assertEqual(
            report["return_path_migration"][
                "selected_runtime_candidate_shared_zone_route_count"
            ],
            1,
        )
        self.assertEqual(
            report["return_path_migration"][
                "selected_runtime_candidate_zone_route_coverage_ratio"
            ],
            1.0,
        )
        self.assertTrue(
            report["return_path_migration"][
                "selected_runtime_candidate_zone_route_complete"
            ]
        )
        self.assertEqual(
            report["return_path_migration"][
                "selected_runtime_candidate_metadata_coverage_ratio"
            ],
            1.0,
        )
        self.assertTrue(
            report["return_path_migration"][
                "selected_runtime_candidate_metadata_complete"
            ]
        )
        self.assertEqual(
            report["return_path_migration"][
                "selected_runtime_explicit_candidate_metadata_flow_count"
            ],
            2,
        )
        self.assertEqual(
            report["return_path_migration"][
                "selected_runtime_fallback_reverse_path_signature_flow_count"
            ],
            0,
        )
        self.assertGreater(
            report["return_path_migration"]["install_point_count"],
            0,
        )
        self.assertFalse(report["return_path_migration"]["shared_backbone_ready"])
        self.assertTrue(
            report["return_path_migration"]["backbone_migration_required"]
        )
        self.assertTrue(report["runtime_update_protection"]["ready"])
        self.assertTrue(
            report["runtime_update_protection"][
                "artifact_ping_validation_present"
            ]
        )
        self.assertTrue(
            report["runtime_update_protection"][
                "artifact_incremental_reuse_present"
            ]
        )
        self.assertTrue(report["timing_classification"]["ready"])
        self.assertIn("static_evidence_ready=True", rendered)
        self.assertIn("install_points_ready=True", rendered)
        self.assertIn("shared_label_ratio=2.0", rendered)
        self.assertIn("candidate_zone_routes=1", rendered)
        self.assertIn("candidate_zone_route_coverage=1.0", rendered)
        self.assertIn("candidate_metadata_coverage=1.0", rendered)
        self.assertIn("candidate_metadata_complete=True", rendered)
        self.assertIn("shared_backbone_ready=False", rendered)
        self.assertIn("preflight: present=False", rendered)


if __name__ == "__main__":
    unittest.main()
