from __future__ import annotations

import unittest

from skyseed_v1.observability.operation_metrics import (
    RuntimeOperationMetrics,
    classify_node_local_command_scope,
    classify_runtime_command,
)


class OperationMetricTests(unittest.TestCase):
    def test_commands_are_classified_without_execution(self) -> None:
        self.assertEqual(
            classify_runtime_command(["ip", "route", "replace", "10.0.0.0/24"]),
            {"family": "route", "operation": "route_replace"},
        )
        scoped = classify_node_local_command_scope(
            ["ip", "-f", "mpls", "route", "replace", "100"],
            description="forward LFIB",
        )
        self.assertEqual(scoped["scope"], "satellite_core_mpls_lfib")

    def test_metrics_record_observation_but_do_not_apply_commands(self) -> None:
        metrics = RuntimeOperationMetrics()
        metrics.record_command(
            ["tc", "qdisc", "replace", "dev", "eth0"],
            wall_seconds=0.25,
            returncode=0,
        )
        report = metrics.snapshot()
        self.assertEqual(report["command_count"], 1)
        self.assertEqual(report["success_count"], 1)
        self.assertEqual(report["counts_by_family"], {"qdisc": 1})


if __name__ == "__main__":
    unittest.main()
