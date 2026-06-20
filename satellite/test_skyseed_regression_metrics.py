#!/usr/bin/env python3
# encoding: utf-8

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from regression_metrics import evaluate_regression, extract_regression_metrics, read_json


BASELINE_PATH = (
    Path(__file__).resolve().parent
    / "baselines"
    / "skyseed_1shell_6z_2h_regression_baseline.json"
)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _runtime_row(index: int = 1) -> dict:
    return {
        "sequence_index": index,
        "epoch_type": "runtime_epoch",
        "epoch_timing_marks_schema_version": "skyseed-epoch-timing-marks-v1",
        "activation_lag_seconds": 0.1,
        "semantic_scheduled_activation_lag_seconds": 0.1,
        "transition_wall_seconds": 2.0,
        "semantic_total_epoch_wall_seconds": 2.0,
        "host_operation_apply_wall_seconds": 0.8,
        "semantic_runtime_host_apply_seconds": 0.8,
        "node_local_apply_wall_seconds": 0.4,
        "semantic_runtime_node_local_apply_seconds": 0.4,
        "semantic_data_plane_ready_lag_seconds": 1.4,
        "semantic_scheduler_blocking_after_data_plane_ready_seconds": 0.02,
        "semantic_artifact_write_blocking_seconds": 0.0,
        "semantic_reporting_artifact_write_seconds": 0.0,
        "timing_mark_elapsed_scheduled_activation_time_utc_seconds": -0.1,
        "timing_mark_elapsed_actual_epoch_start_time_utc_seconds": 0.0,
        "timing_mark_elapsed_data_plane_apply_start_utc_seconds": 0.1,
        "timing_mark_elapsed_host_apply_start_seconds": 0.1,
        "timing_mark_elapsed_host_apply_end_seconds": 0.9,
        "timing_mark_elapsed_data_plane_ready_time_utc_seconds": 1.4,
        "timing_mark_elapsed_artifact_reporting_start_seconds": 2.0,
        "timing_mark_elapsed_artifact_reporting_end_seconds": 2.0,
        "timing_mark_elapsed_epoch_end_time_utc_seconds": 2.0,
    }


def _parameter_row(index: int = 3) -> dict:
    row = _runtime_row(index)
    row.update(
        {
            "epoch_type": "parameter_epoch",
            "transition_wall_seconds": 0.7,
            "semantic_total_epoch_wall_seconds": 0.7,
            "semantic_runtime_host_apply_seconds": None,
            "semantic_runtime_node_local_apply_seconds": None,
            "semantic_parameter_actual_mutation_seconds": 0.3,
            "semantic_parameter_recovery_fallback_seconds": 0.0,
            "semantic_topology_or_forwarding_mutation_free": True,
        }
    )
    return row


