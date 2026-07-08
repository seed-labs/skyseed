#!/usr/bin/env python3
# encoding: utf-8

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "apps"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_skyseed_cost_attribution import build_cost_attribution
from runtime_operation_metrics import classify_runtime_command


class CostAttributionInstrumentationTests(unittest.TestCase):
    def test_tc_batch_classifies_as_qdisc_batch(self) -> None:
        self.assertEqual(
            classify_runtime_command(
                ["sudo", "-n", "tc", "-batch", "/tmp/skyseed-qdisc-batch.tc"]
            ),
            {"family": "qdisc", "operation": "qdisc_batch"},
        )

    def _write_base_run(self, run_dir: Path, *, typed: bool) -> None:
        (run_dir / "run_manifest.json").write_text(
            json.dumps(
                {
                    "run_id": "unit-cost-run",
                    "continuous_operation_summary": {
                        "replenishment_segment_count": 1,
                    },
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "timing.json").write_text(
            json.dumps(
                {
                    "rows": [
                        {
                            "epoch_type": "runtime_epoch",
                            "transition_wall_seconds": 3.0,
                            "host_operation_apply_wall_seconds": 1.0,
                            "node_local_apply_wall_seconds": 0.5,
                            "node_local_incremental_cleanup_wall_seconds": 0.0,
                            "network_mutation_plan_wall_seconds": 1.1,
                            "audit_write_seconds": 0.01,
                            "artifact_write_blocking_seconds": 0.02,
                        },
                        {
                            "epoch_type": "parameter_epoch",
                            "transition_wall_seconds": 0.8,
                            "host_operation_apply_wall_seconds": 0.4,
                            "parameter_qdisc_apply_seconds": 0.7,
                            "parameter_metadata_update_seconds": 0.01,
                            "parameter_dirty_reconcile_seconds": 0.001,
                        },
                    ],
                    "phase_timing": {"post_run_preflight_wall_seconds": 0.25},
                }
            ),
            encoding="utf-8",
        )
        runtime_record = {
            "epoch_type": "runtime_epoch",
            "slice_id": "slice-a",
            "network_mutation_count": 2,
            "host_operation_timing_summary": {
                "operation_counts": {"activate_slot_attachment": 1},
                "delta_category_counts": {"created": 1},
                "slot_type_counts": {"ut_access": 1},
            },
            "timing": {
                "host_operation_apply_wall_seconds": 1.0,
                "node_local_apply_wall_seconds": 0.5,
            },
        }
        parameter_record = {
            "epoch_type": "parameter_epoch",
            "slice_id": "slice-b",
            "network_mutation_count": 1,
            "host_operation_timing_summary": {
                "operation_counts": {"update_slot_parameters": 1},
                "delta_category_counts": {"changed": 1},
                "slot_type_counts": {"gw_access": 1},
            },
            "trigger_reasons": ["routing_cost_changed"],
        }
        node_metrics = {
            "command_count": 2,
            "success_count": 2,
            "failure_count": 0,
            "counts_by_family": {"route": 1, "iptables": 1},
            "counts_by_operation": {"route_replace": 1, "iptables_add": 1},
            "total_wall_seconds": 0.0,
            "batch_count": 1,
            "batch_wall_seconds": 0.12,
            "per_command_timing_available": False,
        }
        host_metrics = {
            "command_count": 3,
            "success_count": 3,
            "failure_count": 0,
            "counts_by_family": {"qdisc": 1, "veth": 2},
            "counts_by_operation": {"qdisc_replace": 1, "veth_up": 2},
            "wall_seconds_by_family": {"qdisc": 0.02, "veth": 0.03},
            "wall_seconds_by_operation": {"qdisc_replace": 0.02, "veth_up": 0.03},
            "returncode_counts": {"0": 3},
            "total_wall_seconds": 0.05,
        }
        if typed:
            runtime_record["host_typed_operation_metrics"] = host_metrics
            runtime_record["node_local_execution"] = {
                "timing": {"apply_typed_operation_metrics": node_metrics}
            }
            parameter_record["host_typed_operation_metrics"] = host_metrics
        (run_dir / "epoch_execution_details.jsonl").write_text(
            "\n".join(json.dumps(row) for row in [runtime_record, parameter_record]) + "\n",
            encoding="utf-8",
        )
        final_cleanup = {
            "all_cleanup_success": True,
            "node_local": {
                "session_cleanup_count": 1,
                "rollback_results": [{"phase": "rollback", "command_count": 2, "wall_seconds": 0.3}],
                "timing": {"rollback_wall_seconds": 0.3},
            },
            "host_rollbacks": [
                {
                    "timing": {"rollback_wall_seconds": 0.4},
                    "outcome": {
                        "operation_count": 1,
                        "operation_timing_summary": {"total_wall_seconds": 0.4},
                    },
                }
            ],
            "host_artifact_cleanup": {
                "removed_bridge_count": 1,
                "removed_host_interface_count": 2,
                "remaining_bridge_count": 0,
                "remaining_host_interface_count": 0,
                "timing": {"cleanup_wall_seconds": 0.2},
            },
        }
        if typed:
            final_cleanup["node_local"]["timing"]["rollback_typed_operation_metrics"] = node_metrics
            final_cleanup["host_rollbacks"][0]["outcome"]["typed_operation_metrics"] = host_metrics
            final_cleanup["host_artifact_cleanup"]["typed_operation_metrics"] = host_metrics
        (run_dir / "real_runtime_report.json").write_text(
            json.dumps({"final_cleanup": final_cleanup}),
            encoding="utf-8",
        )

    def test_old_artifact_without_typed_metrics_reports_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            self._write_base_run(run_dir, typed=False)
            report = build_cost_attribution(run_dir)

        self.assertFalse(report["runtime_epoch_cost"]["host_typed_operation_metrics"]["available"])
        self.assertEqual(
            report["runtime_epoch_cost"]["host_typed_operation_metrics"]["missing_record_count"],
            1,
        )
        self.assertFalse(
            report["rollback_cleanup"]["host_rollback_typed_operation_metrics"]["available"]
        )

    def test_new_typed_metrics_are_summarized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            self._write_base_run(run_dir, typed=True)
            report = build_cost_attribution(run_dir)

        runtime_metrics = report["runtime_epoch_cost"]["host_typed_operation_metrics"]
        self.assertTrue(runtime_metrics["available"])
        self.assertEqual(runtime_metrics["command_count"], 3)
        self.assertEqual(runtime_metrics["counts_by_operation"]["qdisc_replace"], 1)
        node_metrics = report["runtime_epoch_cost"]["node_local_apply_typed_operation_metrics"]
        self.assertEqual(node_metrics["counts_by_family"]["iptables"], 1)
        cleanup_metrics = report["rollback_cleanup"]["host_rollback_typed_operation_metrics"]
        self.assertEqual(cleanup_metrics["available_record_count"], 1)


if __name__ == "__main__":
    unittest.main()
