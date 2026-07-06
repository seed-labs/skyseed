#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from network_runtime import DirectNetnsVethSlotLinkNamespaceQdiscInstaller
from network_runtime import runtime_parameter_update_requires_qdisc
from network_mutation import build_network_mutation_plan
from network_mutation import make_runtime_parameter_mutation_policy


class QdiscLifecycleTests(unittest.TestCase):
    def _installer_with_recorded_commands(self) -> tuple[
        DirectNetnsVethSlotLinkNamespaceQdiscInstaller,
        list[list[str]],
    ]:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        commands: list[list[str]] = []

        def fake_run(command, *, allow_failure=False):
            rendered = [str(part) for part in command]
            commands.append(rendered)
            return subprocess.CompletedProcess(rendered, 0, "", "")

        def fake_nsenter(*, node_id, command, allow_failure=False):
            rendered = ["nsenter", str(node_id), *[str(part) for part in command]]
            commands.append(rendered)
            return subprocess.CompletedProcess(rendered, 0, "", "")

        installer._run_command = fake_run  # type: ignore[method-assign]
        installer._nsenter = fake_nsenter  # type: ignore[method-assign]
        return installer, commands

    def test_fresh_startup_deactivate_skips_qdisc_delete_without_residue(self) -> None:
        installer, commands = self._installer_with_recorded_commands()
        slot = {"slot_name": "ut1-access-slot0", "slot_type": "ut_access"}
        resource = {
            "endpoints": [
                {
                    "host_interface_name": "vhs-ut1",
                    "node_id": "ut1",
                    "interface_name": "ua0",
                }
            ]
        }

        installer._deactivate_resource(
            slot=slot,
            resource=resource,
            lifecycle_mode="fresh_startup_install",
            skip_qdisc_no_residue=True,
            skip_link_no_residue=True,
        )

        self.assertFalse(
            any(command[:5] == ["sudo", "-n", "tc", "qdisc", "del"] for command in commands)
        )
        self.assertFalse(any(command and command[0] == "nsenter" for command in commands))
        self.assertFalse(
            any(
                command[:4] == ["sudo", "-n", "sh", "-lc"]
                and "nomaster" in command[-1]
                for command in commands
            )
        )
        metrics = installer.consume_operation_metrics()["qdisc_lifecycle_metrics"]
        self.assertEqual(metrics["qdisc_delete_skipped_no_residue"], 1)
        self.assertEqual(metrics["link_deactivate_skipped_no_residue"], 2)
        self.assertEqual(metrics["addr_flush_skipped_no_residue"], 1)
        self.assertEqual(metrics["nomaster_skipped_no_residue"], 1)
        self.assertNotIn("qdisc_delete_attempted", metrics)

    def test_dirty_recovery_deactivate_attempts_qdisc_delete(self) -> None:
        installer, commands = self._installer_with_recorded_commands()
        slot = {"slot_name": "ut1-access-slot0", "slot_type": "ut_access"}
        resource = {
            "endpoints": [
                {
                    "host_interface_name": "vhs-ut1",
                    "node_id": "ut1",
                    "interface_name": "ua0",
                }
            ]
        }

        installer._deactivate_resource(
            slot=slot,
            resource=resource,
            lifecycle_mode="dirty_recovery_reset",
            skip_qdisc_no_residue=False,
        )

        self.assertTrue(
            any(command[:5] == ["sudo", "-n", "tc", "qdisc", "del"] for command in commands)
        )
        metrics = installer.consume_operation_metrics()["qdisc_lifecycle_metrics"]
        self.assertEqual(metrics["qdisc_delete_attempted"], 1)
        self.assertEqual(metrics["qdisc_delete_required_existing_mismatch"], 1)
        self.assertEqual(metrics["qdisc_delete_mode:dirty_recovery_reset"], 1)
        self.assertEqual(metrics["link_deactivate_attempted"], 2)
        self.assertEqual(metrics["addr_flush_attempted"], 1)
        self.assertEqual(metrics["nomaster_attempted"], 1)
        self.assertEqual(metrics["dirty_recovery_deactivate_count"], 1)

    def test_normal_occupied_to_unoccupied_deactivate_still_runs(self) -> None:
        installer, commands = self._installer_with_recorded_commands()
        slot = {"slot_name": "ut1-access-slot0", "slot_type": "ut_access"}
        resource = {
            "endpoints": [
                {
                    "host_interface_name": "vhs-ut1",
                    "node_id": "ut1",
                    "interface_name": "ua0",
                }
            ]
        }

        installer._deactivate_resource(
            slot=slot,
            resource=resource,
            lifecycle_mode="normal_runtime_update",
            skip_qdisc_no_residue=False,
            skip_link_no_residue=False,
        )

        self.assertTrue(any(command and command[0] == "nsenter" for command in commands))
        self.assertTrue(
            any(
                command[:4] == ["sudo", "-n", "sh", "-lc"]
                and "nomaster" in command[-1]
                for command in commands
            )
        )
        metrics = installer.consume_operation_metrics()["qdisc_lifecycle_metrics"]
        self.assertEqual(metrics["link_deactivate_attempted"], 2)
        self.assertEqual(metrics["addr_flush_attempted"], 1)
        self.assertEqual(metrics["nomaster_attempted"], 1)

    def test_parameter_update_uses_replace_without_delete_for_netem(self) -> None:
        installer, commands = self._installer_with_recorded_commands()

        installer._apply_qdisc(
            interface_name="vhs-ut1",
            runtime_parameters={"delay_ms": 12.5, "bandwidth_mbps": 100.0},
            lifecycle_mode="parameter_only_update",
        )

        self.assertTrue(
            any(command[:5] == ["sudo", "-n", "tc", "qdisc", "replace"] for command in commands)
        )
        self.assertFalse(
            any(command[:5] == ["sudo", "-n", "tc", "qdisc", "del"] for command in commands)
        )
        metrics = installer.consume_operation_metrics()["qdisc_lifecycle_metrics"]
        self.assertEqual(metrics["qdisc_replace_count"], 1)

    def test_unchanged_parameter_qdisc_replace_is_skipped(self) -> None:
        installer, commands = self._installer_with_recorded_commands()

        installer._apply_qdisc(
            interface_name="vhs-ut1",
            runtime_parameters={"delay_ms": 12.5, "bandwidth_mbps": 100.0},
            lifecycle_mode="parameter_only_update",
        )
        installer._apply_qdisc(
            interface_name="vhs-ut1",
            runtime_parameters={"delay_ms": 12.5, "bandwidth_mbps": 100.0},
            lifecycle_mode="parameter_only_update",
        )

        replace_commands = [
            command
            for command in commands
            if command[:5] == ["sudo", "-n", "tc", "qdisc", "replace"]
        ]
        self.assertEqual(len(replace_commands), 1)
        metrics = installer.consume_operation_metrics()["qdisc_lifecycle_metrics"]
        self.assertEqual(metrics["qdisc_replace_count"], 1)
        self.assertEqual(metrics["qdisc_replace_skipped_unchanged"], 1)
        self.assertEqual(metrics["qdisc_replace_skip_mode:parameter_only_update"], 1)

    def test_metadata_only_parameter_update_skips_qdisc_apply(self) -> None:
        installer, _commands = self._installer_with_recorded_commands()
        qdisc_calls: list[dict] = []

        installer._inventory = lambda topology, topology_path: {  # type: ignore[method-assign]
            "ut1-access-slot0": {
                "slot_name": "ut1-access-slot0",
                "slot_type": "ut_access",
            }
        }
        installer._resource_for_binding = lambda *, slot, binding: {  # type: ignore[method-assign]
            "endpoints": [
                {
                    "host_interface_name": "vhs-ut1",
                    "node_id": "ut1",
                    "interface_name": "ua0",
                }
            ]
        }
        installer._participant_host_interfaces = (  # type: ignore[method-assign]
            lambda *, slot, resource: ["vhs-ut1"]
        )
        installer._apply_qdisc = lambda **kwargs: qdisc_calls.append(kwargs)  # type: ignore[method-assign]
        installer._persist_operation_state = lambda **kwargs: None  # type: ignore[method-assign]

        result = installer.apply_operation(
            controller=SimpleNamespace(topology={}, topology_path="topology.json"),
            link_state_path=Path("link_state.json"),
            operation={
                "operation": "update_slot_parameters",
                "slot_name": "ut1-access-slot0",
                "target_binding": {"ut_id": "ut1", "satellite_id": "sat-a"},
                "target_occupied": True,
                "source_runtime_parameters": {
                    "delay_ms": 8.0,
                    "loss_percent": 0.0,
                    "routing_cost": 41,
                    "fec_preference": "primary",
                },
                "target_runtime_parameters": {
                    "delay_ms": 8.0,
                    "loss_percent": 0.0,
                    "routing_cost": 42,
                    "fec_preference": "primary",
                },
            },
            persist_link_state=False,
        )

        self.assertFalse(qdisc_calls)
        self.assertFalse(result["qdisc_required"])
        self.assertEqual(result["qdisc_apply_wall_seconds"], 0.0)

    def test_bpf_owned_qdisc_override_excludes_bpf_owned_loss(self) -> None:
        installer, _commands = self._installer_with_recorded_commands()
        qdisc_calls: list[dict] = []

        installer._inventory = lambda topology, topology_path: {  # type: ignore[method-assign]
            "ut1-access-slot0": {
                "slot_name": "ut1-access-slot0",
                "slot_type": "ut_access",
            }
        }
        installer._resource_for_binding = lambda *, slot, binding: {  # type: ignore[method-assign]
            "endpoints": [
                {
                    "host_interface_name": "vhs-ut1",
                    "node_id": "ut1",
                    "interface_name": "ua0",
                }
            ]
        }
        installer._participant_host_interfaces = (  # type: ignore[method-assign]
            lambda *, slot, resource: ["vhs-ut1"]
        )
        installer._apply_qdisc = lambda **kwargs: qdisc_calls.append(kwargs)  # type: ignore[method-assign]
        installer._persist_operation_state = lambda **kwargs: None  # type: ignore[method-assign]

        result = installer.apply_operation(
            controller=SimpleNamespace(topology={}, topology_path="topology.json"),
            link_state_path=Path("link_state.json"),
            operation={
                "operation": "update_slot_parameters",
                "slot_name": "ut1-access-slot0",
                "target_binding": {"ut_id": "ut1", "satellite_id": "sat-a"},
                "target_occupied": True,
                "source_runtime_parameters": {
                    "delay_ms": 8.0,
                    "loss_percent": 0.0,
                },
                "target_runtime_parameters": {
                    "delay_ms": 9.0,
                    "loss_percent": 1.5,
                },
                "changed_runtime_parameter_fields": ["delay_ms", "loss_percent"],
                "bpf_owned_host_qdisc_runtime_parameters": {"delay_ms": 9.0},
            },
            persist_link_state=False,
        )

        self.assertTrue(result["qdisc_required"])
        self.assertEqual(len(qdisc_calls), 1)
        self.assertEqual(qdisc_calls[0]["runtime_parameters"], {"delay_ms": 9.0})

    def test_qdisc_requirement_uses_changed_parameter_fields(self) -> None:
        self.assertFalse(
            runtime_parameter_update_requires_qdisc(
                {
                    "operation": "update_slot_parameters",
                    "source_runtime_parameters": {
                        "delay_ms": 8.0,
                        "rate_mbps": 50,
                        "routing_cost": 100,
                    },
                    "target_runtime_parameters": {
                        "delay_ms": 8.0,
                        "rate_mbps": 50,
                        "routing_cost": 101,
                    },
                }
            )
        )
        self.assertTrue(
            runtime_parameter_update_requires_qdisc(
                {
                    "operation": "update_slot_parameters",
                    "source_runtime_parameters": {
                        "delay_ms": 8.0,
                        "routing_cost": 100,
                    },
                    "target_runtime_parameters": {
                        "delay_ms": 9.0,
                        "routing_cost": 101,
                    },
                }
            )
        )

    def test_routing_cost_only_plan_suppresses_qdisc_parameter_drift(self) -> None:
        policy = make_runtime_parameter_mutation_policy(
            delay_threshold_ms=0.5,
            loss_threshold_pct=0.02,
            bandwidth_threshold_pct=5.0,
            routing_cost_threshold=5,
        )
        source_state = {
            "isl-a": {
                "slot_name": "isl-a",
                "slot_type": "cross_plane_isl",
                "occupied": True,
                "binding": {"satellite_a_id": "a", "satellite_b_id": "b"},
                "endpoints": [],
                "runtime_parameters": {
                    "delay_ms": 8.0,
                    "loss_percent": 0.0,
                    "bandwidth_mbps": 50.0,
                    "routing_cost": 100,
                },
            }
        }
        snapshot = SimpleNamespace(
            snapshot_id="snap-cost-only",
            time_slice={"slice_id": "slice-cost-only"},
            active_links=[
                {
                    "link_id": "isl-a",
                    "slot_type": "cross_plane_isl",
                    "binding": {"satellite_a_id": "a", "satellite_b_id": "b"},
                    "runtime_parameters": {
                        "delay_ms": 8.1,
                        "loss_percent": 0.0,
                        "bandwidth_mbps": 50.0,
                        "routing_cost": 106,
                    },
                }
            ],
        )

        plan = build_network_mutation_plan(
            source_state=source_state,
            source_state_id="source",
            target_snapshot=snapshot,
            transition_name="source-to-cost-only",
            runtime_parameter_mutation_policy=policy,
        )

        self.assertEqual(len(plan.operations), 1)
        operation = plan.operations[0]
        self.assertEqual(operation["operation"], "update_slot_parameters")
        self.assertEqual(operation["changed_runtime_parameter_fields"], ["routing_cost"])
        self.assertEqual(operation["target_runtime_parameters"]["routing_cost"], 106)
        self.assertEqual(operation["target_runtime_parameters"]["delay_ms"], 8.0)
        self.assertEqual(
            operation["target_runtime_parameter_snapshot"]["delay_ms"],
            8.1,
        )
        self.assertFalse(runtime_parameter_update_requires_qdisc(operation))

    def test_qdisc_plan_keeps_threshold_crossing_parameter_fields(self) -> None:
        policy = make_runtime_parameter_mutation_policy(
            delay_threshold_ms=0.5,
            loss_threshold_pct=0.02,
            bandwidth_threshold_pct=5.0,
            routing_cost_threshold=5,
        )
        source_state = {
            "gw-a": {
                "slot_name": "gw-a",
                "slot_type": "gw_access",
                "occupied": True,
                "binding": {"gateway_id": "gw1", "satellite_id": "sat-a"},
                "endpoints": [],
                "runtime_parameters": {
                    "delay_ms": 8.0,
                    "loss_percent": 0.0,
                    "bandwidth_mbps": 50.0,
                    "routing_cost": 100,
                },
            }
        }
        snapshot = SimpleNamespace(
            snapshot_id="snap-delay",
            time_slice={"slice_id": "slice-delay"},
            active_links=[
                {
                    "link_id": "gw-a",
                    "slot_type": "gw_access",
                    "binding": {"gateway_id": "gw1", "satellite_id": "sat-a"},
                    "runtime_parameters": {
                        "delay_ms": 8.6,
                        "loss_percent": 0.0,
                        "bandwidth_mbps": 50.0,
                        "routing_cost": 106,
                    },
                }
            ],
        )

        plan = build_network_mutation_plan(
            source_state=source_state,
            source_state_id="source",
            target_snapshot=snapshot,
            transition_name="source-to-delay",
            runtime_parameter_mutation_policy=policy,
        )

        operation = plan.operations[0]
        self.assertIn("delay_ms", operation["changed_runtime_parameter_fields"])
        self.assertEqual(operation["target_runtime_parameters"]["delay_ms"], 8.6)
        self.assertTrue(runtime_parameter_update_requires_qdisc(operation))


if __name__ == "__main__":
    unittest.main()