def _make_run(run_dir: Path, *, hidden_full_apply: bool = False, missing_mark: bool = False) -> None:
    run_dir.mkdir()
    _write_json(
        run_dir / "run_manifest.json",
        {
            "run_id": "test-run",
            "requested_satellite_count": 172,
            "requested_ut_catalog": "ut.json",
            "requested_gateway_catalog": "gw.json",
            "requested_zone_catalog": "zone.json",
            "compose_audit": {"network_counts": {"dynamic_satellite_link": 0}},
            "continuous_operation_summary": {
                "prepared_runtime_epoch_count": 2,
                "prepared_parameter_epoch_count": 1,
                "replenishment_segment_count": 1,
            },
        },
    )
    parameter = _parameter_row()
    if missing_mark:
        parameter.pop("timing_mark_elapsed_data_plane_ready_time_utc_seconds")
    _write_json(
        run_dir / "timing.json",
        {
            "finalized": True,
            "phase_timing": {"post_run_preflight_wall_seconds": 0.5},
            "timing_analysis": {
                "startup_preparation": {
                    "cold_start_before_first_epoch_execution_wall_seconds": 5.0
                },
                "initial_materialization_epoch": {"transition_wall_seconds": 2.0},
            },
            "rows": [_runtime_row(1), _runtime_row(2), parameter],
        },
    )
    _write_json(
        run_dir / "real_runtime_report.json",
        {
            "outcome": {
                "real_apply_success": True,
                "ping_success": True,
                "rollback_completed": True,
            },
            "iterations": [
                {
                    "epoch_type": "parameter_epoch",
                    "escalated_to_runtime_epoch": hidden_full_apply,
                    "scheduled_topology_mutation": False,
                    "topology_mutation_count": 0,
                    "forwarding_mutation_count": 0,
                    "parameter_refresh_recovery_used": False,
                    "node_local_execution": None,
                }
            ],
            "final_cleanup": {
                "all_cleanup_success": True,
                "node_local": {"timing": {"rollback_wall_seconds": 0.5}},
                "host_rollbacks": [{"timing": {"rollback_wall_seconds": 0.25}}],
                "host_artifact_cleanup": {
                    "remaining_bridge_count": 0,
                    "remaining_host_interface_count": 0,
                    "remaining_temp_host_interface_count": 0,
                    "timing": {"cleanup_wall_seconds": 0.25},
                },
            },
        },
    )
    _write_json(
        run_dir / "temporal_audit.json",
        {
            "finalized": True,
            "no_future_active_link_violation": True,
            "no_future_forwarding_state_violation": True,
            "parameter_epoch_without_topology_mutation": True,
            "runtime_epoch_has_expected_mutation_scope": True,
            "rollback_completed": True,
        },
    )
    _write_json(
        run_dir / "return_path_audit.json",
        {
            "current_return_state_scales_with_flow_count": False,
            "return_path_migration_readiness": {
                "current_realization": "shared_gz_backbone"
            },
            "state_counts": {"shared_gz_model_after": {"ut_mpls_egress_count": 0}},
        },
    )
    _write_json(
        run_dir / "gz_update_diff_summary.json",
        {
            "diffs": [
                {
                    "diff_scope": "changed_fec_zone_path_groups_only",
                    "added_fec_groups": ["gw1:zone-a:sat-a", "zone-a:default-gateway:gw1:sat-a:x"],
                    "removed_fec_groups": [],
                    "changed_fec_groups": [],
                    "skipped_unchanged_fec_groups": ["gw2:zone-b:sat-b"],
                    "changed_fec_group_count": 2,
                    "skipped_unchanged_fec_group_count": 1,
                    "changed_gz_return_group_count": 1,
                    "affected_node_count": 2,
                    "estimated_lfib_route_operation_items": 4,
                }
            ]
        },
    )
    _write_json(
        run_dir / "post_run_preflight.json",
        {"assessment": {"ready": True, "blocking_reasons": [], "warning_reasons": []}},
    )


class SkyseedRegressionMetricsTests(unittest.TestCase):
    def test_committed_baseline_passes_against_self(self) -> None:
        baseline = read_json(BASELINE_PATH)
        report = evaluate_regression(baseline, baseline)
        self.assertTrue(report["passed"])
        self.assertEqual(report["hard_failures"], [])

    def test_extracted_metrics_pass_against_self(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            _make_run(run_dir)
            metrics = extract_regression_metrics(run_dir)
            report = evaluate_regression(metrics, metrics)
        self.assertTrue(report["passed"])
        self.assertEqual(report["hard_failures"], [])

    def test_missing_timing_mark_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            _make_run(run_dir, missing_mark=True)
            metrics = extract_regression_metrics(run_dir)
            report = evaluate_regression(metrics, metrics)
        self.assertFalse(report["passed"])
        self.assertIn(
            "timing_mark_missing_count",
            {failure["metric"] for failure in report["hard_failures"]},
        )

    def test_parameter_epoch_hidden_full_apply_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            _make_run(run_dir, hidden_full_apply=True)
            metrics = extract_regression_metrics(run_dir)
            report = evaluate_regression(metrics, metrics)
        self.assertFalse(report["passed"])
        self.assertIn(
            "parameter_epoch_hidden_full_apply_count",
            {failure["metric"] for failure in report["hard_failures"]},
        )


if __name__ == "__main__":
    unittest.main()
