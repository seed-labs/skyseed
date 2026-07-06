#!/usr/bin/env python3
# encoding: utf-8

import json
import tempfile
import unittest
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "apps"))

import linux_mpls_runtime
import satellite.skyseed_preflight as skyseed_preflight
from run_starlink_shell_experiment import (
    _aggregate_node_local_cleanup_reports,
    _compose_force_remove_project_residue,
    _compose_force_remove_stale_networks,
)
from linux_mpls_runtime import (
    ActiveRuntimeSession,
    BPF_OWNED_PROOF_SCHEMA_VERSION,
    DirectGatewayExperimentContext,
    MplsPathSegment,
    NodeLocalBatchResult,
    RuntimeCommandSpec,
    _bpf_owned_dataplane_entries_for_contexts,
    _bpf_owned_compile_mpls_maps_for_contexts,
    _bpf_owned_final_validation_guard,
    _bpf_owned_endpoint_role_for_slot,
    _gateway_nat_source_ip_for_flow,
    _bpf_owned_host_interface_name,
    _bpf_owned_host_interfaces_for_contexts,
    _bpf_owned_path_observability_summary,
    _bpf_owned_runtime_readiness_failure_message,
    _bpf_owned_ut_access_satellite_endpoint,
    _classify_bpf_owned_packet_path_blocker,
    _execute_node_local_batches,
    _group_node_local_batches,
    _node_local_batch_cleanup_metrics,
    _derive_bpf_owned_readiness_proof,
    _normalize_forwarding_backend_config,
    _plan_backend_apply_contract,
    _prepare_bpf_owned_readiness,
    _prove_bpf_owned_readiness,
    _parse_node_local_command_timings,
    _strip_node_local_command_timing_markers,
)
from contracts import NetworkMutationPlan, RuntimeUpdatePlan
from network_runtime import (
    DirectNetnsVethSlotLinkNamespaceQdiscInstaller,
    InProcessHostRuntimeManager,
    MinimalRealNetworkMutationExecutor,
    NetworkMutationExecutionError,
)
from runtime_operation_metrics import summarize_node_local_batches


def _runtime_update_plan(plan_id: str = "rtplan-proof") -> RuntimeUpdatePlan:
    flow_context = {
        "flow_id": "flow-1",
        "ut_id": "ut1",
        "zone_id": "zone-a",
        "gateway_class_id": "zone-a:default-gateway:gw1",
        "serving_satellite_id": "sat-a",
        "selected_gateway_id": "gw1",
        "selected_gateway_satellite_id": "sat-c",
        "satellite_path": ["sat-a", "sat-b", "sat-c"],
        "forward_link_ids": ["sat-a-sat-b", "sat-b-sat-c", "sat-c-gw1"],
        "reverse_link_ids": ["gw1-sat-c", "sat-c-sat-b", "sat-b-sat-a"],
        "forward_labels": [16001, 16002, 16003],
        "reverse_labels": [17001, 17002, 17003],
        "zone_return_prefixes": ["10.90.0.0/24"],
        "return_path_intent": {
            "shared_backbone_return_target": {
                "target_key": "gw1:zone-a:sat-a",
                "zone_serving_satellite_id": "sat-a",
            },
            "candidate_shared_zone_return_route": {
                "route_prefixes": ["10.90.0.0/24"],
            },
        },
    }
    action = {
        "action_id": "forwarding-flow-1",
        "action_type": "forwarding_entry",
        "target_node_id": "sat-a",
        "intent": {
            "forwarding_plane": "label_switched_internet_egress",
            "match": {"flow_id": "flow-1"},
            "outcome": {"flow_context": flow_context},
        },
        "lifecycle": {},
        "validation": {},
    }
    return RuntimeUpdatePlan(
        schema_version="satellite-iteration2-runtime-update-plan-v1alpha1",
        plan_id=plan_id,
        target_snapshot={"snapshot_id": "snap-1", "slice_id": "slice-1"},
        planner={"planner_name": "test-planner"},
        apply_model={
            "state_model": "full_state_intent",
            "action_model": "satellite-runtime-update-action-v1alpha1",
        },
        node_subplans={
            "sat-a": {
                "target_node_id": "sat-a",
                "actions": [action],
            }
        },
        replacement_semantics={},
        validation_expectations={},
    )


def _network_mutation_plan(plan_id: str = "mut-proof") -> NetworkMutationPlan:
    return NetworkMutationPlan(
        schema_version="skyseed-network-mutation-plan-v1",
        mutation_id=plan_id,
        source_snapshot={"state_id": "source"},
        target_scope={
            "state_model": "delta_mutation",
            "target_snapshot_id": "target",
            "target_slice_id": "slice-1",
        },
        execution_boundary={"consumer": "host_network_runtime_manager"},
        operations=[
            {
                "operation": "update_slot_parameters",
                "slot_name": "slot-a",
                "execution_domain": "host_network_runtime",
            }
        ],
        validation_expectations={},
    )


def _prepared_bpf_owned_readiness(
    plan: RuntimeUpdatePlan | None = None,
) -> dict:
    proof = _derive_bpf_owned_readiness_proof(
        runtime_update_plan=plan or _runtime_update_plan(),
    )
    map_entry_count = int(proof.get("map_entry_count") or 0)
    return {
        **proof,
        "ready": True,
        "ok": True,
        "reason": "",
        "fallback_reason": "",
        "fallback_to_linux": False,
        "runtime_generation_loaded": True,
        "active_generation_set": True,
        "map_ready": True,
        "real_backend_prepared": True,
        "prepare_steps": {
            **dict(proof.get("prepare_steps") or {}),
            "real_backend_prepare": {
                "ok": True,
                "loaded_gz_fec_count": map_entry_count,
                "loaded_runtime_generation_count": 1,
                "active_generation_switch_count": 1,
            },
            "compile": {"ok": True, "returncode": 0},
            "load": {
                "ok": True,
                "loaded_fec_count": map_entry_count,
                "loaded_generation_count": 1,
            },
            "attach": {
                "ok": True,
                "attached_interfaces": list(proof.get("attached_interfaces") or []),
            },
        },
    }


