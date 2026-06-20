#!/usr/bin/env python3
# encoding: utf-8

import unittest
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "apps"))

import linux_mpls_runtime
from run_starlink_shell_experiment import _aggregate_node_local_cleanup_reports
from linux_mpls_runtime import (
    NodeLocalBatchResult,
    RuntimeCommandSpec,
    _execute_node_local_batches,
    _group_node_local_batches,
    _node_local_batch_cleanup_metrics,
)
from network_runtime import DirectNetnsVethSlotLinkNamespaceQdiscInstaller


class _FakeCleanupInstaller(DirectNetnsVethSlotLinkNamespaceQdiscInstaller):
    def __init__(self) -> None:
        super().__init__()
        self.host_interfaces = {"vh1", "vt1", "br1"}
        self.container_interfaces = {"node-a": {"eth9"}}

    def _load_topology(self, topology_path: Path) -> dict[str, Any]:
        return {}

    def _runtime_artifact_inventory(
        self,
        *,
        topology: dict[str, Any],
        topology_path: Path,
    ) -> dict[str, Any]:
        return {
            "temp_host_interface_names": ["vt1", "vt1", "missing-temp"],
            "host_interface_names": ["vh1", "vh1", "missing-host"],
            "container_interface_names": [
                {"node_id": "node-a", "interface_name": "eth9"},
                {"node_id": "node-a", "interface_name": "eth9"},
                {"node_id": "node-b", "interface_name": "eth9"},
            ],
            "bridge_names": ["br1", "br1", "missing-br"],
            "slot_counts_by_type": {},
            "candidate_resource_counts_by_type": {},
            "candidate_endpoint_counts_by_type": {},
            "preconnected_endpoint_counts_by_type": {},
        }

    def _host_interface_name_set(self) -> set[str]:
        return set(self.host_interfaces)

    def _container_interface_name_set(self, *, node_id: str) -> set[str]:
        return set(self.container_interfaces.get(node_id, set()))

    def _host_interface_exists(
        self,
        interface_name: str,
        *,
        known_interfaces: set[str] | None = None,
    ) -> bool:
        return interface_name in (known_interfaces if known_interfaces is not None else self.host_interfaces)

    def _container_interface_exists(
        self,
        *,
        node_id: str,
        interface_name: str,
        known_interfaces: set[str] | None = None,
    ) -> bool:
        return interface_name in (
            known_interfaces
            if known_interfaces is not None
            else self.container_interfaces.get(node_id, set())
        )

    def _bridge_exists(
        self,
        bridge_name: str,
        *,
        known_interfaces: set[str] | None = None,
    ) -> bool:
        return self._host_interface_exists(bridge_name, known_interfaces=known_interfaces)

    def _delete_host_interface_if_present(
        self,
        interface_name: str,
        *,
        known_interfaces: set[str] | None = None,
    ) -> None:
        self.host_interfaces.discard(interface_name)
        if known_interfaces is not None:
            known_interfaces.discard(interface_name)

    def _delete_container_interface_if_present(
        self,
        *,
        node_id: str,
        interface_name: str,
        known_interfaces: set[str] | None = None,
    ) -> None:
        self.container_interfaces.setdefault(node_id, set()).discard(interface_name)
        if known_interfaces is not None:
            known_interfaces.discard(interface_name)

    def _run_command(
        self,
        command: list[str] | tuple[str, ...],
        *,
        allow_failure: bool = False,
    ) -> Any:
        if "br1" in command:
            self.host_interfaces.discard("br1")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()


