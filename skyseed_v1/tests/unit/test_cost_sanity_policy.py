from __future__ import annotations

import unittest
from types import SimpleNamespace

from skyseed_v1.runtime.experiment import (
    _cost_sanity_blocker,
    _operational_clean_cost_sanity_summary,
)


class CostSanityPolicyTests(unittest.TestCase):
    def test_elapsed_time_anomalies_warn_without_reclassifying_success(self) -> None:
        inherited = {
            "applicable": True,
            "passed": False,
            "conclusion": "UNKNOWN_BLOCKING_ANOMALY",
            "anomaly_table": [
                {
                    "phase": "timing_buckets",
                    "anomaly": True,
                    "conclusion": "UNKNOWN_BLOCKING_ANOMALY",
                }
            ],
        }
        summary = _operational_clean_cost_sanity_summary(
            args=SimpleNamespace(
                runtime_mode="operational_clean",
                forwarding_backend="bpf_owned",
            ),
            runtime_report={"timing_summary": {"cost_sanity": inherited}},
            timing_analysis={
                "startup_preparation": {
                    "cold_start_before_first_epoch_execution_wall_seconds": 1200.0,
                    "ready_to_execute_epochs_wall_seconds": 700.0,
                }
            },
        )

        self.assertTrue(summary["passed"])
        self.assertEqual(summary["blocking_anomaly_count"], 0)
        self.assertEqual(summary["warning_only_anomaly_count"], 2)
        self.assertTrue(summary["broad_rerun_blocked"])
        self.assertIsNone(_cost_sanity_blocker(summary))

    def test_semantic_cost_anomaly_remains_blocking_when_time_also_warns(self) -> None:
        summary = {
            "passed": False,
            "conclusion": "STARTUP_COST_ANOMALY",
            "anomaly_count": 2,
            "policy": {
                "warning_only_conclusions": ["STARTUP_COST_ANOMALY"],
            },
            "anomaly_table": [
                {
                    "phase": "startup_materialization",
                    "anomaly": True,
                    "conclusion": "STARTUP_COST_ANOMALY",
                },
                {
                    "phase": "command_cardinality",
                    "anomaly": True,
                    "conclusion": "COMMAND_CARDINALITY_ANOMALY",
                },
            ],
        }

        blocker = _cost_sanity_blocker(summary)

        self.assertIsNotNone(blocker)
        self.assertEqual(
            blocker["classified_blocker"], "COMMAND_CARDINALITY_ANOMALY"
        )
        self.assertEqual(
            blocker["first_anomaly"]["phase"], "command_cardinality"
        )


if __name__ == "__main__":
    unittest.main()