class _FakeCleanupInstaller(DirectNetnsVethSlotLinkNamespaceQdiscInstaller):
    def __init__(self) -> None:
        super().__init__()
        self.host_interfaces = {"vh1", "vt1", "br1"}
        self.container_interfaces = {"node-a": {"eth9"}}
        self.host_interface_scan_count = 0
        self.container_interface_scan_count = 0

    def _load_topology(self, topology_path: Path) -> dict[str, Any]:
        return {}

    def _runtime_artifact_inventory(
        self,
        *,
        topology: dict[str, Any],
        topology_path: Path,
        prepared_resource_scope_by_slot: Any | None = None,
    ) -> dict[str, Any]:
        bounded = prepared_resource_scope_by_slot is not None
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
            "cleanup_scope_source": (
                "prepared_owned_resource_index"
                if bounded
                else "full_topology_candidate_inventory_fallback"
            ),
            "bounded_owned_scope": bounded,
            "prepared_slot_scope_count": (
                len(prepared_resource_scope_by_slot or {})
                if bounded
                else 0
            ),
        }

    def _host_interface_name_set(self) -> set[str]:
        self.host_interface_scan_count += 1
        return set(self.host_interfaces)

    def _container_interface_name_set(self, *, node_id: str) -> set[str]:
        self.container_interface_scan_count += 1
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

    def _delete_host_interface_best_effort(self, interface_name: str) -> str:
        if interface_name in self.host_interfaces:
            self.host_interfaces.discard(interface_name)
            return "deleted"
        return "absent"

    def _delete_bridge_best_effort(self, bridge_name: str) -> str:
        return self._delete_host_interface_best_effort(bridge_name)

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

    def _delete_container_interface_best_effort(
        self,
        *,
        node_id: str,
        interface_name: str,
    ) -> str:
        interfaces = self.container_interfaces.setdefault(node_id, set())
        if interface_name in interfaces:
            interfaces.discard(interface_name)
            return "deleted"
        return "absent"

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
    def test_scoped_cleanup_disconnects_stale_network_endpoint_names_before_retry(self) -> None:
        cleanup_calls: list[list[str]] = []
        run_calls: list[list[str]] = []

        def fake_cleanup_command_result(*, command, apply):
            cleanup_calls.append([str(part) for part in command])
            if len(cleanup_calls) == 1:
                return {
                    "command": list(command),
                    "executed": True,
                    "ok": False,
                    "returncode": 1,
                    "stderr": "network has active endpoints",
                }
            return {
                "command": list(command),
                "executed": True,
                "ok": True,
                "returncode": 0,
                "stderr": "",
            }

        def fake_run(command):
            run_calls.append([str(part) for part in command])
            if command[:3] == ["docker", "network", "inspect"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        [
                            {
                                "Containers": {
                                    "missing-container-id": {
                                        "Name": "starlinkexp-as154-router0"
                                    }
                                }
                            }
                        ]
                    ),
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(
            skyseed_preflight,
            "_cleanup_command_result",
            side_effect=fake_cleanup_command_result,
        ), patch.object(skyseed_preflight, "_run", side_effect=fake_run):
            results = skyseed_preflight._cleanup_compose_networks_with_stale_endpoints(
                ["seed_output_net_154_net0"],
                apply=True,
            )

        self.assertEqual(
            cleanup_calls,
            [
                ["docker", "network", "rm", "seed_output_net_154_net0"],
                ["docker", "network", "rm", "seed_output_net_154_net0"],
            ],
        )
        self.assertIn(
            [
                "docker",
                "network",
                "disconnect",
                "-f",
                "seed_output_net_154_net0",
                "starlinkexp-as154-router0",
            ],
            run_calls,
        )
        self.assertTrue(results[-1]["ok"])

    def test_preflight_cleanup_success_ignores_network_retry_superseded_failure(
        self,
    ) -> None:
        report = {
            "checks": {
                "docker_state": {
                    "containers": {},
                    "networks": {
                        "compose_project_networks": [
                            {"name": "seed_output_net_154_net0"}
                        ],
                        "named_skyseed_networks": [],
                    },
                },
                "compose_processes": {"processes": []},
                "direct_backend_host_artifacts": {"interfaces": []},
                "runtime_lock_files": {"files": []},
                "bpf_pinned_map_artifacts": {"map_names": []},
            }
        }
        cleanup_calls: list[list[str]] = []
        run_calls: list[list[str]] = []

        def fake_cleanup_command_result(*, command, apply):
            cleanup_calls.append([str(part) for part in command])
            return {
                "command": list(command),
                "executed": bool(apply),
                "ok": len(cleanup_calls) > 1,
                "returncode": 0 if len(cleanup_calls) > 1 else 1,
                "stderr": ""
                if len(cleanup_calls) > 1
                else "network has active endpoints",
            }

        def fake_run(command):
            run_calls.append([str(part) for part in command])
            if command[:3] == ["docker", "network", "inspect"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        [
                            {
                                "Containers": {
                                    "missing-container-id": {
                                        "Name": "starlinkexp-as154-router0"
                                    }
                                }
                            }
                        ]
                    ),
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(
            skyseed_preflight,
            "_cleanup_command_result",
            side_effect=fake_cleanup_command_result,
        ), patch.object(skyseed_preflight, "_run", side_effect=fake_run):
            result = skyseed_preflight.cleanup_skyseed_environment_residue(
                preflight_report=report,
                apply=True,
            )

        self.assertTrue(result["success"])
        self.assertEqual(
            cleanup_calls,
            [
                ["docker", "network", "rm", "seed_output_net_154_net0"],
                ["docker", "network", "rm", "seed_output_net_154_net0"],
            ],
        )

    def test_preflight_cleanup_counts_and_removes_bpf_pinned_maps(self) -> None:
        report = {
            "checks": {
                "docker_state": {"containers": {}, "networks": {}},
                "compose_processes": {"processes": []},
                "direct_backend_host_artifacts": {"interfaces": []},
                "runtime_lock_files": {"files": []},
                "bpf_pinned_map_artifacts": {
                    "map_names": ["active_generation", "interface_role_map"]
                },
            }
        }
        counts = skyseed_preflight._skyseed_cleanliness_counts(report["checks"])
        self.assertEqual(counts["bpf_pinned_maps"], 2)

        cleanup_calls: list[list[str]] = []

        def fake_cleanup_command_result(*, command, apply):
            cleanup_calls.append([str(part) for part in command])
            return {
                "command": list(command),
                "executed": bool(apply),
                "ok": True,
                "returncode": 0,
                "stderr": "",
            }

        with patch.object(
            skyseed_preflight,
            "_cleanup_command_result",
            side_effect=fake_cleanup_command_result,
        ):
            result = skyseed_preflight.cleanup_skyseed_environment_residue(
                preflight_report=report,
                apply=True,
            )

        self.assertEqual(result["resource_counts"]["bpf_pinned_maps"], 2)
        self.assertEqual(
            cleanup_calls,
            [
                [
                    "sudo",
                    "-n",
                    "rm",
                    "-f",
                    "/sys/fs/bpf/tc/globals/active_generation",
                    "/sys/fs/bpf/tc/globals/interface_role_map",
                ]
            ],
        )

    def test_rolling_topology_switch_requires_explicit_reconcile_without_full_prepare(
        self,
    ) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        installer._runtime_prepared = True
        installer._prepared_topology_path = "/old/topology.json"
        installer._prepared_link_state_path = "/old/link_state.json"

        with (
            patch.object(
                installer,
                "_load_topology",
                return_value={"slot_inventory": {}},
            ),
            patch.object(
                installer,
                "_prepare_runtime",
                side_effect=AssertionError("full prepare must not run"),
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "direct_backend_rolling_topology_switch_requires_explicit_reconcile_scope",
            ):
                installer.controller(
                    topology_path=Path("/new/topology.json"),
                    link_state_path=Path("/new/link_state.json"),
                    reconcile_slot_names=None,
                )

            controller = installer.controller(
                topology_path=Path("/new/topology.json"),
                link_state_path=Path("/new/link_state.json"),
                reconcile_slot_names=["slot-a"],
                reconcile_mode="trusted_skip",
            )

        self.assertEqual(controller.topology_path, "/new/topology.json")

    def test_runtime_activation_cannot_materialize_unprepared_resource(self) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()

        with self.assertRaisesRegex(
            RuntimeError,
            "runtime_resource_materialization_forbidden_missing_prepared_resource",
        ):
            installer._ensure_resource_prepared(
                slot={"slot_name": "ut2001-access-slot0"},
                resource={
                    "resource_key": "ut_access:satellite_id=stl-940335",
                    "endpoints": [],
                },
            )

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
        self.assertEqual(
            metrics["cleanup_counts_by_scope"]["satellite_core_mpls_lfib_residue"],
            1,
        )
        self.assertEqual(
            metrics["cleanup_counts_by_scope"]["fallback_control_residue"],
            1,
        )
        self.assertEqual(
            metrics["bpf_owned_gz_forwarding_cleanup_candidate_command_count"],
            1,
        )

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

        def fake_run(batch: Any, **_: Any) -> NodeLocalBatchResult:
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

    def test_node_local_command_timing_markers_are_parsed(self) -> None:
        timings = _parse_node_local_command_timings(
            "\n".join(
                [
                    "__SKYSEED_CMD_BEGIN__ 0 10.000000000",
                    "command output",
                    "__SKYSEED_CMD_END__ 0 10.125000000 0",
                    "__SKYSEED_CMD_BEGIN__ 1 11.000000000",
                    "__SKYSEED_CMD_END__ 1 11.250000000 2",
                ]
            )
        )

        self.assertEqual(len(timings), 2)
        self.assertEqual(timings[0]["command_index"], 0)
        self.assertAlmostEqual(timings[0]["wall_seconds"], 0.125)
        self.assertEqual(timings[1]["returncode"], 2)

    def test_node_local_command_timing_markers_are_stripped_from_stdout(self) -> None:
        stdout = _strip_node_local_command_timing_markers(
            "\n".join(
                [
                    "__SKYSEED_CMD_BEGIN__ 0 10.000000000",
                    "[apply] install forward LFIB",
                    "command output",
                    "__SKYSEED_CMD_END__ 0 10.125000000 0",
                ]
            )
        )

        self.assertEqual(stdout, "[apply] install forward LFIB\ncommand output")

    def test_node_local_hardened_summary_classifies_scope_and_timing(self) -> None:
        batch_plan = [
            {
                "node_id": "sat-a",
                "phase": "apply",
                "command_count": 3,
                "descriptions": [
                    "install forward LFIB on sat-a for label 16001",
                    "install gateway SNAT rule",
                    "delete gateway forward filter rule",
                    "set sat-a MPLS platform labels",
                ],
                "commands": [
                    ["ip", "-f", "mpls", "route", "replace", "16001", "dev", "lo"],
                    ["iptables", "-w", "10", "-A", "FORWARD", "-j", "ACCEPT"],
                    ["sh", "-lc", "true"],
                    ["sysctl", "-w", "net.mpls.platform_labels=200000"],
                ],
                "allow_failures": [False, False, True, False],
            }
        ]
        batch_results = [
            {
                "node_id": "sat-a",
                "phase": "apply",
                "command_count": 4,
                "wall_seconds": 0.6,
                "command_timings": [
                    {"command_index": 0, "wall_seconds": 0.2, "returncode": 0},
                    {"command_index": 1, "wall_seconds": 0.3, "returncode": 0},
                    {"command_index": 2, "wall_seconds": 0.05, "returncode": 0},
                    {"command_index": 3, "wall_seconds": 0.1, "returncode": 0},
                ],
            }
        ]

        summary = summarize_node_local_batches(batch_plan, batch_results)

        self.assertTrue(summary["per_command_timing_available"])
        self.assertEqual(summary["counts_by_scope"]["satellite_core_mpls_lfib"], 1)
        self.assertEqual(summary["counts_by_scope"]["edge_nat_iptables"], 2)
        self.assertEqual(summary["counts_by_scope"]["mpls_readiness_sysctl"], 1)
        self.assertAlmostEqual(
            summary["wall_seconds_by_scope"]["satellite_core_mpls_lfib"],
            0.2,
        )
        self.assertEqual(
            summary["bpf_owned_gz_forwarding_candidate"]["command_count"],
            1,
        )

    def test_bpf_owned_backend_skips_only_proven_satellite_core_scope(self) -> None:
        specs = [
            RuntimeCommandSpec(
                node_id="sat-a",
                phase="apply",
                description="install forward LFIB on sat-a for label 16001",
                command=("ip", "-f", "mpls", "route", "replace", "16001", "dev", "lo"),
            ),
            RuntimeCommandSpec(
                node_id="sat-b",
                phase="apply",
                description="install shared reverse LFIB on sat-b for gw1:zone-a:stl-1",
                command=("ip", "-f", "mpls", "route", "replace", "17001", "dev", "lo"),
            ),
            RuntimeCommandSpec(
                node_id="gw1",
                phase="apply",
                description="install gateway SNAT rule",
                command=("iptables", "-w", "10", "-A", "FORWARD", "-j", "ACCEPT"),
            ),
            RuntimeCommandSpec(
                node_id="ut1",
                phase="apply",
                description="install UT default route",
                command=("ip", "route", "replace", "default", "via", "10.0.0.1"),
            ),
        ]
        proof = _prepared_bpf_owned_readiness()

        kept, contract = _plan_backend_apply_contract(
            specs,
            backend_config=_normalize_forwarding_backend_config(
                forwarding_backend="bpf_owned",
                bpf_owned_readiness=proof,
            ),
            contract_phase="apply",
        )

        self.assertEqual(proof["schema_version"], BPF_OWNED_PROOF_SCHEMA_VERSION)
        self.assertTrue(proof["ready"])
        self.assertEqual(proof["generation_id"], 0)
        self.assertEqual(proof["eligible_satellite_core_gz_scope"]["g_to_z_entry_count"], 1)
        self.assertEqual(proof["eligible_satellite_core_gz_scope"]["z_to_g_entry_count"], 1)
        self.assertEqual(proof["map_entry_count"], 2)
        self.assertTrue(proof["map_entry_hash"])
        self.assertIn("sat-a-sat-b", proof["attached_interfaces"])
        self.assertFalse(proof["counter_ready_required"])
        self.assertEqual(len(kept), 2)
        self.assertEqual(contract["skipped_by_bpf_backend_count"], 2)
        self.assertFalse(contract["linux_gz_fallback_enabled"])
        self.assertEqual(contract["linux_gz_fallback_command_count"], 0)
        self.assertEqual(contract["eligible_core_fallback_to_linux"], 0)
        self.assertEqual(contract["fallback_to_linux_count"], 0)
        self.assertEqual(contract["effective_backend_mode"], "bpf_owned")
        self.assertEqual(contract["host_required_count"], 2)
        self.assertEqual(contract["bpf_owned_satellite_core_gz_scope_count"], 2)
        self.assertTrue(contract["bpf_owned_strict_no_linux_gz_fallback_guard"])
        self.assertEqual(contract["gz_core_coverage"]["g_to_z_core_command_count"], 1)
        self.assertEqual(contract["gz_core_coverage"]["z_to_g_core_command_count"], 1)
        self.assertEqual(
            {spec.apply_action for spec in kept},
            {"retained_host_required"},
        )
        self.assertIn(
            "satellite_core_mpls_lfib_residue",
            contract["cleanup_scopes_narrowed_by_bpf_ownership"],
        )

    def test_bpf_owned_backend_rejects_legacy_unstructured_readiness(self) -> None:
        specs = [
            RuntimeCommandSpec(
                node_id="sat-a",
                phase="apply",
                description="install forward LFIB on sat-a for label 16001",
                command=("ip", "-f", "mpls", "route", "replace", "16001", "dev", "lo"),
            )
        ]

        kept, contract = _plan_backend_apply_contract(
            specs,
            backend_config=_normalize_forwarding_backend_config(
                forwarding_backend="bpf_owned",
                bpf_owned_readiness={"ok": True},
            ),
            contract_phase="apply",
        )

        self.assertEqual(len(kept), 0)
        self.assertEqual(contract["skipped_by_bpf_backend_count"], 0)
        self.assertEqual(contract["fallback_to_linux_count"], 0)
        self.assertEqual(contract["linux_gz_fallback_command_count"], 0)
        self.assertEqual(contract["eligible_core_fallback_to_linux"], 0)
        self.assertEqual(contract["bpf_owned_planner_error_count"], 1)
        self.assertEqual(contract["effective_backend_mode"], "bpf_owned_planner_error")
        self.assertFalse(contract["bpf_owned_strict_no_linux_gz_fallback_guard"])
        self.assertEqual(
            contract["bpf_owned_planner_error_reasons"],
            {"bpf_owned_readiness_proof_missing": 1},
        )

    def test_bpf_owned_skips_linux_source_policy_and_policy_table_verify(self) -> None:
        specs = [
            RuntimeCommandSpec(
                node_id="sat-a",
                phase="apply",
                description="install serving-satellite source rule",
                command=("sh", "-lc", "ip rule add from 10.90.3.2/32 table 1003"),
            ),
            RuntimeCommandSpec(
                node_id="sat-a",
                phase="verify",
                description="show serving-satellite policy table",
                command=("ip", "route", "show", "table", "1003"),
            ),
            RuntimeCommandSpec(
                node_id="ut-a",
                phase="apply",
                description="install UT default route",
                command=("ip", "route", "replace", "default", "via", "10.90.3.1"),
            ),
        ]

        kept, contract = _plan_backend_apply_contract(
            specs,
            backend_config=_normalize_forwarding_backend_config(
                forwarding_backend="bpf_owned",
                bpf_owned_readiness=_prepared_bpf_owned_readiness(),
            ),
            contract_phase="verify",
        )

        self.assertEqual([spec.description for spec in kept], ["install UT default route"])
        self.assertEqual(contract["skipped_by_bpf_backend_count"], 2)
        self.assertEqual(contract["eligible_core_fallback_to_linux"], 0)
        self.assertFalse(contract["linux_gz_fallback_enabled"])

    def test_bpf_owned_preflight_and_apply_do_not_generate_linux_mpls_commands(self) -> None:
        context = DirectGatewayExperimentContext(
            flow_id="flow-1",
            ut_id="ut1",
            zone_id="zone-a",
            serving_satellite_id="sat-a",
            selected_gateway_id="gw1",
            selected_gateway_satellite_id="sat-a",
            zone_return_prefixes=("10.90.1.2/32",),
            ping_target_ip="10.160.0.72",
            target_host_node_id="as160-host1",
            policy_table_id=1001,
            policy_rule_priority=1001,
            satellite_path=("sat-a",),
            forward_labels=(16001,),
            reverse_labels=(17001,),
            ut_access_interface="ua-u1s0",
            ut_access_ip="10.90.1.2",
            ut_access_peer_ip="10.90.1.1",
            gateway_access_interface="ga-gw1s0",
            gateway_access_ip="10.91.1.2",
            gateway_access_peer_ip="10.91.1.1",
            gateway_nat_interface="ix100",
            gateway_nat_source_ip="10.242.1.2",
            forward_segments=(),
            reverse_segments=(),
            validation_targets=({"node_id": "as160-host1", "ip_address": "10.160.0.72"},),
        )
        commands = []

        def fake_run(**kwargs: Any) -> linux_mpls_runtime.RuntimeCommandResult:
            command = tuple(kwargs["command"])
            commands.append(command)
            return linux_mpls_runtime.RuntimeCommandResult(
                node_id=str(kwargs["node_id"]),
                phase=str(kwargs["phase"]),
                description=str(kwargs["description"]),
                command=command,
                returncode=1 if command and command[0] == "sh" else 0,
                stdout="default via 10.90.1.1 dev ua-u1s0" if command[:3] == ("ip", "route", "show") else "",
                stderr="",
                allow_failure=bool(kwargs.get("allow_failure")),
            )

        with patch.object(linux_mpls_runtime, "_run_docker_command", fake_run):
            preflight = linux_mpls_runtime._capture_bpf_owned_preflight_state(context)

        self.assertFalse(
            any(tuple(command[:4]) == ("ip", "-f", "mpls", "route") for command in commands)
        )
        self.assertFalse(preflight["legacy_linux_mpls_debug_probe"]["enabled"])
        specs = linux_mpls_runtime.MinimalRealLinuxMplsInstaller()._bpf_owned_apply_specs(
            context,
            preflight,
        )
        self.assertFalse(
            any(tuple(spec.command[:4]) == ("ip", "-f", "mpls", "route") for spec in specs)
        )
        self.assertFalse(
            any(
                tuple(spec.command[:3]) == ("ip", "route", "replace")
                and spec.node_id == context.serving_satellite_id
                for spec in specs
            )
        )

    def test_linux_mpls_backend_can_install_linux_gz_forwarding(self) -> None:
        specs = [
            RuntimeCommandSpec(
                node_id="sat-a",
                phase="apply",
                description="install forward LFIB on sat-a for label 16001",
                command=("ip", "-f", "mpls", "route", "replace", "16001", "dev", "lo"),
            )
        ]

        kept, contract = _plan_backend_apply_contract(
            specs,
            backend_config=_normalize_forwarding_backend_config(
                forwarding_backend="linux_mpls",
            ),
            contract_phase="apply",
        )

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].ownership_status, "linux_owned")
        self.assertEqual(kept[0].apply_action, "applied_by_linux")
        self.assertEqual(contract["linux_owned_count"], 1)
        self.assertEqual(contract["skipped_by_bpf_backend_count"], 0)
        self.assertFalse(contract["linux_gz_fallback_enabled"])

    def test_bpf_canary_backend_retains_linux_apply(self) -> None:
        specs = [
            RuntimeCommandSpec(
                node_id="sat-a",
                phase="apply",
                description="install forward LFIB on sat-a for label 16001",
                command=("ip", "-f", "mpls", "route", "replace", "16001", "dev", "lo"),
            )
        ]

        kept, contract = _plan_backend_apply_contract(
            specs,
            backend_config=_normalize_forwarding_backend_config(
                forwarding_backend="bpf_canary",
            ),
            contract_phase="apply",
        )

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].ownership_status, "shadow_only")
        self.assertEqual(
            kept[0].apply_action,
            "applied_by_linux_shadow_or_canary_only",
        )
        self.assertEqual(contract["shadow_only_count"], 1)
        self.assertEqual(contract["skipped_by_bpf_backend_count"], 0)

    def test_bpf_miss_in_strict_mode_is_classified_as_blocker(self) -> None:
        blocker = _classify_bpf_owned_packet_path_blocker(
            readiness={
                "prepare_steps": {
                    "real_backend_prepare": {
                        "steps": {
                            "map_load": {
                                "loaded_classifier_count": 2,
                                "loaded_core_lfib_count": 2,
                                "loaded_egress_handoff_count": 2,
                            }
                        }
                    }
                }
            },
            bpf_counters={
                "ok": True,
                "counters": {
                    "miss": 3,
                    "redirect": 0,
                    "drop": 0,
                },
            },
        )

        self.assertEqual(blocker, "bpf_mpls_map_miss")

    def test_bpf_path_observability_reports_missing_seen_point(self) -> None:
        readiness = {
            "attached_interface_details": [
                {
                    "interface_name": "vhs-ut",
                    "ifindex": 101,
                    "netns": "host",
                    "attach_directions": ["ingress", "egress"],
                    "expected_roles": ["g_to_z_access_ingress:ingress"],
                },
                {
                    "interface_name": "vhs-gw",
                    "ifindex": 102,
                    "netns": "host",
                    "attach_directions": ["ingress", "egress"],
                    "expected_roles": ["g_to_z_access_ingress:egress"],
                },
            ],
            "resolved_packet_route_entries": [
                {
                    "direction": "g_to_z_access_ingress",
                    "flow_id": "flow-ut42",
                    "ut_id": "ut42",
                    "target_ip": "10.160.42.72",
                    "selected_gateway_id": "gw9",
                    "serving_satellite_id": "sat42",
                    "slot_name": "gw9-gateway-slot0",
                    "ingress_interface": "vhs-ut",
                    "ingress_ifindex": 101,
                    "egress_interface": "vhs-gw",
                    "egress_ifindex": 102,
                }
            ],
        }
        packet_observation = {
            "observations": [
                {
                    "selector": "ut42:10.160.42.72",
                    "matched": True,
                    "flow_id": "flow-ut42",
                    "path_string": "ut42 -> sat42 -> gw9 -> as160-host42",
                    "selected_gateway_id": "gw9",
                    "serving_satellite_id": "sat42",
                    "target_ip_address": "10.160.42.72",
                    "interface_counter_deltas": [
                        {
                            "node_id": "sat42",
                            "interface_name": "ua-u42s0",
                            "endpoint_role": "serving_satellite_ut_access",
                            "ifindex_before": 7,
                            "ifindex_after": 7,
                            "rx_packets_delta": 1,
                            "tx_packets_delta": 0,
                        }
                    ],
                }
            ]
        }
        interface_counters = {
            "ok": True,
            "counters_by_ifindex": {
                "102": {"seen": 1, "redirect": 0},
            },
        }

        with patch.object(
            linux_mpls_runtime,
            "_current_host_ifindex",
            side_effect=lambda name: {"vhs-ut": 101, "vhs-gw": 102}.get(name),
        ):
            summary = _bpf_owned_path_observability_summary(
                readiness=readiness,
                packet_observation=packet_observation,
                interface_counters=interface_counters,
                failed_selectors=["ut42:10.160.42.72"],
            )

        self.assertEqual(summary["expected_path_interface_count"], 2)
        self.assertEqual(summary["attached_expected_path_interface_count"], 2)
        self.assertEqual(summary["missing_attach_interfaces"], [])
        self.assertEqual(summary["per_interface_bpf_seen_count"]["vhs-ut"], 0)
        self.assertEqual(summary["per_interface_bpf_seen_count"]["vhs-gw"], 1)
        self.assertEqual(
            summary["first_missing_observation_point"]["interface_name"],
            "vhs-ut",
        )
        self.assertEqual(
            summary["first_missing_observation_point"]["reason"],
            "attached_interface_no_bpf_seen_packets",
        )
        self.assertFalse(summary["stale_ifindex_detected"])

    def test_bpf_owned_readiness_fails_closed_for_stale_proof(self) -> None:
        stale_proof = _prepared_bpf_owned_readiness(
            _runtime_update_plan(plan_id="rtplan-old")
        )

        proof = _prove_bpf_owned_readiness(
            runtime_update_plan=_runtime_update_plan(plan_id="rtplan-new"),
            bpf_owned_readiness=stale_proof,
        )

        self.assertFalse(proof["ready"])
        self.assertEqual(
            proof["readiness_mismatch_reason"],
            "bpf_owned_readiness_intent_hash_mismatch",
        )

    def test_bpf_owned_readiness_fails_closed_for_partial_scope(self) -> None:
        proof = _prepared_bpf_owned_readiness()
        partial = {
            **proof,
            "z_to_g_entries": [],
            "eligible_satellite_core_gz_scope": {
                **proof["eligible_satellite_core_gz_scope"],
                "z_to_g_entry_count": 0,
            },
            "map_entry_count": len(proof["g_to_z_entries"]),
            "map_entry_hash": "stale",
        }

        checked = _prove_bpf_owned_readiness(
            runtime_update_plan=_runtime_update_plan(),
            bpf_owned_readiness=partial,
        )

        self.assertFalse(checked["ready"])
        self.assertEqual(
            checked["readiness_mismatch_reason"],
            "bpf_owned_readiness_z_to_g_scope_mismatch",
        )

    def test_bpf_owned_prepare_phase_marks_ready_after_compile_attach_and_map_load(self) -> None:
        with patch.object(
            linux_mpls_runtime,
            "_bpf_owned_host_interfaces_for_contexts",
            return_value=["vhs-core-a", "vhs-core-b"],
        ), patch.object(
            linux_mpls_runtime,
            "_bpf_owned_prepare_mpls_backend",
            return_value={
                "ok": True,
                "reason": "",
                "attached_interfaces": ["vhs-core-a", "vhs-core-b"],
                "loaded_gz_fec_count": 2,
                "loaded_runtime_generation_count": 1,
                "active_generation_switch_count": 1,
                "compiled_mpls_map_counts": {
                    "ingress_classifier_map": 2,
                    "core_lfib_map": 2,
                    "egress_handoff_map": 2,
                    "active_generation": 1,
                },
                "resolved_mpls_flow_entries": [],
                "steps": {
                    "compile": {"returncode": 0},
                    "attach": [{"returncode": 0}],
                    "map_load": {
                        "ok": True,
                        "loaded_classifier_count": 2,
                        "loaded_core_lfib_count": 2,
                        "loaded_egress_handoff_count": 2,
                        "active_generation": 0,
                    },
                },
            },
        ):
            proof = _prepare_bpf_owned_readiness(
                runtime_update_plan=_runtime_update_plan(),
                topology_path=Path("topology.json"),
                contexts=(),
                bpf_owned_readiness=None,
            )

        self.assertTrue(proof["ready"])
        self.assertEqual(proof["attached_interfaces"], ["vhs-core-a", "vhs-core-b"])
        self.assertEqual(proof["map_entry_count"], 2)
        self.assertEqual(proof["packet_route_entry_count"], 0)
        self.assertFalse(proof["intermediate_core_lookup_uses_dst_ip"])
        self.assertTrue(proof["runtime_generation_loaded"])
        self.assertTrue(proof["map_ready"])
        self.assertTrue(proof["active_generation_set"])
        self.assertTrue(proof["real_backend_prepared"])

    def test_bpf_owned_runtime_readiness_failure_includes_map_reload_root_cause(
        self,
    ) -> None:
        message = _bpf_owned_runtime_readiness_failure_message(
            {
                "reason": "operational_clean_bpf_map_reload_failed",
                "fallback_reason": "operational_clean_bpf_map_reload_failed",
                "blocked_stage": "bpf_hotpath_map_reload",
                "fail_closed": True,
                "map_reload_root_cause": {
                    "exact_bpf_map": "core_lfib_map",
                    "failed_operation": "update",
                    "generation_id": 7,
                    "direct_error_summary": {
                        "returncode": 22,
                        "stderr": "invalid argument",
                    },
                },
            }
        )

        self.assertIn("blocked_stage=bpf_hotpath_map_reload", message)
        self.assertIn("failed_map=core_lfib_map", message)
        self.assertIn("failed_operation=update", message)
        self.assertIn("generation_id=7", message)
        self.assertIn("returncode=22", message)

    def test_bpf_mpls_compiler_allocates_distinct_labels_for_divergent_fecs(self) -> None:
        def segment(
            link_id: str,
            source: str,
            source_if: str,
            dest: str,
            dest_if: str,
            label: int,
        ) -> MplsPathSegment:
            return MplsPathSegment(
                link_id=link_id,
                source_node_id=source,
                source_node_type="satellite" if source.startswith("sat-") else "gateway",
                source_interface=source_if,
                source_ip="10.0.0.1",
                destination_node_id=dest,
                destination_node_type="satellite" if dest.startswith("sat-") else "gateway",
                destination_interface=dest_if,
                destination_ip="10.0.0.2",
                label=label,
            )

        def context(flow_id: str, gateway: str, last_link: str) -> DirectGatewayExperimentContext:
            ut_suffix = flow_id.rsplit("-", 1)[-1]
            return DirectGatewayExperimentContext(
                flow_id=flow_id,
                ut_id=f"ut{ut_suffix}",
                zone_id="zone-a",
                serving_satellite_id="sat-a",
                selected_gateway_id=gateway,
                selected_gateway_satellite_id="sat-b",
                zone_return_prefixes=("10.90.0.0/24",),
                ping_target_ip="10.154.0.129",
                target_host_node_id="as154-host-new",
                policy_table_id=3001,
                policy_rule_priority=3001,
                satellite_path=("sat-a", "sat-b"),
                forward_labels=(100, 101),
                reverse_labels=(200, 201),
                ut_access_interface=f"ua-u{ut_suffix}s0",
                ut_access_ip="10.90.0.2",
                ut_access_peer_ip="10.90.0.1",
                gateway_access_interface=f"ga-{gateway}s0",
                gateway_access_ip="10.91.0.2",
                gateway_access_peer_ip="10.91.0.1",
                gateway_nat_interface="ix0",
                gateway_nat_source_ip="10.200.0.250",
                forward_segments=(
                    segment("sat-a-sat-b", "sat-a", "ab0", "sat-b", "ab0", 100),
                    segment(last_link, "sat-b", f"ga-{gateway}s0", gateway, f"ga-{gateway}s0", 101),
                ),
                reverse_segments=(
                    segment(f"{gateway}-sat-b", gateway, f"ga-{gateway}s0", "sat-b", f"ga-{gateway}s0", 200),
                    segment("sat-b-sat-a", "sat-b", "ba0", "sat-a", "ba0", 201),
                ),
                validation_targets=({"ip_address": "10.154.0.129", "node_id": "as154-host-new"},),
                traffic_scope="ordinary_internet_default",
            )

        compiled = _bpf_owned_compile_mpls_maps_for_contexts(
            contexts=(
                context("flow-1", "gw1", "sat-b-gw1"),
                context("flow-2", "gw2", "sat-b-gw2"),
            ),
            generation_id=3,
            offline=True,
        )

        self.assertEqual(
            compiled["map_conflict_summary"]["unsafe_conflict_count"],
            0,
        )
        self.assertFalse(compiled["intermediate_core_lookup_uses_dst_ip"])
        first_hop_labels = sorted(
            entry["allocated_label"]
            for entry in compiled["label_allocation"]["entries"]
            if entry["direction"] == "g_to_z" and entry["hop_index"] == 0
        )
        self.assertEqual(len(first_hop_labels), 2)
        self.assertNotEqual(first_hop_labels[0], first_hop_labels[1])

    def test_bpf_owned_prepare_phase_falls_back_when_attach_fails(self) -> None:
        with patch.object(
            linux_mpls_runtime,
            "_bpf_owned_host_interfaces_for_contexts",
            return_value=["vhs-core-a"],
        ), patch.object(
            linux_mpls_runtime,
            "_bpf_owned_prepare_mpls_backend",
            return_value={
                "ok": False,
                "reason": "tc_bpf_mpls_attach_failed",
                "steps": {
                    "compile": {"returncode": 0},
                    "attach": [{"returncode": 2}],
                },
            },
        ):
            proof = _prepare_bpf_owned_readiness(
                runtime_update_plan=_runtime_update_plan(),
                topology_path=Path("topology.json"),
                contexts=(),
                bpf_owned_readiness=None,
            )

        self.assertFalse(proof["ready"])
        self.assertTrue(proof["fallback_to_linux"])
        self.assertEqual(proof["fallback_reason"], "tc_bpf_mpls_attach_failed")

    def test_strict_bpf_owned_map_reload_failure_does_not_full_attach(
        self,
    ) -> None:
        failed_prepare = {
            "ok": False,
            "reason": "operational_clean_bpf_map_reload_failed",
            "fail_closed": True,
            "blocked_stage": "bpf_hotpath_map_reload",
            "full_attach_count": 0,
            "delta_attach_count": 0,
            "reused_attach_count": 2,
            "attach_lifecycle_mode": "normal_epoch_update",
            "attach_reuse": {
                "reuse_eligible": False,
                "reason": "map_reload_failed_before_full_prepare",
            },
            "steps": {
                "reuse_map_load_attempt": {
                    "ok": False,
                    "active_generation_flip_skipped": True,
                },
                "map_reload_root_cause": {
                    "exact_bpf_map": "ingress_classifier_map",
                },
            },
            "map_reload_root_cause": {
                "exact_bpf_map": "ingress_classifier_map",
                "failed_operation": "update",
                "generation_id": 2,
            },
        }
        with patch.object(
            linux_mpls_runtime,
            "_prove_bpf_owned_readiness",
            return_value={"ready": False, "reason": "previous_readiness_stale"},
        ), patch.object(
            linux_mpls_runtime,
            "_bpf_owned_host_interfaces_for_contexts",
            return_value=["vhs-core-a"],
        ), patch.object(
            linux_mpls_runtime,
            "_bpf_owned_prepare_mpls_backend",
            return_value=failed_prepare,
        ) as prepare_mock:
            proof = _prepare_bpf_owned_readiness(
                runtime_update_plan=_runtime_update_plan(),
                topology_path=Path("topology.json"),
                contexts=(),
                bpf_owned_readiness={"ready": True},
                fail_closed_on_reuse_miss=True,
            )

        prepare_mock.assert_called_once()
        self.assertFalse(proof["ready"])
        self.assertTrue(proof["fail_closed"])
        self.assertEqual(proof["blocked_stage"], "bpf_hotpath_map_reload")
        real_prepare = proof["prepare_steps"]["real_backend_prepare"]
        self.assertEqual(real_prepare["full_attach_count"], 0)
        self.assertNotEqual(
            real_prepare.get("attach_lifecycle_mode"),
            "dirty_recovery_reset",
        )
        self.assertNotIn("cleanup_before_attach", real_prepare.get("steps", {}))
        self.assertEqual(
            proof["map_reload_root_cause"]["exact_bpf_map"],
            "ingress_classifier_map",
        )

    def test_bpf_owned_packet_routes_are_general_flow_handoff_entries(self) -> None:
        topology = {
            "slot_inventory": {
                "ut_access": [
                    {
                        "slot_name": "ut42-access-slot0",
                        "slot_type": "ut_access",
                        "preconnected_endpoint": {
                            "node_id": "ut42",
                            "node_type": "user_terminal",
                            "interface_name": "ua-u42s0",
                            "ip_address": "10.90.42.2/29",
                        },
                        "dynamic_endpoint_template": {
                            "node_id": "<runtime-satellite>",
                            "node_type": "satellite",
                            "interface_name": "ua-u42s0",
                            "ip_address": "10.90.42.1/29",
                        },
                    }
                ],
                "gw_access": [
                    {
                        "slot_name": "gw9-gateway-slot0",
                        "slot_type": "gw_access",
                        "preconnected_endpoint": {
                            "node_id": "gw9",
                            "node_type": "gateway",
                            "interface_name": "ga-g9s0",
                            "ip_address": "10.91.9.2/29",
                        },
                        "dynamic_endpoint_template": {
                            "node_id": "<runtime-satellite>",
                            "node_type": "satellite",
                            "interface_name": "ga-g9s0",
                            "ip_address": "10.91.9.1/29",
                        },
                    }
                ],
            }
        }
        context = DirectGatewayExperimentContext(
            flow_id="ut-internet-egress:ut42:secondary",
            ut_id="ut42",
            zone_id="zone-test",
            serving_satellite_id="stl-4242",
            selected_gateway_id="gw9",
            selected_gateway_satellite_id="stl-4242",
            gateway_class_id="zone-test:default-gateway:gw9",
            zone_return_prefixes=("10.90.42.2/32",),
            ping_target_ip="10.160.42.72",
            target_host_node_id="as160-host42",
            policy_table_id=1042,
            policy_rule_priority=1042,
            satellite_path=("stl-4242",),
            forward_labels=(10420,),
            reverse_labels=(20420,),
            ut_access_interface="ua-u42s0",
            ut_access_ip="10.90.42.2",
            ut_access_peer_ip="10.90.42.1",
            gateway_access_interface="ga-g9s0",
            gateway_access_ip="10.91.9.2",
            gateway_access_peer_ip="10.91.9.1",
            gateway_nat_interface="ix209",
            gateway_nat_source_ip="10.242.9.2",
            forward_segments=(
                MplsPathSegment(
                    link_id="gw9-gateway-slot0",
                    source_node_id="stl-4242",
                    source_node_type="satellite",
                    source_interface="ga-g9s0",
                    source_ip="10.91.9.1",
                    destination_node_id="gw9",
                    destination_node_type="gateway",
                    destination_interface="ga-g9s0",
                    destination_ip="10.91.9.2",
                    label=10420,
                ),
            ),
            reverse_segments=(
                MplsPathSegment(
                    link_id="gw9-gateway-slot0",
                    source_node_id="gw9",
                    source_node_type="gateway",
                    source_interface="ga-g9s0",
                    source_ip="10.91.9.2",
                    destination_node_id="stl-4242",
                    destination_node_type="satellite",
                    destination_interface="ga-g9s0",
                    destination_ip="10.91.9.1",
                    label=20420,
                ),
            ),
            validation_targets=(
                {"node_id": "as160-host42", "ip_address": "10.160.42.72"},
                {"node_id": "as154-host42", "ip_address": "10.154.42.129"},
            ),
            return_path_intent={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            topology_path = Path(tmp) / "topology.json"
            topology_path.write_text(json.dumps(topology), encoding="utf-8")
            entries = _bpf_owned_dataplane_entries_for_contexts(
                topology_path=topology_path,
                contexts=(context,),
            )
            attach_interfaces = _bpf_owned_host_interfaces_for_contexts(
                topology_path=topology_path,
                contexts=(context,),
            )

        directions = {entry["direction"] for entry in entries}
        self.assertEqual(
            directions,
            {"g_to_z_access_ingress", "z_to_g", "z_to_g_nat_ingress"},
        )
        self.assertTrue(all(entry["ut_id"] == "ut42" for entry in entries))
        self.assertTrue(all(entry["selected_gateway_id"] == "gw9" for entry in entries))
        self.assertFalse(any("ut3" in json.dumps(entry) for entry in entries))
        self.assertFalse(any("gw5" in json.dumps(entry) for entry in entries))
        access_entries = [
            entry for entry in entries if entry["direction"] == "g_to_z_access_ingress"
        ]
        self.assertEqual(
            {entry["target_ip"] for entry in access_entries},
            {"10.154.42.129", "10.160.42.72"},
        )
        satellite_access_interface = "netns:stl-4242:ua-u42s0"
        self.assertEqual(
            {entry["ingress_interface"] for entry in access_entries},
            {satellite_access_interface},
        )
        self.assertIn(satellite_access_interface, attach_interfaces)
        self.assertNotIn("netns:ut42:ua-u42s0", attach_interfaces)
        nat_entries = [
            entry for entry in entries if entry["direction"] == "z_to_g_nat_ingress"
        ]
        self.assertEqual(len(nat_entries), 1)
        self.assertEqual(nat_entries[0]["target_ip"], "10.242.9.2")
        self.assertEqual(nat_entries[0]["new_dst_ip"], "10.90.42.2")
        self.assertEqual(nat_entries[0]["ingress_interface"], "netns:gw9:ix209")
        self.assertEqual(nat_entries[0]["egress_interface"], "netns:gw9:ga-g9s0")
        handoff_entries = [entry for entry in entries if entry["direction"] == "z_to_g"]
        self.assertEqual(len(handoff_entries), 1)
        self.assertEqual(handoff_entries[0]["ingress_interface"], "netns:stl-4242:ga-g9s0")
        self.assertEqual(handoff_entries[0]["egress_interface"], satellite_access_interface)
        self.assertEqual(handoff_entries[0]["new_dst_ip"], "10.90.42.2")
        self.assertEqual(
            handoff_entries[0]["l2_rewrite"],
            {
                "source_node_id": "stl-4242",
                "source_interface": "ua-u42s0",
                "destination_node_id": "ut42",
                "destination_interface": "ua-u42s0",
            },
        )

    def test_bpf_owned_packet_routes_prefer_override_primary_target(self) -> None:
        topology = {
            "slot_inventory": {
                "ut_access": [
                    {
                        "slot_name": "ut42-access-slot0",
                        "slot_type": "ut_access",
                        "preconnected_endpoint": {
                            "node_id": "ut42",
                            "node_type": "user_terminal",
                            "interface_name": "ua-u42s0",
                            "ip_address": "10.90.42.2/29",
                        },
                        "dynamic_endpoint_template": {
                            "node_id": "<runtime-satellite>",
                            "node_type": "satellite",
                            "interface_name": "ua-u42s0",
                            "ip_address": "10.90.42.1/29",
                        },
                    }
                ],
                "gw_access": [
                    {
                        "slot_name": "gw5-gateway-slot0",
                        "slot_type": "gw_access",
                        "preconnected_endpoint": {
                            "node_id": "gw5",
                            "node_type": "gateway",
                            "interface_name": "ga-g5s0",
                            "ip_address": "10.91.5.2/29",
                        },
                        "dynamic_endpoint_template": {
                            "node_id": "<runtime-satellite>",
                            "node_type": "satellite",
                            "interface_name": "ga-g5s0",
                            "ip_address": "10.91.5.1/29",
                        },
                    },
                    {
                        "slot_name": "gw9-gateway-slot0",
                        "slot_type": "gw_access",
                        "preconnected_endpoint": {
                            "node_id": "gw9",
                            "node_type": "gateway",
                            "interface_name": "ga-g9s0",
                            "ip_address": "10.91.9.2/29",
                        },
                        "dynamic_endpoint_template": {
                            "node_id": "<runtime-satellite>",
                            "node_type": "satellite",
                            "interface_name": "ga-g9s0",
                            "ip_address": "10.91.9.1/29",
                        },
                    },
                ],
            }
        }
        ordinary = DirectGatewayExperimentContext(
            flow_id="ut-internet-egress:ut42",
            ut_id="ut42",
            zone_id="zone-test",
            serving_satellite_id="stl-4242",
            selected_gateway_id="gw5",
            selected_gateway_satellite_id="stl-4242",
            gateway_class_id="zone-test:default-gateway:gw5",
            zone_return_prefixes=("10.90.42.2/32",),
            ping_target_ip="10.154.42.129",
            target_host_node_id="as154-host42",
            policy_table_id=1042,
            policy_rule_priority=1042,
            satellite_path=("stl-4242",),
            forward_labels=(10420,),
            reverse_labels=(20420,),
            ut_access_interface="ua-u42s0",
            ut_access_ip="10.90.42.2",
            ut_access_peer_ip="10.90.42.1",
            gateway_access_interface="ga-g5s0",
            gateway_access_ip="10.91.5.2",
            gateway_access_peer_ip="10.91.5.1",
            gateway_nat_interface="ix205",
            gateway_nat_source_ip="10.242.5.2",
            forward_segments=(
                MplsPathSegment(
                    link_id="gw5-gateway-slot0",
                    source_node_id="stl-4242",
                    source_node_type="satellite",
                    source_interface="ga-g5s0",
                    source_ip="10.91.5.1",
                    destination_node_id="gw5",
                    destination_node_type="gateway",
                    destination_interface="ga-g5s0",
                    destination_ip="10.91.5.2",
                    label=10420,
                ),
            ),
            reverse_segments=(
                MplsPathSegment(
                    link_id="gw5-gateway-slot0",
                    source_node_id="gw5",
                    source_node_type="gateway",
                    source_interface="ga-g5s0",
                    source_ip="10.91.5.2",
                    destination_node_id="stl-4242",
                    destination_node_type="satellite",
                    destination_interface="ga-g5s0",
                    destination_ip="10.91.5.1",
                    label=20420,
                ),
            ),
            validation_targets=(
                {"node_id": "as154-host42", "ip_address": "10.154.42.129"},
                {"node_id": "as160-host42", "ip_address": "10.160.42.72"},
            ),
            return_path_intent={},
            traffic_scope="ordinary_internet_default",
        )
        override = replace(
            ordinary,
            flow_id="ut-internet-egress:ut42:override:asn160_via_selected_secondary_gateway",
            selected_gateway_id="gw9",
            gateway_class_id="zone-test:default-gateway:gw9",
            ping_target_ip="10.160.42.72",
            target_host_node_id="as160-host42",
            gateway_access_interface="ga-g9s0",
            gateway_access_ip="10.91.9.2",
            gateway_access_peer_ip="10.91.9.1",
            gateway_nat_interface="ix209",
            gateway_nat_source_ip="10.242.9.2",
            forward_segments=(
                MplsPathSegment(
                    link_id="gw9-gateway-slot0",
                    source_node_id="stl-4242",
                    source_node_type="satellite",
                    source_interface="ga-g9s0",
                    source_ip="10.91.9.1",
                    destination_node_id="gw9",
                    destination_node_type="gateway",
                    destination_interface="ga-g9s0",
                    destination_ip="10.91.9.2",
                    label=10421,
                ),
            ),
            reverse_segments=(
                MplsPathSegment(
                    link_id="gw9-gateway-slot0",
                    source_node_id="gw9",
                    source_node_type="gateway",
                    source_interface="ga-g9s0",
                    source_ip="10.91.9.2",
                    destination_node_id="stl-4242",
                    destination_node_type="satellite",
                    destination_interface="ga-g9s0",
                    destination_ip="10.91.9.1",
                    label=20421,
                ),
            ),
            validation_targets=(
                {"node_id": "as160-host42", "ip_address": "10.160.42.72"},
            ),
            traffic_scope="special_prefix_override",
        )
        with tempfile.TemporaryDirectory() as tmp:
            topology_path = Path(tmp) / "topology.json"
            topology_path.write_text(json.dumps(topology), encoding="utf-8")
            entries = _bpf_owned_dataplane_entries_for_contexts(
                topology_path=topology_path,
                contexts=(ordinary, override),
            )

        access_160_entries = [
            entry
            for entry in entries
            if entry["direction"] == "g_to_z_access_ingress"
            and entry["target_ip"] == "10.160.42.72"
        ]
        self.assertEqual(len(access_160_entries), 1)
        self.assertEqual(access_160_entries[0]["flow_id"], override.flow_id)
        self.assertEqual(access_160_entries[0]["selected_gateway_id"], "gw9")
        self.assertEqual(access_160_entries[0]["slot_name"], "gw9-gateway-slot0")

    def test_bpf_owned_final_validation_guard_blocks_missing_counters(self) -> None:
        context = DirectGatewayExperimentContext(
            flow_id="ut-internet-egress:ut42",
            ut_id="ut42",
            zone_id="zone-test",
            serving_satellite_id="stl-4242",
            selected_gateway_id="gw9",
            selected_gateway_satellite_id="stl-4242",
            gateway_class_id="zone-test:default-gateway:gw9",
            zone_return_prefixes=("10.90.42.2/32",),
            ping_target_ip="10.160.42.72",
            target_host_node_id="as160-host42",
            policy_table_id=1042,
            policy_rule_priority=1042,
            satellite_path=("stl-4242",),
            forward_labels=(10420,),
            reverse_labels=(20420,),
            ut_access_interface="ua-u42s0",
            ut_access_ip="10.90.42.2",
            ut_access_peer_ip="10.90.42.1",
            gateway_access_interface="ga-g9s0",
            gateway_access_ip="10.91.9.2",
            gateway_access_peer_ip="10.91.9.1",
            gateway_nat_interface="ix209",
            gateway_nat_source_ip="10.242.9.2",
            forward_segments=(),
            reverse_segments=(),
            validation_targets=(
                {"node_id": "as160-host42", "ip_address": "10.160.42.72"},
            ),
            return_path_intent={},
        )
        session = ActiveRuntimeSession(
            session_id="session-test",
            backend={
                "forwarding_backend": "bpf_owned",
                "bpf_owned_readiness": {
                    "ready": True,
                    "active_generation": 7,
                    "resolved_packet_route_entries": [
                        {
                            "flow_id": context.flow_id,
                            "ut_id": context.ut_id,
                            "direction": "g_to_z_access_ingress",
                            "target_ip": "10.160.42.72",
                            "ingress_ifindex": 101,
                            "runtime_generation": 7,
                        },
                        {
                            "flow_id": context.flow_id,
                            "ut_id": context.ut_id,
                            "direction": "z_to_g",
                            "target_ip": "10.90.42.2",
                            "ingress_ifindex": 102,
                            "runtime_generation": 7,
                        },
                    ],
                },
            },
            target_slice={"slice_id": "slice-test"},
            host_runtime_boundary={},
            preflight={},
            apply_results=[],
            verify_results=[],
            ping_results=[],
            timing={},
            contexts=(context,),
            context_preflights=({},),
        )
        with patch.object(
            linux_mpls_runtime,
            "_read_bpf_owned_packet_counters",
            return_value={
                "ok": False,
                "command": {"returncode": 255},
                "map_reference": {"kind": "missing"},
                "counters": {},
            },
        ), patch.object(
            linux_mpls_runtime,
            "_read_bpf_owned_active_generation",
            return_value={
                "ok": True,
                "command": {"returncode": 0},
                "map_reference": {"kind": "pinned"},
                "active_generation": 7,
            },
        ), patch.object(
            linux_mpls_runtime,
            "_bpf_owned_attached_interface_guard",
            return_value={
                "attached_count": 1,
                "details": [],
                "stale_ifindex_count": 0,
            },
        ):
            guard = _bpf_owned_final_validation_guard(
                session,
                primary_only=True,
            )

        self.assertFalse(guard["ok"])
        self.assertEqual(
            guard["failure_reason"],
            "bpf_lifecycle_not_ready_for_validation",
        )
        self.assertIn("bpf_maps_missing", guard["blockers"])
        self.assertIn("bpf_packet_counters_not_readable", guard["blockers"])
        self.assertFalse(guard["bpf_packet_counters_readable_before_final_validation"])

    def test_bpf_owned_packet_routes_skip_ambiguous_shared_nat_handoff(self) -> None:
        topology = {
            "slot_inventory": {
                "ut_access": [],
                "gw_access": [
                    {
                        "slot_name": "gw9-gateway-slot0",
                        "slot_type": "gw_access",
                        "preconnected_endpoint": {
                            "node_id": "gw9",
                            "node_type": "gateway",
                            "interface_name": "ga-g9s0",
                            "ip_address": "10.91.9.2/29",
                        },
                        "dynamic_endpoint_template": {
                            "node_id": "<runtime-satellite>",
                            "node_type": "satellite",
                            "interface_name": "ga-g9s0",
                            "ip_address": "10.91.9.1/29",
                        },
                    }
                ],
            }
        }
        base_context = DirectGatewayExperimentContext(
            flow_id="ut-internet-egress:ut42",
            ut_id="ut42",
            zone_id="zone-test",
            serving_satellite_id="stl-4242",
            selected_gateway_id="gw9",
            selected_gateway_satellite_id="stl-4242",
            gateway_class_id="zone-test:default-gateway:gw9",
            zone_return_prefixes=("10.90.42.2/32",),
            ping_target_ip="10.160.42.72",
            target_host_node_id="as160-host42",
            policy_table_id=1042,
            policy_rule_priority=1042,
            satellite_path=("stl-4242",),
            forward_labels=(10420,),
            reverse_labels=(20420,),
            ut_access_interface="ua-u42s0",
            ut_access_ip="10.90.42.2",
            ut_access_peer_ip="10.90.42.1",
            gateway_access_interface="ga-g9s0",
            gateway_access_ip="10.91.9.2",
            gateway_access_peer_ip="10.91.9.1",
            gateway_nat_interface="ix209",
            gateway_nat_source_ip="10.242.9.2",
            forward_segments=(
                MplsPathSegment(
                    link_id="gw9-gateway-slot0",
                    source_node_id="stl-4242",
                    source_node_type="satellite",
                    source_interface="ga-g9s0",
                    source_ip="10.91.9.1",
                    destination_node_id="gw9",
                    destination_node_type="gateway",
                    destination_interface="ga-g9s0",
                    destination_ip="10.91.9.2",
                    label=10420,
                ),
            ),
            reverse_segments=(
                MplsPathSegment(
                    link_id="gw9-gateway-slot0",
                    source_node_id="gw9",
                    source_node_type="gateway",
                    source_interface="ga-g9s0",
                    source_ip="10.91.9.2",
                    destination_node_id="stl-4242",
                    destination_node_type="satellite",
                    destination_interface="ga-g9s0",
                    destination_ip="10.91.9.1",
                    label=20420,
                ),
            ),
            validation_targets=(
                {"node_id": "as160-host42", "ip_address": "10.160.42.72"},
            ),
            return_path_intent={},
        )
        sibling_context = replace(
            base_context,
            flow_id="ut-internet-egress:ut43",
            ut_id="ut43",
            zone_return_prefixes=("10.90.43.2/32",),
            ping_target_ip="10.160.43.72",
            target_host_node_id="as160-host43",
            policy_table_id=1043,
            policy_rule_priority=1043,
            ut_access_interface="ua-u43s0",
            ut_access_ip="10.90.43.2",
            ut_access_peer_ip="10.90.43.1",
            validation_targets=(
                {"node_id": "as160-host43", "ip_address": "10.160.43.72"},
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            topology_path = Path(tmp) / "topology.json"
            topology_path.write_text(json.dumps(topology), encoding="utf-8")
            entries = _bpf_owned_dataplane_entries_for_contexts(
                topology_path=topology_path,
                contexts=(base_context, sibling_context),
            )

        self.assertFalse(
            any(entry["direction"] == "z_to_g_nat_ingress" for entry in entries)
        )
        handoff_entries = [
            entry for entry in entries if entry["direction"] == "z_to_g"
        ]
        self.assertEqual(
            {entry["ut_id"]: entry["new_dst_ip"] for entry in handoff_entries},
            {"ut42": "10.90.42.2", "ut43": "10.90.43.2"},
        )
        self.assertEqual(
            {entry["egress_interface"] for entry in handoff_entries},
            {"netns:stl-4242:ua-u42s0", "netns:stl-4242:ua-u43s0"},
        )

    def test_gateway_nat_source_ip_for_flow_allocates_unique_host_ips(self) -> None:
        used_ips = {"10.242.207.2"}

        first = _gateway_nat_source_ip_for_flow(
            base_ip="10.242.207.2",
            ut_id="ut2027",
            used_ips=used_ips,
        )
        second = _gateway_nat_source_ip_for_flow(
            base_ip="10.242.207.2",
            ut_id="ut2028",
            used_ips=used_ips,
        )
        repeated = _gateway_nat_source_ip_for_flow(
            base_ip="10.242.207.2",
            ut_id="ut2027",
            used_ips=used_ips,
        )

        self.assertEqual(first, "10.242.207.159")
        self.assertEqual(second, "10.242.207.160")
        self.assertEqual(repeated, "10.242.207.161")
        self.assertEqual(len({first, second, repeated}), 3)
        self.assertNotIn("10.242.207.2", {first, second, repeated})

    def test_bpf_owned_packet_routes_keep_unique_nat_handoffs(self) -> None:
        topology = {
            "slot_inventory": {
                "ut_access": [],
                "gw_access": [
                    {
                        "slot_name": "gw9-gateway-slot0",
                        "slot_type": "gw_access",
                        "preconnected_endpoint": {
                            "node_id": "gw9",
                            "node_type": "gateway",
                            "interface_name": "ga-g9s0",
                            "ip_address": "10.91.9.2/29",
                        },
                        "dynamic_endpoint_template": {
                            "node_id": "<runtime-satellite>",
                            "node_type": "satellite",
                            "interface_name": "ga-g9s0",
                            "ip_address": "10.91.9.1/29",
                        },
                    }
                ],
            }
        }
        base_context = DirectGatewayExperimentContext(
            flow_id="ut-internet-egress:ut42",
            ut_id="ut42",
            zone_id="zone-test",
            serving_satellite_id="stl-4242",
            selected_gateway_id="gw9",
            selected_gateway_satellite_id="stl-4242",
            gateway_class_id="zone-test:default-gateway:gw9",
            zone_return_prefixes=("10.90.42.2/32",),
            ping_target_ip="10.160.42.72",
            target_host_node_id="as160-host42",
            policy_table_id=1042,
            policy_rule_priority=1042,
            satellite_path=("stl-4242",),
            forward_labels=(10420,),
            reverse_labels=(20420,),
            ut_access_interface="ua-u42s0",
            ut_access_ip="10.90.42.2",
            ut_access_peer_ip="10.90.42.1",
            gateway_access_interface="ga-g9s0",
            gateway_access_ip="10.91.9.2",
            gateway_access_peer_ip="10.91.9.1",
            gateway_nat_interface="ix209",
            gateway_nat_source_ip="10.242.9.74",
            forward_segments=(
                MplsPathSegment(
                    link_id="gw9-gateway-slot0",
                    source_node_id="stl-4242",
                    source_node_type="satellite",
                    source_interface="ga-g9s0",
                    source_ip="10.91.9.1",
                    destination_node_id="gw9",
                    destination_node_type="gateway",
                    destination_interface="ga-g9s0",
                    destination_ip="10.91.9.2",
                    label=10420,
                ),
            ),
            reverse_segments=(
                MplsPathSegment(
                    link_id="gw9-gateway-slot0",
                    source_node_id="gw9",
                    source_node_type="gateway",
                    source_interface="ga-g9s0",
                    source_ip="10.91.9.2",
                    destination_node_id="stl-4242",
                    destination_node_type="satellite",
                    destination_interface="ga-g9s0",
                    destination_ip="10.91.9.1",
                    label=20420,
                ),
            ),
            validation_targets=(
                {"node_id": "as160-host42", "ip_address": "10.160.42.72"},
            ),
            return_path_intent={},
        )
        sibling_context = replace(
            base_context,
            flow_id="ut-internet-egress:ut43",
            ut_id="ut43",
            zone_return_prefixes=("10.90.43.2/32",),
            ping_target_ip="10.160.43.72",
            target_host_node_id="as160-host43",
            policy_table_id=1043,
            policy_rule_priority=1043,
            ut_access_interface="ua-u43s0",
            ut_access_ip="10.90.43.2",
            ut_access_peer_ip="10.90.43.1",
            gateway_nat_source_ip="10.242.9.75",
            validation_targets=(
                {"node_id": "as160-host43", "ip_address": "10.160.43.72"},
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            topology_path = Path(tmp) / "topology.json"
            topology_path.write_text(json.dumps(topology), encoding="utf-8")
            entries = _bpf_owned_dataplane_entries_for_contexts(
                topology_path=topology_path,
                contexts=(base_context, sibling_context),
            )

        nat_entries = [
            entry for entry in entries if entry["direction"] == "z_to_g_nat_ingress"
        ]
        self.assertEqual(
            {entry["target_ip"]: entry["new_dst_ip"] for entry in nat_entries},
            {"10.242.9.74": "10.90.42.2", "10.242.9.75": "10.90.43.2"},
        )
        self.assertEqual(
            {entry["ingress_interface"] for entry in nat_entries},
            {"netns:gw9:ix209"},
        )

    def test_bpf_owned_backend_rejects_logical_only_readiness(self) -> None:
        specs = [
            RuntimeCommandSpec(
                node_id="sat-a",
                phase="apply",
                description="install forward LFIB on sat-a for label 16001",
                command=("ip", "-f", "mpls", "route", "replace", "16001", "dev", "lo"),
            )
        ]
        proof = _derive_bpf_owned_readiness_proof(
            runtime_update_plan=_runtime_update_plan(),
        )
        checked = _prove_bpf_owned_readiness(
            runtime_update_plan=_runtime_update_plan(),
            bpf_owned_readiness={
                **proof,
                "ready": True,
                "ok": True,
                "reason": "",
                "fallback_reason": "",
                "fallback_to_linux": False,
                "runtime_generation_loaded": True,
                "active_generation_set": True,
                "map_ready": True,
            },
        )

        kept, contract = _plan_backend_apply_contract(
            specs,
            backend_config=_normalize_forwarding_backend_config(
                forwarding_backend="bpf_owned",
                bpf_owned_readiness=checked,
            ),
            contract_phase="apply",
        )

        self.assertFalse(checked["ready"])
        self.assertEqual(
            checked["readiness_mismatch_reason"],
            "bpf_owned_real_prepare_not_proven",
        )
        self.assertEqual(len(kept), 0)
        self.assertEqual(contract["skipped_by_bpf_backend_count"], 0)
        self.assertEqual(
            contract["effective_backend_mode"],
            "bpf_owned_planner_error",
        )
        self.assertFalse(contract["bpf_owned_strict_no_linux_gz_fallback_guard"])
        self.assertFalse(contract["linux_gz_fallback_enabled"])
        self.assertEqual(contract["linux_gz_fallback_command_count"], 0)
        self.assertEqual(contract["eligible_core_fallback_to_linux"], 0)
        self.assertEqual(contract["bpf_owned_planner_error_count"], 1)
        self.assertEqual(
            contract["bpf_owned_planner_error_reasons"],
            {"bpf_owned_real_prepare_not_proven": 1},
        )

    def test_bpf_owned_unproven_readiness_is_planner_error_not_linux_fallback(self) -> None:
        specs = [
            RuntimeCommandSpec(
                node_id="sat-a",
                phase="apply",
                description="install forward LFIB on sat-a for label 16001",
                command=("ip", "-f", "mpls", "route", "replace", "16001", "dev", "lo"),
            )
        ]

        kept, contract = _plan_backend_apply_contract(
            specs,
            backend_config=_normalize_forwarding_backend_config(
                forwarding_backend="bpf_owned",
                bpf_owned_readiness={"ok": False, "reason": "bpf_map_load_failed"},
            ),
            contract_phase="apply",
        )

        self.assertEqual(len(kept), 0)
        self.assertEqual(contract["skipped_by_bpf_backend_count"], 0)
        self.assertEqual(contract["fallback_to_linux_count"], 0)
        self.assertEqual(
            contract["effective_backend_mode"],
            "bpf_owned_planner_error",
        )
        self.assertFalse(contract["bpf_owned_strict_no_linux_gz_fallback_guard"])
        self.assertFalse(contract["linux_gz_fallback_enabled"])
        self.assertEqual(contract["linux_gz_fallback_command_count"], 0)
        self.assertEqual(contract["eligible_core_fallback_to_linux"], 0)
        self.assertEqual(contract["bpf_owned_planner_error_count"], 1)
        self.assertEqual(
            contract["bpf_owned_planner_error_reasons"],
            {"bpf_map_load_failed": 1},
        )

    def test_bpf_shadow_backend_retains_linux_apply(self) -> None:
        specs = [
            RuntimeCommandSpec(
                node_id="sat-a",
                phase="apply",
                description="install forward LFIB on sat-a for label 16001",
                command=("ip", "-f", "mpls", "route", "replace", "16001", "dev", "lo"),
            )
        ]

        kept, contract = _plan_backend_apply_contract(
            specs,
            backend_config=_normalize_forwarding_backend_config(
                forwarding_backend="bpf_shadow",
            ),
            contract_phase="apply",
        )

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].ownership_status, "shadow_only")
        self.assertEqual(
            kept[0].apply_action,
            "applied_by_linux_shadow_or_canary_only",
        )
        self.assertEqual(contract["shadow_only_count"], 1)
        self.assertEqual(contract["skipped_by_bpf_backend_count"], 0)

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
        installer._prepared_resource_keys_by_slot = {"slot-a": {"resource-a"}}

        summary = installer.cleanup_runtime_artifacts(
            topology_path=Path("topology.json"),
            link_state_path=Path("link_state.json"),
        )

        metrics = summary["cleanup_metrics"]
        self.assertTrue(summary["cleanup_completed"])
        self.assertEqual(metrics["duplicate_cleanup_skipped_count"], 4)
        self.assertEqual(metrics["skipped_noop_count"], 4)
        self.assertEqual(metrics["cleanup_resource_attempted_count"], 8)
        self.assertEqual(metrics["cleanup_resource_succeeded_count"], 4)
        self.assertEqual(summary["removed_host_interface_count"], 1)
        self.assertEqual(summary["removed_temp_host_interface_count"], 1)
        self.assertEqual(summary["removed_container_interface_count"], 1)
        self.assertEqual(summary["removed_bridge_count"], 1)
        self.assertEqual(
            metrics["cleanup_scope_source"],
            "prepared_owned_resource_index",
        )
        self.assertTrue(metrics["bounded_owned_scope"])
        self.assertIn("CLEANUP_BOUNDED_OWNED_SCOPE_USED", metrics["outcome_labels"])
        self.assertIn("CLEANUP_NO_GLOBAL_EXISTENCE_SCAN", metrics["outcome_labels"])
        self.assertEqual(metrics["cleanup_existence_probe_count"], 0)
        self.assertFalse(metrics["global_scan_diagnostic_only"])
        self.assertEqual(installer.host_interface_scan_count, 0)
        self.assertEqual(installer.container_interface_scan_count, 0)

    def test_host_artifact_cleanup_missing_owned_index_blocks_global_fallback(self) -> None:
        installer = _FakeCleanupInstaller()

        summary = installer.cleanup_runtime_artifacts(
            topology_path=Path("topology.json"),
            link_state_path=Path("link_state.json"),
        )

        metrics = summary["cleanup_metrics"]
        self.assertFalse(summary["cleanup_completed"])
        self.assertEqual(summary["reason"], "OWNED_RESOURCE_INDEX_MISSING_BLOCKER")
        self.assertEqual(
            metrics["cleanup_scope_source"],
            "missing_prepared_owned_resource_index",
        )
        self.assertFalse(metrics["full_topology_candidate_inventory_fallback"])
        self.assertIn("OWNED_RESOURCE_INDEX_MISSING_BLOCKER", metrics["outcome_labels"])
        self.assertNotIn("NORMAL_PATH_OWNED_SCOPE_ONLY", metrics["outcome_labels"])
        self.assertIn("CLEANUP_NO_GLOBAL_EXISTENCE_SCAN", metrics["outcome_labels"])
        self.assertEqual(metrics["cleanup_execution_model"], "blocked_missing_owned_resource_index")
        self.assertFalse(metrics["global_scan_diagnostic_only"])

    def test_additive_prepare_success_schema_is_explicit_for_non_empty_delta(
        self,
    ) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        installer._runtime_prepared = True
        resource_key = installer._resource_key(
            "ut_access",
            {"satellite_id": "sat-a"},
        )
        topology = {
            "slot_inventory": {
                "ut_access": [
                    {
                        "slot_name": "ut2001-access-slot0",
                        "slot_type": "ut_access",
                        "subnet": "10.0.0.0/30",
                        "candidate_satellite_ids": ["sat-a"],
                        "dynamic_endpoint_template": {
                            "node_type": "satellite",
                            "interface_name": "ut2001",
                            "ip_address": "10.0.0.2/30",
                        },
                    }
                ]
            }
        }

        with (
            patch.object(installer, "_load_topology", return_value=topology),
            patch.object(installer, "_prepare_bridge_netfilter_passthrough"),
            patch.object(installer, "_host_interface_name_set", return_value=set()),
            patch.object(
                installer,
                "_container_interface_name_set",
                return_value=set(),
            ),
            patch.object(installer, "_bridge_exists", return_value=False),
            patch.object(installer, "_set_bridge_ready"),
            patch.object(installer, "_create_prepared_veth"),
        ):
            summary = installer.prepare_additive_resources(
                topology_path=Path("topology.json"),
                link_state_path=Path("link_state.json"),
                prepared_resource_scope_by_slot={
                    "ut2001-access-slot0": {resource_key},
                },
            )

        self.assertIs(summary["ok"], True)
        self.assertIs(summary["additive_prepare_completed"], True)
        self.assertFalse(summary["full_sweep"])
        self.assertEqual(summary["slot_count"], 1)
        self.assertEqual(summary["candidate_resource_count"], 1)
        self.assertEqual(
            summary["prepared_resource_scope_by_slot"],
            {"ut2001-access-slot0": [resource_key]},
        )

    def test_normal_runtime_apply_requires_explicit_owned_scope(self) -> None:
        manager = InProcessHostRuntimeManager()

        with self.assertRaisesRegex(
            NetworkMutationExecutionError,
            "OWNED_RESOURCE_INDEX_MISSING_BLOCKER",
        ):
            manager.apply(
                topology_path=Path("topology.json"),
                link_state_path=Path("link_state.json"),
                network_mutation_plan=_network_mutation_plan(),
                reconcile_slot_names=None,
            )

    def test_normal_runtime_apply_uses_trusted_fast_path_without_state_rescan(
        self,
    ) -> None:
        manager = InProcessHostRuntimeManager()
        operation = {
            "operation": "update_slot_parameters",
            "slot_name": "slot-a",
            "slot_type": "ut_access",
            "execution_domain": "host_network_runtime",
            "source_occupied": True,
            "target_occupied": True,
            "source_binding": {"satellite_id": "sat-a"},
            "target_binding": {"satellite_id": "sat-b"},
        }
        envelope = SimpleNamespace(
            observed_state={"slot-a": {"slot_name": "slot-a"}},
            operations=[operation],
            parallelism={
                "requested_workers": 1,
                "effective_workers": 1,
                "parallel_execution_used": False,
            },
            managed_slot_names=["slot-a"],
            envelope_id="env-1",
        )
        trusted_apply_verification = manager._trusted_plan_state_verification(
            operations=[operation],
            source=False,
            reason="test_trusted_apply",
        )

        with (
            patch.object(
                manager._installer,
                "controller",
                return_value=SimpleNamespace(
                    topology_path="topology.json",
                    link_state_path="link_state.json",
                ),
            ),
            patch.object(manager, "_prepare_envelope", return_value=(envelope, {})),
            patch.object(
                manager,
                "_verify_plan_state",
                side_effect=AssertionError("hot path state rescan must not run"),
            ) as verify_plan_state,
            patch.object(
                manager,
                "_apply_operations",
                return_value=(
                    [{"operation": "update_slot_parameters", "ok": True}],
                    [operation],
                    trusted_apply_verification,
                    {
                        "operation_apply_wall_seconds": 0.01,
                        "post_apply_state_refresh_wall_seconds": 0.0,
                        "post_apply_verification_wall_seconds": 0.0,
                    },
                    {
                        "requested_workers": 1,
                        "effective_workers": 1,
                        "parallel_execution_used": False,
                    },
                ),
            ),
        ):
            result = manager.apply(
                topology_path=Path("topology.json"),
                link_state_path=Path("link_state.json"),
                network_mutation_plan=_network_mutation_plan(),
                reconcile_slot_names=["slot-a"],
            )

        payload = result.to_dict()
        verify_plan_state.assert_not_called()
        self.assertTrue(payload["preflight"]["verification_skipped"])
        self.assertTrue(payload["apply_verification"]["verification_skipped"])
        timing = payload["outcome"]["manager_timing_breakdown"]
        self.assertEqual(timing["preflight_verification_wall_seconds"], 0.0)
        self.assertEqual(timing["post_apply_verification_wall_seconds"], 0.0)
        self.assertEqual(timing["manager_resync_reconciliation_wall_seconds"], 0.0)
        self.assertTrue(payload["outcome"]["apply_success"])

    def test_apply_operations_success_uses_cached_state_without_post_rescan(
        self,
    ) -> None:
        manager = InProcessHostRuntimeManager()
        operation = {
            "operation": "update_slot_parameters",
            "slot_name": "slot-a",
            "slot_type": "ut_access",
            "execution_domain": "host_network_runtime",
            "source_occupied": True,
            "target_occupied": True,
            "source_binding": {"satellite_id": "sat-a"},
            "target_binding": {"satellite_id": "sat-b"},
        }
        working_state = {
            "slot-a": {
                "slot_name": "slot-a",
                "slot_type": "ut_access",
                "occupied": True,
                "binding": {"satellite_id": "sat-a"},
                "endpoints": [],
                "runtime_parameters": {},
            }
        }

        with (
            patch.object(
                manager._installer,
                "apply_operation",
                return_value={"operation": "update_slot_parameters", "ok": True},
            ),
            patch.object(manager._installer, "persist_runtime_state"),
            patch.object(
                manager,
                "_verify_plan_state",
                side_effect=AssertionError("post-apply state rescan must not run"),
            ) as verify_plan_state,
        ):
            (
                apply_results,
                applied_operations,
                apply_verification,
                timing,
                _parallel,
            ) = manager._apply_operations(
                controller=SimpleNamespace(),
                link_state_path=Path("link_state.json"),
                operations=[operation],
                working_state=working_state,
                parallelism={
                    "requested_workers": 1,
                    "effective_workers": 1,
                    "parallel_execution_used": False,
                    "execution_groups": [
                        {
                            "group_index": 0,
                            "operation_indexes": [0],
                            "operation_count": 1,
                            "parallelized": False,
                        }
                    ],
                },
            )

        verify_plan_state.assert_not_called()
        self.assertEqual(len(apply_results), 1)
        self.assertEqual(len(applied_operations), 1)
        self.assertTrue(apply_verification["all_valid"])
        self.assertTrue(apply_verification["verification_skipped"])
        self.assertEqual(timing["post_apply_verification_wall_seconds"], 0.0)

    def test_normal_runtime_apply_blocks_hidden_reconcile_repair(self) -> None:
        manager = InProcessHostRuntimeManager()
        manager._last_mutation_id = "previous-mutation"
        envelope = SimpleNamespace(
            observed_state={},
            operations=[
                {
                    "operation": "update_slot_parameters",
                    "slot_name": "slot-a",
                    "execution_domain": "host_network_runtime",
                }
            ],
            parallelism={
                "requested_workers": 1,
                "effective_workers": 1,
                "parallel_execution_used": False,
            },
            managed_slot_names=["slot-a"],
            envelope_id="env-1",
        )

        with (
            patch.object(
                manager._installer,
                "controller",
                return_value=SimpleNamespace(
                    topology_path="topology.json",
                    link_state_path="link_state.json",
                ),
            ),
            patch.object(manager, "_prepare_envelope", return_value=(envelope, {})),
            patch.object(
                manager,
                "_verify_plan_state",
                side_effect=[
                    {"all_valid": True},
                    {"all_valid": False, "reason": "target_not_updated"},
                ],
            ),
            patch.object(
                manager,
                "_apply_operations",
                side_effect=RuntimeError("transient stale state"),
            ),
            patch.object(manager, "_refresh_runtime_state", return_value={}),
        ):
            result = manager.apply(
                topology_path=Path("topology.json"),
                link_state_path=Path("link_state.json"),
                network_mutation_plan=_network_mutation_plan(),
                reconcile_slot_names=["slot-a"],
            )

        outcome = result.to_dict()["outcome"]
        self.assertFalse(outcome["apply_success"])
        self.assertIn("FULL_SCOPE_RECOVERY_BLOCKER", outcome["failure_reason"])
        scope_contract = outcome["normal_path_scope_contract"]
        self.assertTrue(scope_contract["hidden_repair_blocked"])
        self.assertFalse(scope_contract["full_scope_recovery_used"])
        self.assertIn("NORMAL_PATH_OWNED_SCOPE_ONLY", scope_contract["outcome_labels"])
        self.assertIn("NORMAL_PATH_NO_HIDDEN_REPAIR", scope_contract["outcome_labels"])
        self.assertIn("FULL_SCOPE_RECOVERY_BLOCKER", scope_contract["outcome_labels"])
        self.assertIn(
            "NOOP_FULL_SCOPE_RECONCILE_BLOCKER",
            scope_contract["outcome_labels"],
        )
        self.assertIn("GLOBAL_SCAN_DIAGNOSTIC_ONLY", scope_contract["outcome_labels"])

    def test_minimal_executor_derives_explicit_owned_scope_from_plan(self) -> None:
        executor = MinimalRealNetworkMutationExecutor()

        with patch.object(executor._manager, "apply") as manager_apply:
            executor.apply(
                topology_path=Path("topology.json"),
                link_state_path=Path("link_state.json"),
                network_mutation_plan=_network_mutation_plan(),
            )

        self.assertEqual(
            manager_apply.call_args.kwargs["reconcile_slot_names"],
            ["slot-a"],
        )

    def test_host_artifact_cleanup_uses_prepared_owned_resource_scope(self) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        topology = {
            "slot_inventory": {
                "cross_plane_isl": [
                    {
                        "slot_name": "cross-slot0",
                        "slot_type": "cross_plane_isl",
                        "candidate_pairs": [
                            {
                                "satellite_a_direction": "east",
                                "satellite_a_id": "sat-a",
                                "satellite_b_direction": "west",
                                "satellite_b_id": "sat-b",
                            },
                            {
                                "satellite_a_direction": "east",
                                "satellite_a_id": "sat-c",
                                "satellite_b_direction": "west",
                                "satellite_b_id": "sat-d",
                            },
                        ],
                        "endpoint_a_template": {
                            "node_type": "satellite",
                            "interface_name": "xp0a",
                            "ip_address": "10.0.0.2",
                        },
                        "endpoint_b_template": {
                            "node_type": "satellite",
                            "interface_name": "xp0b",
                            "ip_address": "10.0.0.3",
                        },
                    }
                ]
            }
        }
        first_key = installer._resource_key(
            "cross_plane_isl",
            {
                "satellite_a_direction": "east",
                "satellite_a_id": "sat-a",
                "satellite_b_direction": "west",
                "satellite_b_id": "sat-b",
            },
        )

        inventory = installer._runtime_artifact_inventory(
            topology=topology,
            topology_path=Path("topology.json"),
            prepared_resource_scope_by_slot={"cross-slot0": {first_key}},
        )

        self.assertTrue(inventory["bounded_owned_scope"])
        self.assertEqual(
            inventory["cleanup_scope_source"],
            "prepared_owned_resource_index",
        )
        self.assertEqual(inventory["prepared_slot_scope_count"], 1)
        self.assertEqual(
            inventory["inventory_scope"],
            "prepared_owned_resource_index_slots",
        )
        self.assertEqual(inventory["missing_slot_names"], [])
        self.assertEqual(len(inventory["bridge_names"]), 1)
        self.assertEqual(len(inventory["host_interface_names"]), 2)
        self.assertEqual(len(inventory["temp_host_interface_names"]), 2)
        self.assertEqual(len(inventory["container_interface_names"]), 2)

    def test_compose_down_uses_run_owned_project_and_file(self) -> None:
        import run_starlink_shell_runtime_attempt

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "seed_output"
            output_dir.mkdir()
            compose_file = output_dir / "docker-compose.yml"
            compose_file.write_text("services: {}\n", encoding="utf-8")
            (output_dir / ".env").write_text(
                "COMPOSE_PROJECT_NAME=seed_output\n",
                encoding="utf-8",
            )

            completed = linux_mpls_runtime.subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="",
                stderr="",
            )
            with patch.object(
                run_starlink_shell_runtime_attempt.subprocess,
                "run",
                return_value=completed,
            ) as run:
                result = run_starlink_shell_runtime_attempt._compose_down(output_dir)

        self.assertIsNotNone(result)
        command = run.call_args.args[0]
        self.assertEqual(
            command,
            [
                "docker-compose",
                "-f",
                str(compose_file),
                "-p",
                "seed_output",
                "down",
                "--remove-orphans",
                "-v",
            ],
        )
        self.assertEqual(run.call_args.kwargs["cwd"], str(output_dir.parent))

    def test_compose_project_residue_cleanup_is_project_label_bounded(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            rendered = [str(part) for part in command]
            calls.append(rendered)
            if rendered[:3] == ["docker", "ps", "-aq"]:
                return linux_mpls_runtime.subprocess.CompletedProcess(
                    args=rendered,
                    returncode=0,
                    stdout="container-a\ncontainer-b\n",
                    stderr="",
                )
            if rendered[:3] == ["docker", "rm", "-f"]:
                return linux_mpls_runtime.subprocess.CompletedProcess(
                    args=rendered,
                    returncode=0,
                    stdout="\n".join(rendered[3:]) + "\n",
                    stderr="",
                )
            if rendered[:4] == ["docker", "network", "ls", "-q"]:
                return linux_mpls_runtime.subprocess.CompletedProcess(
                    args=rendered,
                    returncode=0,
                    stdout="network-a\n",
                    stderr="",
                )
            if rendered[:3] == ["docker", "network", "rm"]:
                return linux_mpls_runtime.subprocess.CompletedProcess(
                    args=rendered,
                    returncode=0,
                    stdout="network-a\n",
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {rendered}")

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "seed_output"
            output_dir.mkdir()
            (output_dir / ".env").write_text(
                "COMPOSE_PROJECT_NAME=seed_output\n",
                encoding="utf-8",
            )
            with patch("run_starlink_shell_experiment.subprocess.run", side_effect=fake_run):
                cleanup = _compose_force_remove_project_residue(output_dir)

        self.assertTrue(cleanup["bounded_by_compose_project_label"])
        self.assertTrue(cleanup["cleanup_completed"])
        self.assertEqual(cleanup["removed_container_count"], 2)
        self.assertEqual(cleanup["removed_network_count"], 1)
        self.assertIn(
            "label=com.docker.compose.project=seed_output",
            calls[0],
        )
        self.assertEqual(calls[1], ["docker", "rm", "-f", "container-a", "container-b"])
        self.assertIn(
            "label=com.docker.compose.project=seed_output",
            calls[2],
        )
        self.assertEqual(calls[3], ["docker", "network", "rm", "network-a"])

    def test_stale_network_scrub_uses_compose_project_label_filter(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            rendered = [str(part) for part in command]
            calls.append(rendered)
            if rendered[:3] == ["docker", "network", "ls"]:
                return linux_mpls_runtime.subprocess.CompletedProcess(
                    args=rendered,
                    returncode=0,
                    stdout="seed_output_net\tseed_output\n",
                    stderr="",
                )
            if rendered[:3] == ["docker", "network", "inspect"]:
                return linux_mpls_runtime.subprocess.CompletedProcess(
                    args=rendered,
                    returncode=0,
                    stdout=json.dumps([{"Containers": {}}]),
                    stderr="",
                )
            if rendered[:3] == ["docker", "network", "rm"]:
                return linux_mpls_runtime.subprocess.CompletedProcess(
                    args=rendered,
                    returncode=0,
                    stdout="seed_output_net\n",
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {rendered}")

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "seed_output"
            output_dir.mkdir()
            (output_dir / ".env").write_text(
                "COMPOSE_PROJECT_NAME=seed_output\n",
                encoding="utf-8",
            )
            with patch("run_starlink_shell_experiment.subprocess.run", side_effect=fake_run):
                result = _compose_force_remove_stale_networks(output_dir)

        self.assertTrue(result["bounded_by_compose_project_label"])
        self.assertFalse(result["full_docker_network_scan"])
        self.assertIn("--filter", calls[0])
        self.assertIn("label=com.docker.compose.project=seed_output", calls[0])
        self.assertEqual(result["removed_networks"], ["seed_output_net"])


if __name__ == "__main__":
    unittest.main()