class RollbackCleanupOptimizationTests(unittest.TestCase):
    def test_node_local_duplicate_cleanup_is_grouped_and_reported(self) -> None:
        specs = [
            RuntimeCommandSpec(
                node_id="sat-a",
                phase="rollback",
                description="delete route",
                command=("ip", "route", "del", "198.51.100.1/32"),
                allow_failure=True,
            ),
            RuntimeCommandSpec(
                node_id="sat-a",
                phase="rollback",
                description="delete route duplicate",
                command=("ip", "route", "del", "198.51.100.1/32"),
                allow_failure=True,
            ),
            RuntimeCommandSpec(
                node_id="sat-b",
                phase="rollback",
                description="delete label",
                command=("ip", "-f", "mpls", "route", "del", "16001"),
                allow_failure=True,
            ),
        ]

        batches = _group_node_local_batches(specs)
        metrics = _node_local_batch_cleanup_metrics(
            requested_specs=specs,
            batches=batches,
            max_workers=8,
        )

        self.assertEqual(sum(batch.command_count for batch in batches), 2)
        self.assertEqual(metrics["requested_command_count"], 3)
        self.assertEqual(metrics["grouped_command_count"], 2)
        self.assertEqual(metrics["duplicate_cleanup_skipped_count"], 1)
        self.assertEqual(metrics["skipped_noop_count"], 1)
        self.assertEqual(metrics["node_local_rollback_worker_count"], 2)

    def test_bounded_parallel_preserves_batch_result_order(self) -> None:
        specs = [
            RuntimeCommandSpec(
                node_id=f"sat-{index}",
                phase="rollback",
                description=f"delete route {index}",
                command=("ip", "route", "del", f"198.51.100.{index}/32"),
                allow_failure=True,
            )
            for index in range(3)
        ]
        batches = _group_node_local_batches(specs)

        def fake_run(batch: Any) -> NodeLocalBatchResult:
            return NodeLocalBatchResult(
                node_id=batch.node_id,
                phase=batch.phase,
                command_count=batch.command_count,
                descriptions=tuple(spec.description for spec in batch.command_specs),
                returncode=0,
                stdout="",
                stderr="",
                wall_seconds=0.01,
            )

        with patch.object(linux_mpls_runtime, "_run_node_local_batch", fake_run):
            results = _execute_node_local_batches(batches, max_workers=3)

        self.assertEqual([result.node_id for result in results], ["sat-0", "sat-1", "sat-2"])
        self.assertEqual(sum(result.command_count for result in results), 3)

    def test_node_local_cleanup_report_aggregation_keeps_worker_metrics(self) -> None:
        metrics = _aggregate_node_local_cleanup_reports(
            [
                {
                    "cleanup_metrics": {
                        "requested_command_count": 4,
                        "grouped_command_count": 3,
                        "duplicate_cleanup_skipped_count": 1,
                        "skipped_noop_count": 1,
                        "node_local_rollback_worker_count": 2,
                        "bounded_parallel_worker_count": 2,
                        "node_local_command_batch_count": 2,
                    },
                    "rollback_results": [
                        {"success": True, "command_count": 2},
                        {"success": False, "command_count": 1},
                    ],
                }
            ]
        )

        self.assertEqual(metrics["requested_command_count"], 4)
        self.assertEqual(metrics["grouped_command_count"], 3)
        self.assertEqual(metrics["duplicate_cleanup_skipped_count"], 1)
        self.assertEqual(metrics["skipped_noop_count"], 1)
        self.assertEqual(metrics["node_local_rollback_worker_count"], 2)
        self.assertEqual(metrics["rollback_attempted_count"], 3)
        self.assertEqual(metrics["rollback_succeeded_count"], 2)
        self.assertEqual(metrics["rollback_failed_count"], 1)

    def test_host_artifact_cleanup_dedupes_and_reports_noops(self) -> None:
        installer = _FakeCleanupInstaller()

        summary = installer.cleanup_runtime_artifacts(
            topology_path=Path("topology.json"),
            link_state_path=Path("link_state.json"),
        )

        metrics = summary["cleanup_metrics"]
        self.assertTrue(summary["cleanup_completed"])
        self.assertEqual(metrics["duplicate_cleanup_skipped_count"], 4)
        self.assertEqual(metrics["skipped_noop_count"], 4)
        self.assertEqual(metrics["cleanup_resource_attempted_count"], 4)
        self.assertEqual(metrics["cleanup_resource_succeeded_count"], 4)
        self.assertEqual(summary["removed_host_interface_count"], 1)
        self.assertEqual(summary["removed_temp_host_interface_count"], 1)
        self.assertEqual(summary["removed_container_interface_count"], 1)
        self.assertEqual(summary["removed_bridge_count"], 1)


if __name__ == "__main__":
    unittest.main()
