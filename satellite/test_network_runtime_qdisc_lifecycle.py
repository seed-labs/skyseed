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
from network_runtime import InProcessHostRuntimeManager
from network_runtime import NetworkMutationExecutionError
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
        qdisc_batch_payloads: list[str] = []
        installer._test_qdisc_batch_payloads = qdisc_batch_payloads  # type: ignore[attr-defined]

        def fake_run(command, *, allow_failure=False):
            rendered = [str(part) for part in command]
            commands.append(rendered)
            if rendered[:4] == ["sudo", "-n", "tc", "-batch"]:
                qdisc_batch_payloads.append(Path(rendered[4]).read_text())
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
            any(command[:4] == ["sudo", "-n", "tc", "-batch"] for command in commands)
        )
        self.assertIn(
            "qdisc del dev vhs-ut1 root",
            installer._test_qdisc_batch_payloads[0],  # type: ignore[attr-defined]
        )
        metrics = installer.consume_operation_metrics()["qdisc_lifecycle_metrics"]
        self.assertEqual(metrics["qdisc_delete_attempted"], 1)
        self.assertEqual(metrics["qdisc_batch_command_count"], 1)
        self.assertEqual(metrics["qdisc_batch_delete_sub_update_count"], 1)
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

    def test_structural_activate_batches_qdisc_after_interfaces_are_up(self) -> None:
        installer, commands = self._installer_with_recorded_commands()
        slot = {"slot_name": "ut1-access-slot0", "slot_type": "ut_access"}
        resource = {
            "resource_key": "resource-1",
            "endpoints": [
                {
                    "host_interface_name": "vhs-ut1",
                    "node_id": "ut1",
                    "interface_name": "ua0",
                    "ip_address": "10.0.0.2/31",
                }
            ],
        }
        installer._prepared_resource_keys_by_slot = {  # type: ignore[attr-defined]
            "ut1-access-slot0": {"resource-1"}
        }

        summary = installer._activate_resource(
            slot=slot,
            resource=resource,
            runtime_parameters={"delay_ms": 12.5, "bandwidth_mbps": 100.0},
            probe=False,
        )

        host_up_index = next(
            index
            for index, command in enumerate(commands)
            if command[:4] == ["sudo", "-n", "sh", "-lc"]
            and " master " in command[-1]
        )
        ns_up_index = next(
            index for index, command in enumerate(commands) if command[:1] == ["nsenter"]
        )
        batch_index = next(
            index
            for index, command in enumerate(commands)
            if command[:4] == ["sudo", "-n", "tc", "-batch"]
        )
        self.assertLess(host_up_index, batch_index)
        self.assertLess(ns_up_index, batch_index)
        self.assertEqual(summary["qdisc_shell_command_count"], 1)
        self.assertEqual(summary["qdisc_sub_update_count"], 1)
        self.assertIn(
            "qdisc replace dev vhs-ut1 root netem delay 12.500ms rate 100.000000mbit",
            installer._test_qdisc_batch_payloads[0],  # type: ignore[attr-defined]
        )

    def test_structural_deactivate_batches_qdisc_before_links_are_down(self) -> None:
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

        summary = installer._deactivate_resource(
            slot=slot,
            resource=resource,
            lifecycle_mode="normal_runtime_update",
        )

        batch_index = next(
            index
            for index, command in enumerate(commands)
            if command[:4] == ["sudo", "-n", "tc", "-batch"]
        )
        ns_down_index = next(
            index for index, command in enumerate(commands) if command[:1] == ["nsenter"]
        )
        host_down_index = next(
            index
            for index, command in enumerate(commands)
            if command[:4] == ["sudo", "-n", "sh", "-lc"]
            and "nomaster" in command[-1]
        )
        self.assertLess(batch_index, ns_down_index)
        self.assertLess(batch_index, host_down_index)
        self.assertEqual(summary["qdisc_delete_sub_update_count"], 1)

    def test_rebind_batches_delete_before_replace_without_cross_stage_merge(self) -> None:
        installer, commands = self._installer_with_recorded_commands()
        slot = {"slot_name": "ut1-access-slot0", "slot_type": "ut_access"}
        source_resource = {
            "endpoints": [
                {
                    "host_interface_name": "vhs-old",
                    "node_id": "ut1",
                    "interface_name": "ua0",
                }
            ]
        }
        target_resource = {
            "resource_key": "target-resource",
            "endpoints": [
                {
                    "host_interface_name": "vhs-new",
                    "node_id": "ut1",
                    "interface_name": "ua1",
                    "ip_address": "10.0.0.4/31",
                }
            ],
        }
        installer._inventory = lambda topology, topology_path: {  # type: ignore[method-assign]
            "ut1-access-slot0": slot
        }
        installer._resource_for_binding = (  # type: ignore[method-assign]
            lambda *, slot, binding: target_resource
            if binding.get("satellite_id") == "new"
            else source_resource
        )
        installer._prepared_resource_keys_by_slot = {  # type: ignore[attr-defined]
            "ut1-access-slot0": {"target-resource"}
        }
        installer._persist_operation_state = lambda **kwargs: None  # type: ignore[method-assign]
        installer._probe_activated_resource = lambda **kwargs: None  # type: ignore[method-assign]

        result = installer.apply_operation(
            controller=SimpleNamespace(topology={}, topology_path="topology.json"),
            link_state_path=Path("link_state.json"),
            operation={
                "operation": "rebind_slot_attachment",
                "slot_name": "ut1-access-slot0",
                "source_binding": {"ut_id": "ut1", "satellite_id": "old"},
                "target_binding": {"ut_id": "ut1", "satellite_id": "new"},
                "target_occupied": True,
                "target_runtime_parameters": {"delay_ms": 7.0},
            },
            persist_link_state=False,
        )

        batch_payloads = installer._test_qdisc_batch_payloads  # type: ignore[attr-defined]
        self.assertEqual(len(batch_payloads), 2)
        self.assertIn("qdisc del dev vhs-old root", batch_payloads[0])
        self.assertIn(
            "qdisc replace dev vhs-new root netem delay 7.000ms",
            batch_payloads[1],
        )
        self.assertEqual(result["qdisc_batch_command_count"], 2)
        self.assertEqual(result["qdisc_sub_update_count"], 2)

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
        def fake_apply_qdisc_replacements_batch(**kwargs):
            qdisc_calls.append(kwargs)
            return installer._empty_qdisc_batch_summary(
                lifecycle_mode=kwargs.get("lifecycle_mode", "")
            )

        installer._apply_qdisc_replacements_batch = fake_apply_qdisc_replacements_batch  # type: ignore[method-assign]
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

    def test_parameter_group_batches_multiple_qdisc_updates_once(self) -> None:
        installer, commands = self._installer_with_recorded_commands()
        slot_inventory = {
            "gw1-access-slot0": {
                "slot_name": "gw1-access-slot0",
                "slot_type": "gw_access",
            },
            "ut1-access-slot0": {
                "slot_name": "ut1-access-slot0",
                "slot_type": "ut_access",
            },
        }
        resources = {
            "gw1-access-slot0": {
                "endpoints": [{"host_interface_name": "vhs-gw1"}],
            },
            "ut1-access-slot0": {
                "endpoints": [{"host_interface_name": "vhs-ut1"}],
            },
        }
        installer._inventory = lambda topology, topology_path: slot_inventory  # type: ignore[method-assign]
        installer._resource_for_binding = (  # type: ignore[method-assign]
            lambda *, slot, binding: resources[str(slot["slot_name"])]
        )
        state_by_name: dict[str, dict] = {}

        results = installer.apply_parameter_update_operations(
            controller=SimpleNamespace(topology={}, topology_path="topology.json"),
            link_state_path=Path("link_state.json"),
            operations=[
                {
                    "operation": "update_slot_parameters",
                    "slot_name": "gw1-access-slot0",
                    "slot_type": "gw_access",
                    "target_binding": {"gateway_id": "gw1", "satellite_id": "sat-a"},
                    "target_occupied": True,
                    "source_runtime_parameters": {"delay_ms": 8.0},
                    "target_runtime_parameters": {"delay_ms": 9.0},
                    "changed_runtime_parameter_fields": ["delay_ms"],
                },
                {
                    "operation": "update_slot_parameters",
                    "slot_name": "ut1-access-slot0",
                    "slot_type": "ut_access",
                    "target_binding": {"ut_id": "ut1", "satellite_id": "sat-b"},
                    "target_occupied": True,
                    "source_runtime_parameters": {"delay_ms": 4.0},
                    "target_runtime_parameters": {"delay_ms": 5.0},
                    "changed_runtime_parameter_fields": ["delay_ms"],
                },
            ],
            state_by_name=state_by_name,
            persist_link_state=False,
        )

        batch_commands = [
            command
            for command in commands
            if command[:4] == ["sudo", "-n", "tc", "-batch"]
        ]
        self.assertEqual(len(batch_commands), 1)
        payload = installer._test_qdisc_batch_payloads[0]  # type: ignore[attr-defined]
        self.assertIn("qdisc replace dev vhs-gw1 root netem delay 9.000ms", payload)
        self.assertIn("qdisc replace dev vhs-ut1 root netem delay 5.000ms", payload)
        self.assertEqual(sum(result["qdisc_shell_command_count"] for result in results), 1)
        self.assertEqual(sum(result["qdisc_sub_update_count"] for result in results), 2)
        self.assertEqual(sum(result["qdisc_batch_command_count"] for result in results), 1)
        self.assertEqual(
            sum(result["qdisc_batch_sub_update_count"] for result in results),
            2,
        )
        self.assertEqual(set(state_by_name), {"gw1-access-slot0", "ut1-access-slot0"})

    def test_parallel_mixed_group_batches_parameter_updates_as_one_task(self) -> None:
        class FakeInstaller:
            supports_batched_link_state_persist = False
            supports_qdisc_parameter_batch = True

            def __init__(self) -> None:
                self.parameter_batches: list[list[str]] = []
                self.single_operations: list[str] = []

            def apply_parameter_update_operations(self, **kwargs):
                operations = list(kwargs["operations"])
                self.parameter_batches.append(
                    [str(operation["slot_name"]) for operation in operations]
                )
                return [
                    {
                        "slot_name": str(operation["slot_name"]),
                        "operation": "update_slot_parameters",
                        "qdisc_shell_command_count": 1 if index == 0 else 0,
                        "qdisc_sub_update_count": 2,
                        "qdisc_batch_command_count": 1 if index == 0 else 0,
                        "qdisc_batch_sub_update_count": 2,
                        "qdisc_batch_used": True,
                    }
                    for index, operation in enumerate(operations)
                ]

            def apply_operation(self, **kwargs):
                operation = dict(kwargs["operation"])
                self.single_operations.append(str(operation["slot_name"]))
                return {
                    "slot_name": str(operation["slot_name"]),
                    "operation": str(operation["operation"]),
                }

        installer = FakeInstaller()
        manager = InProcessHostRuntimeManager(max_host_workers=4, installer=installer)
        operations = [
            {"operation": "rebind_slot_attachment", "slot_name": "rebind-a"},
            {"operation": "update_slot_parameters", "slot_name": "param-a"},
            {"operation": "update_slot_parameters", "slot_name": "param-b"},
            {"operation": "rebind_slot_attachment", "slot_name": "rebind-b"},
        ]

        apply_results, applied_operations, _verification, _timing, _summary = (
            manager._apply_operations(
                controller=SimpleNamespace(topology={}, topology_path="topology.json"),
                link_state_path=Path("link_state.json"),
                operations=operations,
                working_state={},
                parallelism={
                    "parallel_execution_used": True,
                    "execution_groups": [
                        {"group_index": 0, "operation_indexes": [0, 1, 2, 3]}
                    ],
                    "requested_workers": 4,
                    "effective_workers": 4,
                    "reason": "test",
                },
            )
        )

        self.assertEqual(installer.parameter_batches, [["param-a", "param-b"]])
        self.assertEqual(set(installer.single_operations), {"rebind-a", "rebind-b"})
        self.assertEqual([result["slot_name"] for result in apply_results], [
            "rebind-a",
            "param-a",
            "param-b",
            "rebind-b",
        ])
        self.assertEqual([operation["slot_name"] for operation in applied_operations], [
            "rebind-a",
            "param-a",
            "param-b",
            "rebind-b",
        ])

    def test_serial_mixed_sequence_batches_only_contiguous_parameter_runs(self) -> None:
        class FakeInstaller:
            supports_batched_link_state_persist = False
            supports_qdisc_parameter_batch = True

            def __init__(self) -> None:
                self.call_order: list[tuple[str, list[str]]] = []

            def apply_parameter_update_operations(self, **kwargs):
                operations = list(kwargs["operations"])
                slot_names = [str(operation["slot_name"]) for operation in operations]
                self.call_order.append(("batch", slot_names))
                return [
                    {
                        "slot_name": slot_name,
                        "operation": "update_slot_parameters",
                    }
                    for slot_name in slot_names
                ]

            def apply_operation(self, **kwargs):
                operation = dict(kwargs["operation"])
                slot_name = str(operation["slot_name"])
                self.call_order.append(("single", [slot_name]))
                return {
                    "slot_name": slot_name,
                    "operation": str(operation["operation"]),
                }

        installer = FakeInstaller()
        manager = InProcessHostRuntimeManager(max_host_workers=1, installer=installer)
        operations = [
            {"operation": "update_slot_parameters", "slot_name": "param-a"},
            {"operation": "update_slot_parameters", "slot_name": "param-b"},
            {"operation": "rebind_slot_attachment", "slot_name": "rebind-a"},
            {"operation": "update_slot_parameters", "slot_name": "param-c"},
        ]

        manager._apply_operations(
            controller=SimpleNamespace(topology={}, topology_path="topology.json"),
            link_state_path=Path("link_state.json"),
            operations=operations,
            working_state={},
            parallelism={
                "parallel_execution_used": False,
                "execution_groups": [],
                "requested_workers": 1,
                "effective_workers": 1,
                "reason": "test",
            },
        )

        self.assertEqual(
            installer.call_order,
            [
                ("batch", ["param-a", "param-b"]),
                ("single", ["rebind-a"]),
                ("batch", ["param-c"]),
            ],
        )

    def test_operation_group_batches_structural_rebind_as_delete_then_replace(self) -> None:
        installer, commands = self._installer_with_recorded_commands()
        probes: list[str] = []
        slots = {
            "slot-a": {"slot_name": "slot-a", "slot_type": "ut_access"},
            "slot-b": {"slot_name": "slot-b", "slot_type": "ut_access"},
        }

        def resource_for_binding(*, slot, binding):
            name = str(binding["name"])
            return {
                "resource_key": name,
                "endpoints": [
                    {
                        "host_interface_name": f"vh-{name}",
                        "node_id": f"node-{name}",
                        "interface_name": "eth-edge",
                        "ip_address": "10.0.0.2/31",
                    }
                ],
            }

        installer._inventory = lambda topology, topology_path: slots  # type: ignore[method-assign]
        installer._resource_for_binding = resource_for_binding  # type: ignore[method-assign]
        installer._prepared_resource_keys_by_slot = {  # type: ignore[attr-defined]
            "slot-a": {"target-a"},
            "slot-b": {"target-b"},
        }
        installer._probe_activated_resource = (  # type: ignore[method-assign]
            lambda *, slot, resource: probes.append(str(slot["slot_name"]))
        )

        results = installer.apply_operation_group(
            controller=SimpleNamespace(topology={}, topology_path="topology.json"),
            link_state_path=Path("link_state.json"),
            operations=[
                {
                    "operation": "rebind_slot_attachment",
                    "slot_name": "slot-a",
                    "source_binding": {"name": "source-a"},
                    "target_binding": {"name": "target-a"},
                    "target_occupied": True,
                    "target_runtime_parameters": {"delay_ms": 5.0},
                },
                {
                    "operation": "rebind_slot_attachment",
                    "slot_name": "slot-b",
                    "source_binding": {"name": "source-b"},
                    "target_binding": {"name": "target-b"},
                    "target_occupied": True,
                    "target_runtime_parameters": {"delay_ms": 7.0},
                },
            ],
            persist_link_state=False,
        )

        batch_commands = [
            (index, command)
            for index, command in enumerate(commands)
            if command[:4] == ["sudo", "-n", "tc", "-batch"]
        ]
        self.assertEqual(len(batch_commands), 2)
        delete_payload = installer._test_qdisc_batch_payloads[0]  # type: ignore[attr-defined]
        replace_payload = installer._test_qdisc_batch_payloads[1]  # type: ignore[attr-defined]
        self.assertIn("qdisc del dev vh-source-a root", delete_payload)
        self.assertIn("qdisc del dev vh-source-b root", delete_payload)
        self.assertIn("qdisc replace dev vh-target-a root netem delay 5.000ms", replace_payload)
        self.assertIn("qdisc replace dev vh-target-b root netem delay 7.000ms", replace_payload)
        first_physical = next(
            index
            for index, command in enumerate(commands)
            if command[:1] == ["nsenter"]
            or (command[:4] == ["sudo", "-n", "sh", "-lc"] and "ip link set dev" in command[-1])
        )
        self.assertLess(batch_commands[0][0], first_physical)
        self.assertLess(first_physical, batch_commands[1][0])
        self.assertEqual(probes, ["slot-a", "slot-b"])
        self.assertEqual(sum(result["qdisc_batch_command_count"] for result in results), 2)
        self.assertEqual(sum(result["qdisc_batch_sub_update_count"] for result in results), 4)

    def test_operation_group_merges_structural_and_parameter_replacements(self) -> None:
        installer, _commands = self._installer_with_recorded_commands()
        slots = {
            "rebind-slot": {"slot_name": "rebind-slot", "slot_type": "ut_access"},
            "param-slot": {"slot_name": "param-slot", "slot_type": "ut_access"},
        }

        def resource_for_binding(*, slot, binding):
            return {
                "resource_key": str(binding["name"]),
                "endpoints": [
                    {
                        "host_interface_name": f"vh-{binding['name']}",
                        "node_id": f"node-{binding['name']}",
                        "interface_name": "eth-edge",
                        "ip_address": "10.0.0.2/31",
                    }
                ],
            }

        installer._inventory = lambda topology, topology_path: slots  # type: ignore[method-assign]
        installer._resource_for_binding = resource_for_binding  # type: ignore[method-assign]
        installer._prepared_resource_keys_by_slot = {  # type: ignore[attr-defined]
            "rebind-slot": {"target-rebind"},
        }
        installer._probe_activated_resource = lambda *, slot, resource: None  # type: ignore[method-assign]

        results = installer.apply_operation_group(
            controller=SimpleNamespace(topology={}, topology_path="topology.json"),
            link_state_path=Path("link_state.json"),
            operations=[
                {
                    "operation": "rebind_slot_attachment",
                    "slot_name": "rebind-slot",
                    "source_binding": {"name": "source-rebind"},
                    "target_binding": {"name": "target-rebind"},
                    "target_occupied": True,
                    "target_runtime_parameters": {"delay_ms": 11.0},
                },
                {
                    "operation": "update_slot_parameters",
                    "slot_name": "param-slot",
                    "target_binding": {"name": "param-target"},
                    "target_occupied": True,
                    "source_runtime_parameters": {"delay_ms": 1.0},
                    "target_runtime_parameters": {"delay_ms": 13.0},
                    "changed_runtime_parameter_fields": ["delay_ms"],
                },
            ],
            persist_link_state=False,
        )

        self.assertEqual(len(installer._test_qdisc_batch_payloads), 2)  # type: ignore[attr-defined]
        replace_payload = installer._test_qdisc_batch_payloads[1]  # type: ignore[attr-defined]
        self.assertIn("qdisc replace dev vh-target-rebind root netem delay 11.000ms", replace_payload)
        self.assertIn("qdisc replace dev vh-param-target root netem delay 13.000ms", replace_payload)
        self.assertEqual(sum(result["qdisc_batch_command_count"] for result in results), 2)
        self.assertEqual(sum(result["qdisc_batch_sub_update_count"] for result in results), 3)

    def test_parameter_group_batch_failure_does_not_fallback_or_advance_state(self) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        commands: list[list[str]] = []
        batch_payloads: list[str] = []

        def fake_run(command, *, allow_failure=False):
            rendered = [str(part) for part in command]
            commands.append(rendered)
            if rendered[:4] == ["sudo", "-n", "tc", "-batch"]:
                batch_payloads.append(Path(rendered[4]).read_text())
                raise RuntimeError("synthetic batch failure")
            return subprocess.CompletedProcess(rendered, 0, "", "")

        installer._run_command = fake_run  # type: ignore[method-assign]
        installer._inventory = lambda topology, topology_path: {  # type: ignore[method-assign]
            "ut1-access-slot0": {
                "slot_name": "ut1-access-slot0",
                "slot_type": "ut_access",
            }
        }
        installer._resource_for_binding = lambda *, slot, binding: {  # type: ignore[method-assign]
            "endpoints": [{"host_interface_name": "vhs-ut1"}]
        }
        state_by_name: dict[str, dict] = {}

        with self.assertRaisesRegex(NetworkMutationExecutionError, "QDISC_BATCH_EXECUTION_FAILED"):
            installer.apply_parameter_update_operations(
                controller=SimpleNamespace(topology={}, topology_path="topology.json"),
                link_state_path=Path("link_state.json"),
                operations=[
                    {
                        "operation": "update_slot_parameters",
                        "slot_name": "ut1-access-slot0",
                        "slot_type": "ut_access",
                        "target_binding": {"ut_id": "ut1", "satellite_id": "sat-a"},
                        "target_occupied": True,
                        "source_runtime_parameters": {"delay_ms": 4.0},
                        "target_runtime_parameters": {"delay_ms": 5.0},
                        "changed_runtime_parameter_fields": ["delay_ms"],
                    }
                ],
                state_by_name=state_by_name,
                persist_link_state=False,
            )

        self.assertEqual(len(batch_payloads), 1)
        self.assertFalse(state_by_name)
        self.assertNotIn("vhs-ut1", installer._qdisc_runtime_args_by_interface)  # type: ignore[attr-defined]
        self.assertEqual(
            [
                command
                for command in commands
                if command[:5] == ["sudo", "-n", "tc", "qdisc", "replace"]
            ],
            [],
        )

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
