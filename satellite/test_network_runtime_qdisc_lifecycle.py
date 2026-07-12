#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

import json
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
    def test_container_pid_cache_must_be_exactly_sealed_before_handoff(self) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        installer._container_pid_cache = {"sat-a": 101, "gw-a": 202}

        with self.assertRaisesRegex(
            RuntimeError,
            "frozen_container_pid_cache_not_sealed",
        ):
            installer.frozen_container_pid_cache_snapshot()
        with self.assertRaisesRegex(
            RuntimeError,
            "frozen_container_pid_cache_scope_mismatch",
        ):
            installer.seal_container_pid_cache(expected_node_ids=["sat-a"])

        seal = installer.seal_container_pid_cache(
            expected_node_ids=["sat-a", "gw-a"]
        )

        self.assertTrue(seal["complete"])
        self.assertFalse(seal["live_discovery_allowed"])
        self.assertEqual(
            installer.frozen_container_pid_cache_snapshot(),
            {"sat-a": 101, "gw-a": 202},
        )

    def test_targeted_binding_capture_batches_by_frozen_node_without_scan(self) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        installer._container_pid_cache = {"sat-a": 101, "sat-b": 202}
        commands = []

        def run(command, *, allow_failure=False):
            commands.append(list(command))
            marker = command.index("skyseed-targeted-interface-unicast-prepare")
            interface_names = list(command[marker + 1 :])
            stdout = "".join(
                json.dumps(
                    [
                        {
                            "ifindex": 1000 + index,
                            "address": f"02:00:00:00:00:{index + 1:02x}",
                            "flags": ["BROADCAST", "UP"],
                        }
                    ]
                )
                + "\n"
                for index, _ in enumerate(interface_names)
            )
            return subprocess.CompletedProcess(command, 0, stdout, "")

        installer._run_command = run  # type: ignore[method-assign]
        report = installer.capture_existing_container_interface_bindings(
            interface_refs=[
                "netns:sat-a:if0",
                "netns:sat-a:if1",
                "netns:sat-b:if0",
            ]
        )

        self.assertTrue(report["ok"])
        self.assertFalse(report["full_scan"])
        self.assertEqual(report["requested_interface_ref_count"], 3)
        self.assertEqual(report["targeted_node_batch_count"], 2)
        self.assertEqual(report["targeted_nsenter_command_count"], 2)
        self.assertEqual(len(commands), 2)
        self.assertEqual(len(report["bindings"]), 3)

    def test_targeted_binding_capture_parallel_failure_returns_no_partial_handoff(
        self,
    ) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        installer._container_pid_cache = {"sat-a": 101, "sat-b": 202}
        commands = []

        def run(command, *, allow_failure=False):
            commands.append(list(command))
            if "202" in command:
                raise RuntimeError("frozen_target_interface_missing")
            stdout = json.dumps(
                [
                    {
                        "ifindex": 1001,
                        "address": "02:00:00:00:00:01",
                        "flags": ["BROADCAST", "UP"],
                    }
                ]
            )
            return subprocess.CompletedProcess(command, 0, stdout, "")

        installer._run_command = run  # type: ignore[method-assign]

        with self.assertRaisesRegex(RuntimeError, "frozen_target_interface_missing"):
            installer.capture_existing_container_interface_bindings(
                interface_refs=[
                    "netns:sat-a:if0",
                    "netns:sat-b:if0",
                ]
            )

        self.assertEqual(len(commands), 2)

    def test_horizon_handoff_merges_prepared_scope_without_replacing_active_state(
        self,
    ) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        installer._runtime_prepared = True
        installer._prepared_topology_path = "/parent/topology.json"
        installer._prepared_link_state_path = "/parent/link_state.json"
        installer._prepared_resource_keys_by_slot = {
            "slot-a": {"resource-old"}
        }
        installer._container_pid_cache = {"sat-a": 4242}
        installer._container_pid_cache_complete = True

        report = installer.accept_horizon_maintenance_prepared_state(
            topology_path=Path(__file__),
            link_state_path=Path(__file__),
            prepared_resource_scope_by_slot={
                "slot-a": ["resource-old", "resource-new"],
                "slot-b": ["resource-b"],
            },
            prepared_resource_counts={
                "bridge_count": 2,
                "candidate_resource_count": 3,
            },
            frozen_container_pid_cache={"sat-a": 4242},
        )

        self.assertTrue(report["ok"])
        self.assertTrue(report["physical_prepare_skipped"])
        self.assertTrue(report["hidden_reconcile_skipped"])
        self.assertTrue(report["parent_active_link_state_preserved"])
        self.assertEqual(report["added_prepared_resource_count"], 2)
        self.assertEqual(
            installer._prepared_resource_keys_by_slot,
            {
                "slot-a": {"resource-old", "resource-new"},
                "slot-b": {"resource-b"},
            },
        )
        self.assertEqual(
            installer._prepared_topology_path,
            "/parent/topology.json",
        )
        self.assertEqual(
            installer._prepared_link_state_path,
            "/parent/link_state.json",
        )

    def test_horizon_handoff_rejects_dropped_parent_resource_and_pid_drift(
        self,
    ) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        installer._runtime_prepared = True
        installer._prepared_resource_keys_by_slot = {
            "slot-a": {"resource-old"}
        }
        installer._container_pid_cache = {"sat-a": 4242}
        installer._container_pid_cache_complete = True
        with self.assertRaisesRegex(
            RuntimeError,
            "horizon_maintenance_handoff_dropped_prepared_resources",
        ):
            installer.accept_horizon_maintenance_prepared_state(
                topology_path=Path(__file__),
                link_state_path=Path(__file__),
                prepared_resource_scope_by_slot={"slot-a": ["resource-new"]},
                frozen_container_pid_cache={"sat-a": 4242},
            )
        with self.assertRaisesRegex(
            RuntimeError,
            "horizon_maintenance_handoff_pid_cache_mismatch",
        ):
            installer.accept_horizon_maintenance_prepared_state(
                topology_path=Path(__file__),
                link_state_path=Path(__file__),
                prepared_resource_scope_by_slot={"slot-a": ["resource-old"]},
                frozen_container_pid_cache={"sat-a": 5252},
            )

    def test_additive_prepare_uses_frozen_owned_index_without_live_scans(self) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        installer._runtime_prepared = True
        installer._load_topology = lambda _: {}  # type: ignore[method-assign]
        installer._slot_inventory_for_names = lambda topology, slot_names: {  # type: ignore[method-assign]
            "slot-a": {
                "slot_name": "slot-a",
                "slot_type": "cross_plane_isl",
                "subnet": "10.0.0.0/30",
            }
        }
        installer._host_interface_name_set = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError("live host scan forbidden")
        )
        installer._container_interface_name_set = lambda **_: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError("live netns scan forbidden")
        )
        installer._set_bridge_ready = lambda **_: None  # type: ignore[method-assign]
        installer._preconnected_endpoint = lambda _: None  # type: ignore[method-assign]
        installer._prepared_resources_for_slot = lambda **_: {  # type: ignore[method-assign]
            "resource-a": {
                "endpoints": [
                    {
                        "host_interface_name": "vhs-a",
                        "temp_interface_name": "vtmp-a",
                        "node_id": "sat-a",
                        "interface_name": "xp-a",
                        "ip_address": "10.0.0.1/30",
                    }
                ]
            }
        }
        created = []

        def create_with_binding(**kwargs):
            created.append(kwargs)
            return {
                "interface_ref": "netns:sat-a:xp-a",
                "ifindex": 4321,
                "mac_address": "02:00:00:00:00:01",
                "binding_source": "created_veth_operation_result",
            }

        installer._create_prepared_veth = create_with_binding  # type: ignore[method-assign]

        report = installer.prepare_additive_resources(
            topology_path=Path("topology.json"),
            link_state_path=Path("link_state.json"),
            prepared_resource_scope_by_slot={"slot-a": ["resource-a"]},
        )

        self.assertTrue(report["ok"])
        self.assertTrue(report["live_host_interface_scan_skipped"])
        self.assertTrue(report["live_container_interface_scan_skipped"])
        self.assertEqual(
            report["prepared_owned_resource_scope_by_slot"],
            {"slot-a": ["resource-a"]},
        )
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0]["capture_binding"])
        self.assertEqual(
            report["created_container_interface_bindings"],
            [
                {
                    "interface_ref": "netns:sat-a:xp-a",
                    "ifindex": 4321,
                    "mac_address": "02:00:00:00:00:01",
                    "binding_source": "created_veth_operation_result",
                }
            ],
        )

    def test_created_veth_captures_binding_only_when_requested(self) -> None:
        installer, commands = self._installer_with_recorded_commands()
        installer._delete_host_interface_if_present = lambda *_, **__: None  # type: ignore[method-assign]
        installer._container_pid = lambda _: 4242  # type: ignore[method-assign]
        original_nsenter = installer._nsenter

        def nsenter_with_ifindex(*, node_id, command, allow_failure=False):
            if command[0] == "sh" and 'ip -j link show dev "$2"' in command[2]:
                rendered = ["nsenter", str(node_id), *[str(part) for part in command]]
                commands.append(rendered)
                return subprocess.CompletedProcess(
                    rendered,
                    0,
                    '[{"ifindex":4321,"ifname":"xp-a"}]\n',
                    "",
                )
            return original_nsenter(
                node_id=node_id,
                command=command,
                allow_failure=allow_failure,
            )

        installer._nsenter = nsenter_with_ifindex  # type: ignore[method-assign]
        endpoint = {
            "host_interface_name": "vhs-a",
            "temp_interface_name": "vtmp-a",
            "node_id": "sat-a",
            "interface_name": "xp-a",
            "ip_address": "10.0.0.1/30",
        }

        uncaptured = installer._create_prepared_veth(
            bridge_name="br-a",
            slot_name="slot-a",
            endpoint=endpoint,
            activate_now=False,
            assign_ip=False,
        )
        self.assertIsNone(uncaptured)
        self.assertFalse(
            any(
                "ip -j link show" in " ".join(command)
                for command in commands
            )
        )

        commands.clear()
        captured = installer._create_prepared_veth(
            bridge_name="br-a",
            slot_name="slot-a",
            endpoint=endpoint,
            activate_now=False,
            assign_ip=False,
            capture_binding=True,
        )
        self.assertEqual(captured["interface_ref"], "netns:sat-a:xp-a")
        self.assertEqual(captured["ifindex"], 4321)
        self.assertEqual(captured["binding_source"], "created_veth_operation_result")
        self.assertEqual(
            sum(
                "ip -j link show" in " ".join(command)
                for command in commands
            ),
            1,
        )
        container_batches = [
            command
            for command in commands
            if "skyseed-created-veth-container-prepare" in command
        ]
        host_batches = [
            command
            for command in commands
            if "skyseed-created-veth-host-prepare" in command
        ]
        self.assertEqual(len(container_batches), 1)
        self.assertEqual(len(host_batches), 1)
        container_script = container_batches[0][4]
        host_script = host_batches[0][4]
        self.assertTrue(container_script.startswith("set -eu; "))
        self.assertLess(
            container_script.index('ip link set "$1" name "$2"'),
            container_script.index('ip link set dev "$2" address "$3"'),
        )
        self.assertLess(
            container_script.index('ip link set dev "$2" address "$3"'),
            container_script.index('ip link set dev "$2" multicast off'),
        )
        self.assertIn('ip addr flush dev "$2" || true', container_script)
        self.assertIn('ip link set "$2" down || true', container_script)
        self.assertTrue(host_script.startswith("set -eu; "))
        self.assertIn('ip link set dev "$1" multicast off', host_script)
        self.assertIn('ip link set "$1" down || true', host_script)

    def test_created_veth_container_batch_failure_stops_without_host_fallback(
        self,
    ) -> None:
        installer, commands = self._installer_with_recorded_commands()
        installer._container_pid = lambda _: 4242  # type: ignore[method-assign]

        def fail_container_batch(*, node_id, command, allow_failure=False):
            rendered = ["nsenter", str(node_id), *[str(part) for part in command]]
            commands.append(rendered)
            raise RuntimeError("synthetic_container_batch_failure")

        installer._nsenter = fail_container_batch  # type: ignore[method-assign]
        with self.assertRaisesRegex(
            RuntimeError,
            "synthetic_container_batch_failure",
        ):
            installer._create_prepared_veth(
                bridge_name="br-a",
                slot_name="slot-a",
                endpoint={
                    "host_interface_name": "vhs-a",
                    "temp_interface_name": "vtmp-a",
                    "node_id": "sat-a",
                    "interface_name": "xp-a",
                    "ip_address": "10.0.0.1/30",
                },
                activate_now=False,
                assign_ip=False,
                capture_binding=True,
            )
        self.assertEqual(
            sum("skyseed-created-veth-container-prepare" in command for command in commands),
            1,
        )
        self.assertFalse(
            any("skyseed-created-veth-host-prepare" in command for command in commands)
        )

    def test_prepared_bridge_disables_host_multicast_before_link_up(self) -> None:
        installer, commands = self._installer_with_recorded_commands()

        installer._set_bridge_ready(
            bridge_name="br-a",
            subnet="192.0.2.0/30",
            bridge_gateway_ip="192.0.2.1",
        )

        multicast_command = [
            "sudo", "-n", "ip", "link", "set", "dev", "br-a",
            "multicast", "off",
        ]
        up_command = ["sudo", "-n", "ip", "link", "set", "br-a", "up"]
        self.assertIn(multicast_command, commands)
        self.assertIn(up_command, commands)
        self.assertLess(
            commands.index(multicast_command),
            commands.index(up_command),
        )

    def test_initial_prepare_returns_complete_created_interface_cache(self) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        endpoint = {
            "host_interface_name": "vhs-a",
            "temp_interface_name": "vtmp-a",
            "node_id": "sat-a",
            "interface_name": "fs0001",
            "ip_address": "10.0.0.1/30",
        }
        slot = {
            "slot_name": "slot-a",
            "slot_type": "same_plane_isl",
            "subnet": "10.0.0.0/30",
            "docker_network_name": "unused",
        }
        installer._runtime_artifact_inventory = lambda **_: {  # type: ignore[method-assign]
            "inventory_scope": "bounded",
            "slot_counts_by_type": {"same_plane_isl": 1},
            "candidate_resource_counts_by_type": {"same_plane_isl": 1},
            "candidate_endpoint_counts_by_type": {"same_plane_isl": 1},
            "preconnected_endpoint_counts_by_type": {"same_plane_isl": 0},
        }
        installer._slot_inventory_for_names = lambda *_: {"slot-a": slot}  # type: ignore[method-assign]
        installer._host_interface_name_set = lambda: set()  # type: ignore[method-assign]
        installer._docker_network_name_set = lambda: set()  # type: ignore[method-assign]
        installer._container_interface_name_set = lambda **_: set()  # type: ignore[method-assign]
        installer._remove_docker_network_if_present = lambda *_, **__: None  # type: ignore[method-assign]
        installer._delete_host_interface_if_present = lambda *_, **__: None  # type: ignore[method-assign]
        installer._bridge_exists = lambda *_, **__: False  # type: ignore[method-assign]
        installer._set_bridge_ready = lambda **_: None  # type: ignore[method-assign]
        installer._preconnected_endpoint = lambda _: None  # type: ignore[method-assign]
        installer._prepared_resources_for_slot = lambda **_: {  # type: ignore[method-assign]
            "resource-a": {"resource_key": "resource-a", "endpoints": [endpoint]}
        }
        installer._mark_link_state_prepared = lambda **_: None  # type: ignore[method-assign]

        def create(**kwargs):
            self.assertTrue(kwargs["capture_binding"])
            return {
                "interface_ref": "netns:sat-a:fs0001",
                "ifindex": 9001,
                "mac_address": "02:00:00:00:00:01",
                "binding_source": "created_veth_operation_result",
            }

        installer._create_prepared_veth = create  # type: ignore[method-assign]

        report = installer._prepare_runtime(
            topology={},
            topology_path=Path("topology.json"),
            link_state_path=Path("link_state.json"),
            prepared_resource_scope_by_slot={"slot-a": ["resource-a"]},
        )

        cache = report["frozen_interface_binding_cache"]
        self.assertTrue(cache["complete"])
        self.assertFalse(cache["live_discovery_allowed"])
        self.assertEqual(
            cache["ifindex_by_interface_ref"],
            {"netns:sat-a:fs0001": 9001},
        )

    def test_targeted_existing_binding_uses_frozen_pid_without_scan(self) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        installer._container_pid_cache = {"gw201": 4242}
        commands = []

        def run(command, *, allow_failure=False):
            commands.append(list(command))
            return subprocess.CompletedProcess(
                command,
                0,
                '[{"ifindex":201,"ifname":"ix200","address":"02:00:00:00:02:01"}]',
                "",
            )

        installer._run_command = run  # type: ignore[method-assign]
        report = installer.capture_existing_container_interface_bindings(
            interface_refs=["netns:gw201:ix200"]
        )

        self.assertTrue(report["ok"])
        self.assertFalse(report["full_scan"])
        self.assertTrue(report["unicast_physical_prepare"])
        self.assertEqual(report["multicast_disable_count"], 1)
        self.assertEqual(report["requested_interface_ref_count"], 1)
        self.assertEqual(report["bindings"][0]["ifindex"], 201)
        self.assertEqual(
            commands,
            [[
                "sudo", "-n", "nsenter", "-t", "4242", "-n",
                "sh", "-c",
                'set -eu; for interface_name do ip link set dev "$interface_name" '
                'multicast off; ip -j link show dev "$interface_name"; done',
                "skyseed-targeted-interface-unicast-prepare", "ix200",
            ]],
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "targeted_interface_binding_frozen_pid_missing:gw202",
        ):
            installer.capture_existing_container_interface_bindings(
                interface_refs=["netns:gw202:ix201"]
            )
        self.assertEqual(len(commands), 1)

    def test_targeted_host_unicast_prepare_uses_exact_interface_names(self) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        commands = []

        def run(command, *, allow_failure=False):
            rendered = [str(part) for part in command]
            commands.append(rendered)
            stdout = ""
            if rendered[:5] == ["ip", "-j", "link", "show", "dev"]:
                stdout = json.dumps(
                    [
                        {
                            "ifindex": 202,
                            "ifname": rendered[5],
                            "flags": ["BROADCAST", "UP"],
                        }
                    ]
                )
            return subprocess.CompletedProcess(rendered, 0, stdout, "")

        installer._run_command = run  # type: ignore[method-assign]
        report = installer.prepare_existing_host_interfaces_unicast(
            interface_names=["sib-one", "sib-two", "sib-one"]
        )

        self.assertTrue(report["ok"])
        self.assertFalse(report["full_scan"])
        self.assertEqual(report["prepared_interface_count"], 2)
        self.assertEqual(report["multicast_disable_count"], 2)
        self.assertEqual(
            [command for command in commands if command[:3] == ["sudo", "-n", "ip"]],
            [
                [
                    "sudo", "-n", "ip", "link", "set", "dev", "sib-one",
                    "multicast", "off",
                ],
                [
                    "sudo", "-n", "ip", "link", "set", "dev", "sib-two",
                    "multicast", "off",
                ],
            ],
        )

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
        self.assertTrue(
            all("-brief" not in " ".join(command) for command in commands),
            "activation must not repeat link/address discovery after fail-closed writes",
        )
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
        self.assertTrue(
            all("-brief" not in " ".join(command) for command in commands),
            "batched activation must not run per-resource discovery probes",
        )
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
