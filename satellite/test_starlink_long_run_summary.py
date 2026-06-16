#!/usr/bin/env python3
# encoding: utf-8

import json
import tempfile
import unittest
from pathlib import Path

from summarize_starlink_long_run import build_summary


class StarlinkLongRunSummaryTests(unittest.TestCase):
    def test_build_summary_merges_timing_gz_and_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "run",
                        "run_mode": "real-runtime",
                        "outcome": {"rollback_completed": True},
                        "continuous_operation_summary": {
                            "prepared_runtime_epoch_count": 2,
                            "prepared_parameter_epoch_count": 1,
                            "prepared_epoch_queue_count": 3,
                            "replenishment_segment_count": 1,
                        },
                    }
                )
            )
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "outcome": {
                            "ping_success": True,
                            "real_apply_success": True,
                            "rollback_completed": True,
                        },
                        "final_runtime_connectivity_validation": {"ping_success": True},
                    }
                )
            )
            (run_dir / "timing.json").write_text(
                json.dumps(
                    {
                        "timing_summary": {
                            "deadline_miss_count": 1,
                            "deadline_miss_threshold_seconds": 0.05,
                            "deadline_miss_definition": "strict scheduler-boundary miss",
                        },
                        "rows": [
                            {
                                "epoch_type": "runtime_epoch",
                                "activation_relative_time_seconds": 0.0,
                                "activation_lag_seconds": 0.01,
                                "transition_wall_seconds": 1.0,
                                "host_operation_apply_wall_seconds": 0.5,
                                "node_local_apply_wall_seconds": 0.2,
                            },
                            {
                                "epoch_type": "runtime_epoch",
                                "activation_relative_time_seconds": 30.0,
                                "activation_lag_seconds": 0.20,
                                "transition_wall_seconds": 4.0,
                                "host_operation_apply_wall_seconds": 2.0,
                                "node_local_apply_wall_seconds": 0.4,
                            },
                            {
                                "epoch_type": "parameter_epoch",
                                "activation_relative_time_seconds": 60.0,
                                "activation_lag_seconds": 0.03,
                                "transition_wall_seconds": 0.5,
                                "parameter_qdisc_apply_seconds": 0.3,
                            },
                        ],
                    }
                )
            )
            (run_dir / "events.jsonl").write_text(
                json.dumps({"event_type": "runtime_epoch_result", "ping_success": True})
                + "\n"
            )
            (run_dir / "temporal_audit.json").write_text(
                json.dumps({"no_future_active_link_violation": True})
            )
            (run_dir / "path_observation_results.json").write_text(
                json.dumps({"paths": [{"path_hop_count": 1}, {"path_hop_count": 5}]})
            )
            (run_dir / "post_run_preflight.json").write_text(
                json.dumps(
                    {
                        "assessment": {"ready": True, "blocking_reasons": [], "warning_reasons": []},
                        "clean_after_run": True,
                        "compose_residue_summary": {"compose_project_container_count": 0},
                    }
                )
            )
            gz_path = root / "gz.json"
            gz_path.write_text(
                json.dumps(
                    {
                        "passed": True,
                        "state_counts": {
                            "shared_gz_model_after": {"ut_mpls_egress_count": 0}
                        },
                        "shared_return_groups": [{"target_key": "gw:zone:sat"}],
                    }
                )
            )
            current_preflight = root / "preflight.json"
            current_preflight.write_text(
                json.dumps(
                    {
                        "assessment": {
                            "ready": True,
                            "blocking_reasons": [],
                            "warning_reasons": ["host_mpls_not_ready"],
                        },
                        "compose_residue_summary": {"compose_project_container_count": 0},
                    }
                )
            )

            summary = build_summary(
                run_dir,
                gz_semantics=gz_path,
                current_preflight=current_preflight,
            )

        self.assertEqual(summary["event_time_duration_seconds"], 60.0)
        self.assertEqual(summary["epoch_counts"]["runtime_epoch_count"], 2)
        self.assertEqual(
            summary["runtime_epoch_timing"]["activation_lag_seconds"]["p95"],
            0.2,
        )
        self.assertEqual(summary["deadline_miss"]["count"], 1)
        self.assertTrue(summary["gz_semantics"]["passed"])
        self.assertEqual(len(summary["gz_semantics"]["shared_return_groups"]), 1)
        self.assertEqual(summary["long_hop_count_gt4"], 1)
        self.assertTrue(summary["preflight"]["run_local_post_run"]["clean_after_run"])
        self.assertEqual(
            summary["preflight"]["current_environment"]["warning_reasons"],
            ["host_mpls_not_ready"],
        )


if __name__ == "__main__":
    unittest.main()
