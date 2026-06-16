#!/usr/bin/env python3
# encoding: utf-8

import json
import tempfile
import unittest
from pathlib import Path

from run_starlink_shell_experiment import (
    DEADLINE_MISS_THRESHOLD_SECONDS,
    EPOCH_TIMING_DERIVED_FIELDS,
    EPOCH_TIMING_MARK_FIELDS,
    EPOCH_TIMING_MARK_SCHEMA_VERSION,
    _build_epoch_timing_marks,
    _live_timing_summary,
    _record_node_local_timing_marks,
    _runtime_report_for_final_timing,
    _timing_analysis,
    _timing_rows,
)


class TimingAnalysisTests(unittest.TestCase):
    def test_steady_runtime_metrics_exclude_first_runtime_epoch(self) -> None:
        analysis = _timing_analysis(
            phase_timing={
                "compose_up_wall_seconds": 100.0,
                "container_readiness_wall_seconds": 10.0,
                "initial_runtime_readiness_wall_seconds": 5.0,
                "pre_epoch_host_runtime_prewarm_wall_seconds": 20.0,
                "first_runtime_epoch_transition_wall_seconds": 60.0,
                "first_runtime_epoch_network_mutation_wall_seconds": 30.0,
                "first_runtime_epoch_node_local_apply_wall_seconds": 10.0,
                "first_runtime_epoch_direct_backend_prepare_wall_seconds": 7.0,
                "first_runtime_epoch_direct_backend_reconcile_wall_seconds": 3.0,
            },
            timing_rows=[
                {
                    "epoch_type": "runtime_epoch",
                    "transition_wall_seconds": 60.0,
                    "network_mutation_plan_wall_seconds": 30.0,
                    "host_operation_apply_wall_seconds": 20.0,
                    "node_local_apply_wall_seconds": 10.0,
                },
                {
                    "epoch_type": "runtime_epoch",
                    "transition_wall_seconds": 6.0,
                    "network_mutation_plan_wall_seconds": 2.0,
                    "host_operation_apply_wall_seconds": 1.0,
                    "node_local_apply_wall_seconds": 3.0,
                },
                {
                    "epoch_type": "runtime_epoch",
                    "transition_wall_seconds": 8.0,
                    "network_mutation_plan_wall_seconds": 4.0,
                    "host_operation_apply_wall_seconds": 3.0,
                    "node_local_apply_wall_seconds": 5.0,
                },
                {
                    "epoch_type": "parameter_epoch",
                    "transition_wall_seconds": 1.5,
                    "host_operation_apply_wall_seconds": 0.5,
                },
            ],
        )

        startup = analysis["startup_preparation"]
        initial_activation = analysis["initial_activation"]
        first_runtime = analysis["first_runtime_epoch"]
        steady = analysis["steady_state_runtime_epochs"]
        parameter = analysis["parameter_epochs"]

        self.assertEqual(startup["ready_to_execute_epochs_wall_seconds"], 35.0)
        self.assertEqual(
            startup["cold_start_before_first_epoch_execution_wall_seconds"],
            145.0,
        )
        self.assertEqual(first_runtime["transition_wall_seconds"], 60.0)
        self.assertEqual(
            first_runtime["execution_after_prepare_reconcile_wall_seconds"],
            50.0,
        )
        self.assertEqual(
            initial_activation["classification"],
            "bootstrap_plus_initial_runtime_epoch",
        )
        self.assertTrue(initial_activation["includes_first_runtime_epoch"])
        self.assertTrue(
            initial_activation["steady_runtime_excludes_initial_activation"]
        )
        self.assertEqual(
            initial_activation[
                "cold_start_before_first_epoch_execution_wall_seconds"
            ],
            145.0,
        )
        self.assertEqual(
            initial_activation[
                "first_runtime_epoch_prepare_reconcile_wall_seconds"
            ],
            10.0,
        )
        self.assertEqual(
            initial_activation[
                "first_runtime_epoch_execution_after_prepare_reconcile_wall_seconds"
            ],
            50.0,
        )
        self.assertEqual(
            initial_activation["initial_activation_wall_seconds"],
            195.0,
        )
        self.assertEqual(steady["source_runtime_epoch_count"], 3)
        self.assertEqual(steady["excluded_initial_runtime_epoch_count"], 1)
        self.assertEqual(steady["count"], 2)
        self.assertEqual(steady["average_transition_wall_seconds"], 7.0)
        self.assertEqual(steady["average_network_mutation_plan_wall_seconds"], 3.0)
        self.assertEqual(steady["average_host_mutation_apply_wall_seconds"], 2.0)
        self.assertEqual(steady["average_node_local_apply_wall_seconds"], 4.0)
        self.assertEqual(parameter["count"], 1)
        self.assertEqual(parameter["average_transition_wall_seconds"], 1.5)

    def test_single_runtime_epoch_has_no_steady_runtime_average(self) -> None:
        analysis = _timing_analysis(
            phase_timing={
                "first_runtime_epoch_transition_wall_seconds": 12.0,
            },
            timing_rows=[
                {
                    "epoch_type": "runtime_epoch",
                    "transition_wall_seconds": 12.0,
                }
            ],
        )

        steady = analysis["steady_state_runtime_epochs"]
        initial_activation = analysis["initial_activation"]
        self.assertEqual(steady["source_runtime_epoch_count"], 1)
        self.assertEqual(steady["excluded_initial_runtime_epoch_count"], 1)
        self.assertEqual(steady["count"], 0)
        self.assertEqual(steady["average_transition_wall_seconds"], 0.0)
        self.assertEqual(initial_activation["initial_activation_wall_seconds"], 12.0)

    def test_live_timing_summary_exposes_p95_and_deadline_definition(self) -> None:
        summary = _live_timing_summary(
            [
                {
                    "epoch_type": "runtime_epoch",
                    "activation_lag_seconds": 0.01,
                    "deadline_miss": False,
                    "timing": {"transition_wall_seconds": 1.0},
                },
                {
                    "epoch_type": "runtime_epoch",
                    "activation_lag_seconds": 0.20,
                    "deadline_miss": True,
                    "timing": {"transition_wall_seconds": 5.0},
                },
                {
                    "epoch_type": "parameter_epoch",
                    "activation_lag_seconds": 0.03,
                    "deadline_miss": False,
                    "timing": {
                        "transition_wall_seconds": 0.5,
                        "host_operation_apply_wall_seconds": 0.2,
                    },
                },
            ]
        )

        self.assertEqual(
            summary["deadline_miss_threshold_seconds"],
            DEADLINE_MISS_THRESHOLD_SECONDS,
        )
        self.assertIn("strict scheduler-boundary", summary["deadline_miss_definition"])
        self.assertEqual(summary["deadline_miss_count"], 1)
        self.assertEqual(
            summary["runtime_epochs"]["activation_lag_seconds"]["p95_seconds"],
            0.2,
        )
        self.assertEqual(
            summary["runtime_epochs"]["transition_wall_seconds"]["p95_seconds"],
            5.0,
        )
        self.assertEqual(
            summary["parameter_epochs"]["transition_wall_seconds"]["average_seconds"],
            0.5,
        )

    def test_live_timing_summary_exposes_semantic_epoch_breakdowns(self) -> None:
        summary = _live_timing_summary(
            [
                {
                    "epoch_type": "runtime_epoch",
                    "activation_lag_seconds": 0.5,
                    "topology_mutation_count": 2,
                    "forwarding_mutation_count": 3,
                    "timing": {
                        "transition_wall_seconds": 10.0,
                        "host_runtime_prepare_wall_seconds": 0.5,
                        "host_runtime_reconcile_wall_seconds": 0.25,
                        "host_operation_apply_wall_seconds": 1.0,
                        "node_local_apply_wall_seconds": 2.0,
                        "node_local_incremental_cleanup_wall_seconds": 1.5,
                        "pre_transition_connectivity_probe_wall_seconds": 0.25,
                        "post_transition_connectivity_probe_wall_seconds": 0.75,
                        "access_link_warmup_wall_seconds": 0.5,
                        "node_local_verify_wall_seconds": 0.25,
                        "audit_write_seconds": 0.1,
                    },
                },
                {
                    "epoch_type": "parameter_epoch",
                    "activation_lag_seconds": 0.1,
                    "escalated_to_runtime_epoch": False,
                    "topology_mutation_count": 0,
                    "forwarding_mutation_count": 0,
                    "timing": {
                        "transition_wall_seconds": 2.0,
                        "parameter_qdisc_apply_seconds": 0.4,
                        "parameter_metadata_update_seconds": 0.1,
                        "parameter_dirty_reconcile_seconds": 0.2,
                        "targeted_validation_seconds": 0.3,
                        "post_transition_connectivity_probe_wall_seconds": 0.2,
                    },
                },
            ]
        )

        runtime_breakdown = summary["runtime_epochs"]["semantic_timing_breakdown"]
        parameter_breakdown = summary["parameter_epochs"]["semantic_timing_breakdown"]
        self.assertIn("definitions", runtime_breakdown)
        self.assertEqual(
            runtime_breakdown["distributions"]["data_plane_mutation_apply_seconds"][
                "average_seconds"
            ],
            3.0,
        )
        self.assertEqual(
            runtime_breakdown["distributions"]["data_plane_ready_lag_seconds"][
                "average_seconds"
            ],
            6.0,
        )
        self.assertEqual(
            parameter_breakdown["distributions"]["parameter_actual_mutation_seconds"][
                "average_seconds"
            ],
            0.5,
        )
        self.assertEqual(
            parameter_breakdown["topology_or_forwarding_mutation_free_count"],
            1,
        )
        self.assertEqual(parameter_breakdown["lightweight_under_event_time_count"], 1)

    def test_timing_rows_flatten_semantic_breakdown_for_csv_artifacts(self) -> None:
        rows = _timing_rows(
            {
                "iterations": [
                    {
                        "sequence_index": 1,
                        "epoch_type": "runtime_epoch",
                        "activation_lag_seconds": 0.5,
                        "timing": {
                            "transition_wall_seconds": 10.0,
                            "host_operation_apply_wall_seconds": 1.0,
                            "node_local_apply_wall_seconds": 2.0,
                            "node_local_incremental_cleanup_wall_seconds": 1.5,
                        },
                    },
                    {
                        "sequence_index": 2,
                        "epoch_type": "parameter_epoch",
                        "activation_lag_seconds": 0.1,
                        "escalated_to_runtime_epoch": False,
                        "topology_mutation_count": 0,
                        "forwarding_mutation_count": 0,
                        "timing": {
                            "transition_wall_seconds": 2.0,
                            "parameter_qdisc_apply_seconds": 0.4,
                            "parameter_metadata_update_seconds": 0.1,
                            "targeted_validation_seconds": 0.3,
                        },
                    },
                ]
            }
        )

        self.assertEqual(
            rows[0]["semantic_data_plane_mutation_apply_seconds"],
            3.0,
        )
        self.assertEqual(
            rows[0]["semantic_pre_apply_cleanup_or_blocking_verification_seconds"],
            1.5,
        )
        self.assertEqual(
            rows[1]["semantic_parameter_actual_mutation_seconds"],
            0.5,
        )
        self.assertTrue(
            rows[1]["semantic_parameter_epoch_lightweight_under_event_time"]
        )

    def test_final_timing_recovers_live_checkpoint_when_runtime_report_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint = {
                "finalized": False,
                "iterations": [
                    {
                        "epoch_type": "runtime_epoch",
                        "sequence_index": 1,
                        "activation_lag_seconds": 0.5,
                        "timing": {
                            "transition_wall_seconds": 4.0,
                            "host_operation_apply_wall_seconds": 1.0,
                            "node_local_apply_wall_seconds": 2.0,
                        },
                    }
                ],
                "timing_summary": {"runtime_epoch_count": 1},
            }
            path = Path(tmpdir)
            (path / "real_runtime_report.json").write_text(
                json.dumps(checkpoint),
                encoding="utf-8",
            )

            recovered = _runtime_report_for_final_timing(path, None)

        self.assertIsNotNone(recovered)
        self.assertEqual(len(_timing_rows(recovered or {})), 1)

    def test_epoch_timing_marks_derive_first_class_boundaries(self) -> None:
        marks = _build_epoch_timing_marks(
            scheduled_activation_time_utc="2026-06-10T08:46:06Z",
            actual_epoch_start_wall=100.0,
            actual_epoch_start_epoch=1_800_000_000.0,
            activation_lag_seconds=0.5,
            mark_wall_times={
                "pre_apply_cleanup_start": 100.1,
                "pre_apply_cleanup_end": 100.3,
                "blocking_verification_start": 100.3,
                "blocking_verification_end": 100.5,
                "data_plane_apply_start_utc": 100.5,
                "host_apply_start": 100.5,
                "host_apply_end": 101.0,
                "node_local_apply_start": 101.0,
                "node_local_apply_end": 102.0,
                "data_plane_ready_time_utc": 102.0,
                "post_apply_validation_start": 102.0,
                "post_apply_validation_end": 102.4,
                "post_apply_cleanup_verification_start": 102.4,
                "post_apply_cleanup_verification_end": 102.6,
                "artifact_reporting_start": 102.6,
                "artifact_reporting_end": 102.7,
                "epoch_end_time_utc": 102.7,
            },
        )

        self.assertEqual(marks["schema_version"], EPOCH_TIMING_MARK_SCHEMA_VERSION)
        for field in EPOCH_TIMING_MARK_FIELDS:
            self.assertIn(field, marks)
            self.assertIn(field, marks["monotonic_elapsed_seconds"])
        self.assertEqual(
            marks["derived_seconds"]["actual_network_mutation_time_seconds"],
            1.5,
        )
        self.assertEqual(
            marks["derived_seconds"]["blocking_cleanup_verification_time_seconds"],
            0.4,
        )
        self.assertEqual(
            marks["derived_seconds"][
                "post_apply_non_blocking_validation_reporting_time_seconds"
            ],
            0.5,
        )
        self.assertEqual(
            marks["derived_seconds"]["total_epoch_wall_time_seconds"],
            2.7,
        )
        self.assertEqual(
            marks["derived_seconds"]["data_plane_ready_lag_seconds"],
            2.5,
        )
        self.assertEqual(
            marks["derived_seconds"][
                "scheduler_blocking_after_data_plane_ready_seconds"
            ],
            0.7,
        )
        self.assertEqual(
            marks["derived_seconds"]["required_safety_gate_seconds"],
            0.2,
        )
        self.assertEqual(
            marks["derived_seconds"]["artifact_write_blocking_seconds"],
            0.1,
        )
        self.assertEqual(
            marks["derived_seconds"]["non_blocking_post_apply_seconds"],
            0.0,
        )

    def test_timing_rows_flatten_epoch_timing_marks_for_both_epoch_types(self) -> None:
        runtime_marks = _build_epoch_timing_marks(
            scheduled_activation_time_utc="2026-06-10T08:46:06Z",
            actual_epoch_start_wall=10.0,
            actual_epoch_start_epoch=1_800_000_000.0,
            activation_lag_seconds=0.1,
            mark_wall_times={
                "data_plane_apply_start_utc": 10.2,
                "host_apply_start": 10.2,
                "host_apply_end": 10.7,
                "node_local_apply_start": 10.7,
                "node_local_apply_end": 11.0,
                "data_plane_ready_time_utc": 11.0,
                "post_apply_validation_start": 11.0,
                "post_apply_validation_end": 11.2,
                "artifact_reporting_start": 11.2,
                "artifact_reporting_end": 11.21,
                "epoch_end_time_utc": 11.21,
            },
        )
        parameter_marks = _build_epoch_timing_marks(
            scheduled_activation_time_utc="2026-06-10T08:46:16Z",
            actual_epoch_start_wall=20.0,
            actual_epoch_start_epoch=1_800_000_010.0,
            activation_lag_seconds=0.2,
            mark_wall_times={
                "data_plane_apply_start_utc": 20.0,
                "host_apply_start": 20.0,
                "host_apply_end": 20.4,
                "data_plane_ready_time_utc": 20.4,
                "post_apply_validation_start": 20.4,
                "post_apply_validation_end": 20.5,
                "artifact_reporting_start": 20.5,
                "artifact_reporting_end": 20.51,
                "epoch_end_time_utc": 20.51,
            },
        )
        rows = _timing_rows(
            {
                "iterations": [
                    {
                        "sequence_index": 1,
                        "epoch_type": "runtime_epoch",
                        "activation_lag_seconds": 0.1,
                        "epoch_timing_marks": runtime_marks,
                        "timing": {"transition_wall_seconds": 1.2},
                    },
                    {
                        "sequence_index": 2,
                        "epoch_type": "parameter_epoch",
                        "activation_lag_seconds": 0.2,
                        "epoch_timing_marks": parameter_marks,
                        "timing": {"transition_wall_seconds": 0.5},
                    },
                ]
            }
        )

        for row in rows:
            self.assertEqual(
                row["epoch_timing_marks_schema_version"],
                EPOCH_TIMING_MARK_SCHEMA_VERSION,
            )
            for field in EPOCH_TIMING_MARK_FIELDS:
                self.assertIn(f"timing_mark_{field}", row)
                self.assertIn(f"timing_mark_elapsed_{field}_seconds", row)
            for field in EPOCH_TIMING_DERIVED_FIELDS:
                self.assertIn(f"derived_{field}", row)
        self.assertEqual(
            rows[0]["derived_actual_network_mutation_time_seconds"],
            0.8,
        )
        self.assertEqual(
            rows[1]["timing_mark_node_local_apply_start"],
            None,
        )
        self.assertEqual(
            rows[1]["derived_data_plane_ready_lag_seconds"],
            0.6,
        )
        self.assertEqual(
            rows[0]["scheduler_blocking_after_data_plane_ready_seconds"],
            None,
        )
        self.assertEqual(
            rows[0]["derived_scheduler_blocking_after_data_plane_ready_seconds"],
            0.21,
        )
        self.assertIn(
            "semantic_scheduler_blocking_after_data_plane_ready_seconds",
            rows[0],
        )

    def test_node_local_cleanup_becomes_first_class_pre_apply_mark(self) -> None:
        mark_wall_times = {
            "data_plane_apply_start_utc": 10.0,
            "host_apply_start": 10.0,
            "host_apply_end": 12.0,
            "artifact_reporting_start": 18.0,
            "artifact_reporting_end": 18.1,
            "epoch_end_time_utc": 18.1,
        }
        _record_node_local_timing_marks(
            mark_wall_times,
            node_local_apply_started_wall=12.5,
            node_local_apply_wall_seconds=3.0,
            node_local_incremental_cleanup_wall_seconds=2.0,
            node_local_verify_wall_seconds=0.5,
        )

        marks = _build_epoch_timing_marks(
            scheduled_activation_time_utc="2026-06-10T08:46:06Z",
            actual_epoch_start_wall=10.0,
            actual_epoch_start_epoch=1_800_000_000.0,
            activation_lag_seconds=0.25,
            mark_wall_times=mark_wall_times,
        )

        elapsed = marks["monotonic_elapsed_seconds"]
        self.assertEqual(elapsed["pre_apply_cleanup_start"], 2.5)
        self.assertEqual(elapsed["pre_apply_cleanup_end"], 4.5)
        self.assertEqual(elapsed["node_local_apply_start"], 4.5)
        self.assertEqual(elapsed["node_local_apply_end"], 7.5)
        self.assertEqual(elapsed["data_plane_ready_time_utc"], 7.5)
        self.assertEqual(
            marks["derived_seconds"]["blocking_cleanup_verification_time_seconds"],
            2.0,
        )
        self.assertEqual(
            marks["derived_seconds"]["actual_network_mutation_time_seconds"],
            7.5,
        )
        self.assertEqual(
            marks["derived_seconds"]["data_plane_ready_lag_seconds"],
            7.75,
        )

    def test_live_timing_summary_classifies_post_data_plane_scheduler_cost(self) -> None:
        runtime_marks_initial = _build_epoch_timing_marks(
            scheduled_activation_time_utc="2026-06-10T08:46:06Z",
            actual_epoch_start_wall=10.0,
            actual_epoch_start_epoch=1_800_000_000.0,
            activation_lag_seconds=0.0,
            mark_wall_times={
                "data_plane_apply_start_utc": 10.0,
                "host_apply_start": 10.0,
                "host_apply_end": 11.0,
                "node_local_apply_start": 11.0,
                "node_local_apply_end": 12.0,
                "data_plane_ready_time_utc": 12.0,
                "post_apply_validation_start": 12.0,
                "post_apply_validation_end": 13.0,
                "artifact_reporting_start": 13.0,
                "artifact_reporting_end": 13.1,
                "epoch_end_time_utc": 13.1,
            },
        )
        runtime_marks_steady = _build_epoch_timing_marks(
            scheduled_activation_time_utc="2026-06-10T08:48:06Z",
            actual_epoch_start_wall=20.0,
            actual_epoch_start_epoch=1_800_000_120.0,
            activation_lag_seconds=0.0,
            mark_wall_times={
                "data_plane_apply_start_utc": 20.0,
                "host_apply_start": 20.0,
                "host_apply_end": 20.5,
                "node_local_apply_start": 20.5,
                "node_local_apply_end": 21.0,
                "data_plane_ready_time_utc": 21.0,
                "post_apply_validation_start": 21.0,
                "post_apply_validation_end": 21.2,
                "artifact_reporting_start": 21.2,
                "artifact_reporting_end": 21.25,
                "epoch_end_time_utc": 21.25,
            },
        )
        summary = _live_timing_summary(
            [
                {
                    "sequence_index": 1,
                    "epoch_id": "runtime-0",
                    "epoch_type": "runtime_epoch",
                    "segment_epoch_key": "initial:slice-0001",
                    "epoch_timing_marks": runtime_marks_initial,
                    "timing": {
                        "transition_wall_seconds": 3.1,
                        "scheduler_blocking_after_data_plane_ready_seconds": 1.1,
                        "semantic_breakdown": {
                            "scheduler_blocking_after_data_plane_ready_seconds": 1.1,
                        },
                    },
                },
                {
                    "sequence_index": 2,
                    "epoch_id": "runtime-1",
                    "epoch_type": "runtime_epoch",
                    "segment_epoch_key": "initial:slice-0002",
                    "epoch_timing_marks": runtime_marks_steady,
                    "timing": {
                        "transition_wall_seconds": 1.25,
                        "scheduler_blocking_after_data_plane_ready_seconds": 0.25,
                        "semantic_breakdown": {
                            "scheduler_blocking_after_data_plane_ready_seconds": 0.25,
                        },
                    },
                },
            ]
        )

        self.assertTrue(
            summary["post_data_plane_phase_classification"][
                "epoch_execution_details_jsonl_writing"
            ]["blocks_next_epoch"]
        )
        self.assertTrue(summary["initial_runtime_epoch"]["present"])
        self.assertEqual(
            summary["runtime_epochs_excluding_initial"][
                "excluded_initial_runtime_epoch_count"
            ],
            1,
        )
        self.assertEqual(summary["runtime_epochs_excluding_initial"]["count"], 1)
        self.assertEqual(
            summary["runtime_epochs_excluding_initial"][
                "scheduler_blocking_after_data_plane_ready_seconds"
            ]["average_seconds"],
            0.25,
        )


if __name__ == "__main__":
    unittest.main()
