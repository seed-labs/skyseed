import json
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from horizon_maintenance import (
    MaintenanceLock,
    READY_ARTIFACT_NAME,
    HorizonMaintenanceRequest,
    base_compiled_inputs_ready,
    classify_horizon_maintenance,
    load_ready_artifact,
    publish_horizon_maintenance_result,
    retired_cleanup_candidate_ready,
    retired_cleanup_dependency_report,
    successor_maintenance_gate,
)
from horizon_maintenance_process_worker import (
    _attach_report_uses_resident_budget,
    _base_generation_interface_binding_caches,
    _freeze_preload_work_item,
    _publish_process_exception_failure,
    _union_frozen_inputs,
    _validate_final_interface_binding_scope,
    run_horizon_maintenance_process_payload,
)
from linux_mpls_runtime import (
    _execute_bpf_delta_attach_tasks,
    _prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs,
)
from apps.audit_horizon_maintenance_process import audit_horizon_maintenance_process
from apps.run_starlink_shell_experiment import (
    _generation_id_matches_explicit_value,
    _terminate_horizon_maintenance_process_workers_for_cleanup,
)
from network_runtime import InProcessHostRuntimeManager


def _compiled_inputs() -> dict:
    return {
        "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
        "active_generation": {"value": 7},
        "ingress_classifier_map": {"path": "ingress.manifest.json"},
                "ut_gateway_policy_map": [],
                "gateway_selector_ingress_map": [],
        "core_lfib_map": {"path": "core.manifest.json"},
        "egress_handoff_map": {"path": "egress.manifest.json"},
                "zone_edge_delivery_map": [],
    }


def _readiness(interface_ref: str = "sat1:if0") -> dict:
    detail = {
        "interface_ref": interface_ref,
        "interface_name": interface_ref,
        "ifindex": 101,
        "mac_address": "02:00:00:00:00:01",
        "expected_roles": ["g_to_z_classifier"],
        "attach_directions": ["ingress"],
    }
    return {
        "ok": True,
        "ready": True,
        "attach_plan": {interface_ref: ["ingress"]},
        "attached_interfaces": [interface_ref],
        "attached_interface_details": [detail],
        "compiled_runtime_inputs": {
            "frozen_interface_binding_cache": {
                "ifindex_by_interface_ref": {interface_ref: 101},
                "mac_by_interface_ref": {
                    interface_ref: "02:00:00:00:00:01"
                },
                "complete": True,
                "live_discovery_allowed": False,
            }
        },
        "prepare_steps": {
            "real_backend_prepare": {
                "attach_plan": {interface_ref: ["ingress"]},
                "attached_interfaces": [interface_ref],
                "attached_interface_details": [detail],
            }
        },
    }


def _base_record() -> dict:
    compiled_inputs = _compiled_inputs()
    compiled_inputs["frozen_interface_binding_cache"] = dict(
        _readiness()["compiled_runtime_inputs"]["frozen_interface_binding_cache"]
    )
    return {
        "ok": True,
        "generation_id": 7,
        "physical_generation_id": 7,
        "source": "readiness.active_generation_compiled_runtime_inputs",
        "compiled_runtime_inputs": compiled_inputs,
    }


def _request(
    expected_preload_count: int = 1,
    *,
    previous_horizon_id: str = "",
) -> HorizonMaintenanceRequest:
    return HorizonMaintenanceRequest(
        segment_index=1,
        segment_id="segment_001",
        current_horizon_id="H0",
        next_horizon_id="H1",
        previous_horizon_id=previous_horizon_id,
        source_state_id="live_state:H0",
        base_generation_id=7,
        prepared_generation_ids=(7, 8),
        expected_preload_count=expected_preload_count,
        worker_mode="process",
    )


def _write_worker_input_files(directory: Path) -> tuple[str, str]:
    topology_path = directory / "topology.json"
    link_state_path = directory / "link_state.json"
    topology_path.write_text("{}", encoding="utf-8")
    link_state_path.write_text("{}", encoding="utf-8")
    return str(topology_path), str(link_state_path)


def _frozen_container_pid_cache() -> dict:
    return {"node-a": 4242}


def _worker_context() -> dict:
    return {
        "snapshot": {
            "schema_version": "skyseed-topology-snapshot-v1",
            "snapshot_id": "slice-001",
            "time_slice": {"slice_id": "slice-001"},
        },
        "runtime_update_plan": {
            "schema_version": "skyseed-runtime-update-plan-v1",
            "plan_id": "plan-001",
            "target_snapshot": {"snapshot_id": "slice-001"},
            "planner": {},
            "apply_model": {},
            "node_subplans": {},
            "replacement_semantics": {},
            "validation_expectations": {},
        },
    }


def _externalize_worker_contexts(payload: dict, directory: Path) -> None:
    items = [dict(item) for item in list(payload.get("preload_work_items") or [])]
    artifact_value = str(payload.get("preload_work_items_artifact") or "")
    if not items and artifact_value:
        items = [
            dict(json.loads(line))
            for line in Path(artifact_value).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    context_records = []
    compact_items = []
    for index, item in enumerate(items):
        queued = dict(item.get("queued_record") or {})
        key = str(
            queued.get("segment_epoch_key")
            or dict(item.get("context_ref") or {}).get("segment_epoch_key")
            or f"{payload.get('segment_id') or 'segment_001'}:unit-{index:04d}"
        )
        queued.setdefault("segment_epoch_key", key)
        item["queued_record"] = queued
        context = dict(item.pop("context", {}) or {})
        if key and context:
            context_records.append(
                {"segment_epoch_key": key, "slice_id": "slice-001", "context": context}
            )
        item["context_ref"] = {
            "schema_version": "skyseed-horizon-maintenance-context-ref-v1",
            "segment_epoch_key": key,
        }
        compact_items.append(item)
    context_path = directory / "horizon_maintenance_contexts.jsonl"
    encoded = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        for record in context_records
    ).encode("utf-8")
    context_path.write_bytes(encoded)
    payload["schema_version"] = "skyseed-horizon-maintenance-process-work-v3"
    payload["preload_work_items"] = compact_items
    payload.pop("preload_work_items_artifact", None)
    payload["preload_context_artifact"] = {
        "schema_version": "skyseed-horizon-maintenance-context-artifact-v1",
        "segment_id": str(payload.get("segment_id") or "segment_001"),
        "path": str(context_path),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
        "record_count": len(context_records),
    }


def _host_report(**overrides: object) -> dict:
    report = {
        "schema_version": "skyseed-additive-resource-prepare-v1",
        "maintenance_stage": "runtime/horizon_replenishment/additive_host_prepare",
        "ok": True,
        "additive_prepare_completed": True,
        "full_prepare_allowed": False,
        "full_host_scan_skipped": True,
        "future_host_link_state_apply_allowed": False,
        "active_generation_flip_allowed": False,
    }
    report.update(overrides)
    return report


def _attach_report(**overrides: object) -> dict:
    report = {
        "schema_version": "skyseed-bpf-owned-preactivation-attach-capability-v1",
        "maintenance_stage": (
            "runtime/horizon_replenishment/bpf_attach_capability_prepare"
        ),
        "ok": True,
        "delta_attach_count": 3,
        "full_attach_count": 0,
        "runtime_full_generation_reload_count": 0,
        "runtime_apply_skipped": True,
        "active_generation_set": False,
        "map_ready": False,
        "future_map_write_allowed": False,
        "active_generation_flip_allowed": False,
    }
    report.update(overrides)
    return report


def _queued_record(**overrides: object) -> dict:
    record = {
        "schema_version": "skyseed-rolling-horizon-queued-bpf-generation-v1",
        "ok": True,
        "preload_queued": True,
        "bpf_map_write_manifest_only": True,
        "manifest_only_loader_required": True,
        "batch_map_write_required": True,
        "delta_attach_count": 0,
        "full_attach_count": 0,
        "runtime_full_generation_reload_count": 0,
        "active_generation_flip_skipped": True,
        "runtime_apply_skipped": True,
    }
    record.update(overrides)
    return record


def _inactive_record(**overrides: object) -> dict:
    record = dict(_queued_record())
    record.update(
        {
            "schema_version": "skyseed-queued-bpf-preloaded-generation-v1",
            "preload_queued": False,
            "inactive_generation_written": True,
            "manifest_only_map_write_used": True,
            "manifest_only_loader": True,
            "batch_map_write_used": True,
            "batch_map_write_update_count": 12,
            "actual_data_write_count": 12,
        }
    )
    record.update(overrides)
    return record


class HorizonMaintenanceContractTest(unittest.TestCase):
    def test_active_generation_zero_is_an_explicit_cleanup_dependency(self) -> None:
        self.assertTrue(_generation_id_matches_explicit_value(0, 0))
        self.assertFalse(_generation_id_matches_explicit_value(None, 0))
        self.assertFalse(_generation_id_matches_explicit_value("", 0))

    def setUp(self) -> None:
        self._default_contexts = patch(
            "horizon_maintenance_process_worker._realization_contexts",
            return_value=("ctx",),
        )
        self._default_binding_refs = patch(
            "horizon_maintenance_process_worker."
            "_bpf_owned_interface_binding_refs_for_contexts",
            return_value=[],
        )

        def freeze(**kwargs: object) -> dict:
            generation_id = int(kwargs.get("generation_id") or 0)
            return {
                "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
                "active_generation": {"value": generation_id},
                "ingress_classifier_map": [],
                "ut_gateway_policy_map": [],
                "gateway_selector_ingress_map": [],
                "core_lfib_map": [],
                "egress_handoff_map": [],
                "zone_edge_delivery_map": [],
                "generation_base_map": [],
                "attach_plan": {"sat1:if0": ["ingress"]},
                "attached_interfaces": ["sat1:if0"],
                "attached_interface_details": _readiness()[
                    "attached_interface_details"
                ],
                "frozen_interface_binding_cache": {
                    "ifindex_by_interface_ref": dict(
                        kwargs.get("interface_ifindex_cache") or {}
                    ),
                    "mac_by_interface_ref": dict(
                        kwargs.get("interface_mac_cache") or {}
                    ),
                    "complete": True,
                    "live_discovery_allowed": False,
                },
            }

        self._default_freeze = patch(
            "horizon_maintenance_process_worker."
            "_bpf_owned_freeze_generation_compiled_runtime_inputs",
            side_effect=freeze,
        )
        self._default_edge_route_prepare = patch(
            "horizon_maintenance_process_worker."
            "_prepare_bpf_owned_edge_route_baseline",
            return_value={
                "report": {
                    "schema_version": (
                        "skyseed-pre-runtime-bpf-owned-edge-route-baseline-v1"
                    ),
                    "ready": True,
                    "ok": True,
                    "prepared_route_identity_evidence": {
                        "schema_version": (
                            "skyseed-bpf-owned-prepared-route-identity-evidence-v1"
                        ),
                        "ut_default_route_count": 0,
                        "gateway_local_return_route_count": 0,
                        "ut_default_routes": [],
                        "gateway_local_return_routes": [],
                    },
                }
            },
        )
        for patcher in (
            self._default_contexts,
            self._default_binding_refs,
            self._default_freeze,
            self._default_edge_route_prepare,
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_cleanup_quiesce_terminates_worker_before_return(self) -> None:
        class FakeProcess:
            pid = 4242

            def __init__(self) -> None:
                self.running = True
                self.terminate_count = 0

            def poll(self):
                return None if self.running else 0

            def terminate(self):
                self.terminate_count += 1
                self.running = False

            def wait(self, timeout=None):
                self.running = False
                return 0

            def kill(self):
                raise AssertionError("kill should not be required")

        process = FakeProcess()
        report = _terminate_horizon_maintenance_process_workers_for_cleanup(
            [process]
        )

        self.assertEqual(process.terminate_count, 1)
        self.assertFalse(process.running)
        self.assertTrue(report["all_terminal"])
        self.assertTrue(report["quiesced_before_host_artifact_cleanup"])
        self.assertEqual(report["terminated_count"], 1)
        self.assertEqual(report["killed_count"], 0)

    def test_process_worker_consumes_base_generation_binding_cache(self) -> None:
        ifindex_cache, mac_cache = _base_generation_interface_binding_caches(
            _readiness()["compiled_runtime_inputs"]
        )
        self.assertEqual(ifindex_cache, {"sat1:if0": 101})
        self.assertEqual(mac_cache, {"sat1:if0": "02:00:00:00:00:01"})

    def test_process_worker_binding_cache_includes_non_attached_handoff_endpoint(
        self,
    ) -> None:
        compiled_inputs = dict(_readiness()["compiled_runtime_inputs"])
        binding = compiled_inputs["frozen_interface_binding_cache"]
        binding["mac_by_interface_ref"][
            "netns:ut2001:ua-u2001s0"
        ] = "02:00:00:00:00:02"

        ifindex_cache, mac_cache = _base_generation_interface_binding_caches(
            compiled_inputs
        )

        self.assertNotIn("netns:ut2001:ua-u2001s0", ifindex_cache)
        self.assertEqual(
            mac_cache["netns:ut2001:ua-u2001s0"],
            "02:00:00:00:00:02",
        )

    def test_process_worker_rejects_incomplete_base_generation_binding_cache(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "horizon_maintenance_base_generation_binding_cache_incomplete",
        ):
            _base_generation_interface_binding_caches({})

    def test_process_worker_final_binding_scope_rejects_missing_and_unexpected_refs(
        self,
    ) -> None:
        bindings = [
            {
                "interface_ref": "netns:sat1:if0",
                "ifindex": 101,
                "mac_address": "02:00:00:00:00:01",
            }
        ]
        with self.assertRaisesRegex(
            RuntimeError,
            r'"missing": \["netns:sat2:if0"\].*"unexpected": \[\]',
        ):
            _validate_final_interface_binding_scope(
                required_interface_refs=["netns:sat1:if0", "netns:sat2:if0"],
                bindings=bindings,
            )
        with self.assertRaisesRegex(
            RuntimeError,
            r'"missing": \[\].*"unexpected": \["netns:sat1:if1"\]',
        ):
            _validate_final_interface_binding_scope(
                required_interface_refs=["netns:sat1:if0"],
                bindings=[
                    *bindings,
                    {
                        "interface_ref": "netns:sat1:if1",
                        "ifindex": 102,
                        "mac_address": "02:00:00:00:00:02",
                    },
                ],
            )

    def test_process_worker_script_bootstraps_package_imports(self) -> None:
        satellite_dir = Path(__file__).resolve().parent
        env = dict(os.environ)
        env["PYTHONPATH"] = str(satellite_dir)

        completed = subprocess.run(
            [
                sys.executable,
                str(satellite_dir / "horizon_maintenance_process_worker.py"),
                "--help",
            ],
            cwd="/tmp",
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=10.0,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--input", completed.stdout)

    def test_process_worker_frozen_inputs_keep_resident_attach_budget(self) -> None:
        budget = {
            "schema_version": "skyseed-bpf-resident-attach-budget-v1",
            "budget_source": "official_precompose_bpf_role_union_gate+prepared_horizon_offline_role_union",
            "max_attach_interfaces": 773,
            "max_resident_attach_interfaces": 773,
        }
        compiled = {
                "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
                "active_generation": {"value": 32},
                "attach_plan": {
                    "netns:gw201:ga-g201s0": ["egress"],
                    "netns:stl-940001:ua-u2007s0": ["ingress"],
                },
                "attached_interfaces": [
                    "netns:gw201:ga-g201s0",
                    "netns:stl-940001:ua-u2007s0",
                ],
                "attached_interface_details": [
                    {
                        "interface_ref": "netns:gw201:ga-g201s0",
                        "expected_roles": ["z_to_g_local_origin_classifier"],
                    },
                    {
                        "interface_ref": "netns:stl-940001:ua-u2007s0",
                        "expected_roles": ["g_to_z_classifier"],
                    },
                ],
                "ingress_classifier_map": [],
                "ut_gateway_policy_map": [],
                "gateway_selector_ingress_map": [],
                "core_lfib_map": [],
                "egress_handoff_map": [],
                "zone_edge_delivery_map": [],
                "generation_base_map": [],
        }
        with patch(
            "horizon_maintenance_process_worker._realization_contexts",
            return_value=("ctx",),
        ), patch(
            "horizon_maintenance_process_worker."
            "_bpf_owned_freeze_generation_compiled_runtime_inputs",
            return_value=compiled,
        ), patch(
            "horizon_maintenance_process_worker."
            "_bpf_owned_frozen_compiled_runtime_inputs_check",
            return_value={"ready": True},
        ):
            frozen = _freeze_preload_work_item(
                item={"context": _worker_context(), "physical_generation_id": 32},
                resident_attach_budget=budget,
                topology_path=Path(__file__),
                interface_ifindex_cache={"netns:sat1:if0": 101},
                interface_mac_cache={"netns:sat1:if0": "02:00:00:00:00:01"},
            )
        merged = _union_frozen_inputs([frozen])

        frozen_inputs = frozen["frozen_compiled_runtime_inputs"]
        self.assertEqual(
            frozen_inputs["resident_attach_coverage_budget"]["max_attach_interfaces"],
            773,
        )
        self.assertEqual(merged["bpf_attach_budget"]["max_attach_interfaces"], 773)
        self.assertEqual(len(merged["attach_plan"]), 2)

    def test_process_worker_rejects_precompiled_target_generation(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "process_worker_target_generation_precompile_forbidden",
        ):
            _freeze_preload_work_item(
                item={"frozen_compiled_runtime_inputs": _compiled_inputs()},
                topology_path=Path(__file__),
                interface_ifindex_cache={},
                interface_mac_cache={},
            )

    def test_process_worker_can_freeze_from_context_when_explicitly_allowed(
        self,
    ) -> None:
        budget = {
            "budget_source": "unit-test-dynamic-budget",
            "max_attach_interfaces": 773,
        }
        compiled = {
            "active_generation": {"value": 8},
            "ingress_classifier_map": [],
                "ut_gateway_policy_map": [],
                "gateway_selector_ingress_map": [],
            "core_lfib_map": [],
            "egress_handoff_map": [],
                "zone_edge_delivery_map": [],
            "generation_base_map": [],
            "attach_plan": {"netns:stl-future:ua-u2001s0": ["ingress"]},
            "attached_interfaces": ["netns:stl-future:ua-u2001s0"],
            "attached_interface_details": [
                {
                    "interface_ref": "netns:stl-future:ua-u2001s0",
                    "expected_roles": ["g_to_z_classifier"],
                }
            ],
        }

        with patch(
            "horizon_maintenance_process_worker._realization_contexts",
            return_value=("ctx",),
        ) as contexts_mock, patch(
            "horizon_maintenance_process_worker."
            "_bpf_owned_freeze_generation_compiled_runtime_inputs",
            return_value=compiled,
        ) as compile_mock, patch(
            "horizon_maintenance_process_worker."
            "_bpf_owned_frozen_compiled_runtime_inputs_check",
            return_value={"ready": True},
        ):
            frozen = _freeze_preload_work_item(
                item={
                    "physical_generation_id": 8,
                    "base_generation_id": 7,
                    "context": _worker_context(),
                },
                base_generation_compiled_inputs=_compiled_inputs(),
                resident_attach_budget=budget,
                topology_path=Path(__file__),
                interface_ifindex_cache={"netns:stl-future:ua-u2001s0": 101},
                interface_mac_cache={
                    "netns:stl-future:ua-u2001s0": "02:00:00:00:00:01"
                },
            )

        self.assertTrue(frozen["frozen_compiled_runtime_inputs_check"]["ready"])
        self.assertEqual(
            compile_mock.call_args.kwargs["interface_ifindex_cache"],
            {"netns:stl-future:ua-u2001s0": 101},
        )
        self.assertEqual(
            frozen["frozen_compiled_runtime_inputs"]["active_generation"]["value"],
            8,
        )
        self.assertEqual(
            frozen["frozen_compiled_runtime_inputs"][
                "resident_attach_coverage_budget"
            ]["max_attach_interfaces"],
            773,
        )
        contexts_mock.assert_called_once()
        self.assertEqual(
            contexts_mock.call_args.kwargs["topology_path"],
            Path(__file__),
        )
        compile_mock.assert_called_once()

    def test_frozen_attach_prepare_consumes_dynamic_resident_budget(self) -> None:
        budget = {
            "schema_version": "skyseed-bpf-resident-attach-budget-v1",
            "budget_source": "unit-test-dynamic-budget",
            "max_attach_interfaces": 773,
            "max_resident_attach_interfaces": 773,
        }
        frozen_inputs = {
            "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
            "active_generation": {"value": 32},
            "attach_plan": {
                "netns:gw201:ga-g201s0": ["egress"],
                "netns:stl-940001:ua-u2007s0": ["ingress"],
            },
            "attached_interfaces": [
                "netns:gw201:ga-g201s0",
                "netns:stl-940001:ua-u2007s0",
            ],
            "attached_interface_details": [
                {
                    "interface_ref": "netns:gw201:ga-g201s0",
                    "ifindex": 201,
                    "mac_address": "02:00:00:00:00:01",
                    "attach_directions": ["egress"],
                    "expected_roles": ["z_to_g_local_origin_classifier"],
                    "control_plane_ipv4_addrs": ["10.0.0.1"],
                },
                {
                    "interface_ref": "netns:stl-940001:ua-u2007s0",
                    "ifindex": 202,
                    "mac_address": "02:00:00:00:00:02",
                    "attach_directions": ["ingress"],
                    "expected_roles": ["g_to_z_classifier"],
                    "control_plane_ipv4_addrs": ["10.0.0.2"],
                },
            ],
            "ingress_classifier_map": [],
                "ut_gateway_policy_map": [],
                "gateway_selector_ingress_map": [],
            "core_lfib_map": [],
            "egress_handoff_map": [],
                "zone_edge_delivery_map": [],
            "generation_base_map": [],
            "resident_attach_coverage_budget": budget,
            "bpf_attach_budget": budget,
        }
        readiness = {
            "attach_plan": dict(frozen_inputs["attach_plan"]),
            "attached_interfaces": list(frozen_inputs["attached_interfaces"]),
            "attached_interface_details": list(
                frozen_inputs["attached_interface_details"]
            ),
            "map_capacities": {
                "classifiers": 128,
                "lfib": 256,
                "handoffs": 128,
            },
        }
        report = _prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs(
            topology_path=Path("/tmp/topology.json"),
            frozen_compiled_runtime_inputs=frozen_inputs,
            bpf_owned_readiness=readiness,
            max_attach_workers=2,
            retain_inactive_generations=[0, 31, 32],
            prepared_generation_window_count=4,
        )

        self.assertEqual(
            report["resident_attach_coverage_budget"]["budget_source"],
            "unit-test-dynamic-budget",
        )
        self.assertEqual(report["attach_cardinality_budget"]["max_attach_interfaces"], 773)
        self.assertEqual(
            report["attach_cardinality_budget"]["budget_source"],
            "unit-test-dynamic-budget",
        )
        self.assertTrue(report["ok"])

    def test_process_worker_fails_closed_when_attach_prepare_uses_default_budget(
        self,
    ) -> None:
        guarded = _attach_report_uses_resident_budget(
            attach_report={
                "ok": False,
                "reason": "bpf_resident_attach_coverage_budget_exceeded",
                "prepare_steps": {
                    "preactivation_attach_prepare": {
                        "attach_cardinality_budget": {
                            "budget_source": "default",
                            "max_attach_interfaces": 256,
                            "attach_eligible_count": 302,
                            "budget_exceeded": True,
                        }
                    }
                },
            },
            resident_attach_budget={
                "budget_source": "unit-test-dynamic-budget",
                "max_attach_interfaces": 773,
            },
        )

        self.assertFalse(guarded["ok"])
        self.assertEqual(
            guarded["reason"],
            "process_worker_attach_prepare_resident_budget_not_consumed",
        )
        self.assertFalse(guarded["fallback_to_linux"])
        self.assertTrue(guarded["fail_closed"])

    def test_preactivation_delta_attach_executes_from_frozen_inputs_without_probes(
        self,
    ) -> None:
        interface_ref = "netns:sat1:if0"
        frozen_inputs = {
            "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
            "active_generation": {"value": 8},
            "ingress_classifier_map": [],
                "ut_gateway_policy_map": [],
                "gateway_selector_ingress_map": [],
            "core_lfib_map": [],
            "egress_handoff_map": [],
                "zone_edge_delivery_map": [],
            "generation_base_map": [],
            "map_conflicts": [],
            "compiled_mpls_map_counts": {
                "ingress_classifier_map": 0,
                "ut_gateway_policy_map": 0,
                "gateway_selector_ingress_map": 0,
                "core_lfib_map": 0,
                "egress_handoff_map": 0,
                "zone_edge_delivery_map": 0,
                "generation_base_map": 0,
            },
            "attach_plan": {interface_ref: ["ingress", "egress"]},
            "attached_interfaces": [interface_ref],
            "attached_interface_details": [
                {
                    "interface_ref": interface_ref,
                    "interface_name": interface_ref,
                    "ifindex": 101,
                    "mac_address": "02:00:00:00:00:01",
                    "attach_directions": ["ingress", "egress"],
                    "expected_roles": ["g_to_z_classifier"],
                    "control_plane_ipv4_addrs": ["10.0.0.1"],
                }
            ],
            "resident_attach_coverage_budget": {
                "budget_source": "unit-test",
                "max_attach_interfaces": 8,
            },
        }
        readiness = _readiness(interface_ref)
        readiness["map_capacities"] = {
            "classifiers": 128,
            "lfib": 256,
            "handoffs": 128,
        }
        attach_calls: list[dict] = []

        def fake_attach(interface_ref_arg, object_path, **kwargs):
            attach_calls.append(
                {
                    "interface_ref": interface_ref_arg,
                    "object_path": str(object_path),
                    **kwargs,
                }
            )
            return [{"returncode": 0, "stdout": "", "stderr": ""}]

        with patch(
            "satellite.bpf_mpls_live.compile_mpls_bpf",
            return_value={"returncode": 0, "stdout": "", "stderr": ""},
        ), patch(
            "satellite.bpf_mpls_live.attach_mpls",
            side_effect=fake_attach,
        ), patch(
            "satellite.bpf_mpls_live.verify_mpls_clsact",
            side_effect=AssertionError("maintenance must not probe reused clsact"),
        ), patch(
            "satellite.bpf_mpls_live.interface_ipv4_addresses",
            side_effect=AssertionError("maintenance must not discover interface IPs"),
        ), patch(
            "satellite.bpf_mpls_live.read_mpls_tc_filter_stats",
            side_effect=AssertionError("maintenance must not read filter stats"),
        ):
            report = _prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs(
                topology_path=Path("/tmp/topology.json"),
                frozen_compiled_runtime_inputs=frozen_inputs,
                bpf_owned_readiness=readiness,
                max_attach_workers=2,
                retain_inactive_generations=[7, 8],
                prepared_generation_window_count=3,
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["delta_attach_count"], 1)
        self.assertEqual(len(attach_calls), 1)
        self.assertEqual(
            attach_calls[0]["clsact_setup_mode"],
            "direct_existing_delta",
        )
        self.assertEqual(
            attach_calls[0]["control_plane_ipv4_addrs"],
            ["10.0.0.1"],
        )
        self.assertEqual(
            report["attach_capability_prepare_physical_check_source"],
            "frozen_parent_attach_readiness",
        )
        execution = report["prepare_steps"]["preactivation_attach_prepare"][
            "delta_attach_execution"
        ]
        self.assertEqual(execution["requested_worker_count"], 2)
        self.assertEqual(execution["worker_count"], 1)
        self.assertFalse(execution["bounded_parallel"])

    def test_delta_attach_executor_is_bounded_and_returns_input_order(self) -> None:
        active = 0
        peak_active = 0
        active_by_node: dict[str, int] = {}
        lock = threading.Lock()
        two_active = threading.Event()

        def fake_attach(interface_ref, object_path, **kwargs):
            nonlocal active, peak_active
            node_id = str(interface_ref).split(":", 2)[1]
            with lock:
                active += 1
                active_by_node[node_id] = active_by_node.get(node_id, 0) + 1
                if active_by_node[node_id] != 1:
                    raise AssertionError("same-node attach tasks overlapped")
                peak_active = max(peak_active, active)
                if active == 2:
                    two_active.set()
            try:
                if not two_active.wait(timeout=1.0):
                    raise AssertionError("delta attach did not reach bounded parallelism")
                return [
                    {
                        "returncode": 0,
                        "interface_ref": {"interface_ref": interface_ref},
                    }
                ]
            finally:
                with lock:
                    active -= 1
                    active_by_node[node_id] -= 1

        tasks = [
            (
                f"netns:sat{index % 2}:if{index}",
                ["ingress"],
                [],
                "direct_new_delta",
            )
            for index in range(4)
        ]
        results, execution = _execute_bpf_delta_attach_tasks(
            attach_tasks=tasks,
            object_path=Path("/tmp/frozen-delta.o"),
            attach_mpls=fake_attach,
            max_attach_workers=2,
        )

        self.assertEqual(peak_active, 2)
        self.assertEqual(execution["requested_worker_count"], 2)
        self.assertEqual(execution["worker_count"], 2)
        self.assertTrue(execution["bounded_parallel"])
        self.assertEqual(execution["parallel_unit"], "frozen_node")
        self.assertEqual(execution["node_group_count"], 2)
        self.assertEqual(
            [item["interface_ref"]["interface_ref"] for item in results],
            [task[0] for task in tasks],
        )

    def test_delta_attach_executor_rejects_empty_task_result(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "bpf_delta_attach_empty_results"):
            _execute_bpf_delta_attach_tasks(
                attach_tasks=[
                    ("netns:sat1:if0", ["ingress"], [], "direct_new_delta")
                ],
                object_path=Path("/tmp/frozen-delta.o"),
                attach_mpls=lambda *args, **kwargs: [],
                max_attach_workers=2,
            )

    def test_process_audit_blocks_compact_manifest_missing_attach_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            segment_dir = run_dir / "replenished_segments" / "segment_001"
            segment_dir.mkdir(parents=True)
            (segment_dir / "horizon_maintenance_process_work.json").write_text(
                json.dumps(
                    {
                        "schema_version": "skyseed-horizon-maintenance-process-work-v3",
            "frozen_container_pid_cache": _frozen_container_pid_cache(),
                        "horizon_id": "H1",
                        "process_input_compact_manifest": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (segment_dir / "horizon_maintenance_queued.json").write_text(
                json.dumps(
                    {
                        "horizon_id": "H1",
                        "worker_mode": "process",
                        "process_worker_input_compact_manifest": True,
                        "expected_preload_count": 25,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = audit_horizon_maintenance_process(run_dir)

        self.assertFalse(report["ok"])
        self.assertIn(
            "process_worker_compact_manifest_missing_resident_attach_budget",
            {blocker["reason"] for blocker in report["blockers"]},
        )

    def test_process_audit_blocks_attach_prepare_default_budget_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            segment_dir = run_dir / "replenished_segments" / "segment_001"
            segment_dir.mkdir(parents=True)
            (segment_dir / "horizon_maintenance_process_work.json").write_text(
                json.dumps(
                    {
                        "schema_version": "skyseed-horizon-maintenance-process-work-v3",
            "frozen_container_pid_cache": _frozen_container_pid_cache(),
                        "horizon_id": "H1",
                        "process_input_compact_manifest": True,
                        "resident_attach_coverage_budget": {
                            "budget_source": "unit-test-dynamic-budget",
                            "max_attach_interfaces": 773,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (segment_dir / "horizon_maintenance_queued.json").write_text(
                json.dumps(
                    {
                        "horizon_id": "H1",
                        "worker_mode": "process",
                        "process_worker_input_compact_manifest": True,
                        "expected_preload_count": 25,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (segment_dir / "horizon_maintenance_failure.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "bpf_attach_capability_report": {
                            "ok": False,
                            "reason": "bpf_resident_attach_coverage_budget_exceeded",
                            "prepare_steps": {
                                "preactivation_attach_prepare": {
                                    "attach_cardinality_budget": {
                                        "budget_source": "default",
                                        "max_attach_interfaces": 256,
                                        "attach_eligible_count": 302,
                                        "budget_exceeded": True,
                                    }
                                }
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = audit_horizon_maintenance_process(run_dir)

        self.assertFalse(report["ok"])
        self.assertIn(
            "process_worker_attach_prepare_resident_budget_not_consumed",
            {blocker["reason"] for blocker in report["blockers"]},
        )

    def test_missing_base_compiled_inputs_fail_closed_and_reject_summary(self) -> None:
        compact = {
            "ok": True,
            "generation_id": 7,
            "compiled_runtime_inputs": {
                "compiled_runtime_inputs_summary": {"present": True},
                "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
            },
        }

        base_check = base_compiled_inputs_ready(compact)

        self.assertFalse(base_check["ready"])
        self.assertTrue(base_check["compact_summary_rejected"])

        result = classify_horizon_maintenance(
            request=_request(),
            host_additive_prepare=_host_report(),
            attach_capability_prepare=_attach_report(),
            base_generation_record=compact,
            queued_preload_records=[_queued_record()],
            inactive_preload_records=[],
            retired_cleanup_report=retired_cleanup_dependency_report(
                previous_horizon_id=""
            ),
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn(
            "base_compiled_runtime_inputs",
            {blocker["stage"] for blocker in result["blockers"]},
        )

    def test_retired_cleanup_candidate_accepts_root_generation_manifest(self) -> None:
        root_generation_candidate = {
            "generation_id": 0,
            "compiled_runtime_inputs": _compiled_inputs(),
        }

        root_check = retired_cleanup_candidate_ready(root_generation_candidate)

        self.assertTrue(root_check["ready"])
        self.assertEqual(root_check["generation_id"], 0)

        missing_id_check = retired_cleanup_candidate_ready(
            {"compiled_runtime_inputs": _compiled_inputs()}
        )
        self.assertFalse(missing_id_check["ready"])
        self.assertEqual(
            missing_id_check["reason"], "retired_cleanup_generation_id_missing"
        )

        compact_check = retired_cleanup_candidate_ready(
            {
                "generation_id": 0,
                "compiled_runtime_inputs": {
                    "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v2",
                    "compiled_runtime_inputs_summary": {"present": True},
                },
            }
        )
        self.assertFalse(compact_check["ready"])
        self.assertTrue(compact_check["compact_summary_rejected"])

    def test_previous_horizon_cleanup_required_before_ready(self) -> None:
        result = classify_horizon_maintenance(
            request=_request(previous_horizon_id="H0"),
            host_additive_prepare=_host_report(),
            attach_capability_prepare=_attach_report(),
            base_generation_record=_base_record(),
            queued_preload_records=[_queued_record()],
            inactive_preload_records=[_inactive_record()],
            retired_cleanup_report=retired_cleanup_dependency_report(
                previous_horizon_id="H0",
                queued_count=1,
                completed_count=0,
                status="queued",
            ),
        )

        self.assertFalse(result["ready"])
        self.assertEqual(result["status"], "failed")
        self.assertIn(
            "retired_generation_cleanup",
            {blocker["stage"] for blocker in result["blockers"]},
        )

    def test_previous_horizon_cleanup_completed_allows_ready(self) -> None:
        result = classify_horizon_maintenance(
            request=_request(previous_horizon_id="H0"),
            host_additive_prepare=_host_report(),
            attach_capability_prepare=_attach_report(),
            base_generation_record=_base_record(),
            queued_preload_records=[_queued_record()],
            inactive_preload_records=[_inactive_record()],
            retired_cleanup_report=retired_cleanup_dependency_report(
                previous_horizon_id="H0",
                completed_count=1,
                status="completed",
            ),
        )

        self.assertTrue(result["ready"])
        self.assertEqual(result["status"], "ready")

    def test_successor_maintenance_waits_for_ready_consumption(self) -> None:
        self.assertTrue(
            successor_maintenance_gate(
                previous_horizon_id="H1",
                consumed_horizon_ids=["H1"],
            )["allowed"]
        )

        ready_not_consumed = successor_maintenance_gate(
            previous_horizon_id="H1",
            ready_horizon_ids=["H1"],
        )
        self.assertFalse(ready_not_consumed["allowed"])
        self.assertEqual(ready_not_consumed["status"], "deferred")
        self.assertEqual(
            ready_not_consumed["reason"], "previous_horizon_ready_not_consumed"
        )

        pending = successor_maintenance_gate(
            previous_horizon_id="H1",
            pending_horizon_ids=["H1"],
        )
        self.assertFalse(pending["allowed"])
        self.assertEqual(pending["reason"], "previous_horizon_maintenance_pending")

        failed = successor_maintenance_gate(
            previous_horizon_id="H1",
            failed_horizon_ids=["H1"],
        )
        self.assertFalse(failed["allowed"])
        self.assertEqual(failed["status"], "failed")

    def test_ready_path_publishes_complete_ready_artifact(self) -> None:
        request = _request()
        queued_record = _queued_record(compiled_runtime_inputs=_compiled_inputs())
        inactive_record = _inactive_record(
            compiled_runtime_inputs=_compiled_inputs(),
            preload_report={
                "ok": True,
                "compiled_runtime_inputs": _compiled_inputs(),
                "prepare_steps": {
                    "real_backend_prepare": {
                        "ok": True,
                        "batch_map_write_used": True,
                        "actual_data_write_count": 3,
                    }
                },
            },
        )
        result = classify_horizon_maintenance(
            request=request,
            host_additive_prepare=_host_report(wall_seconds=2.5),
            attach_capability_prepare=_attach_report(),
            base_generation_record=_base_record(),
            queued_preload_records=[queued_record],
            inactive_preload_records=[inactive_record],
            retired_cleanup_report=retired_cleanup_dependency_report(
                previous_horizon_id=""
            ),
            wall_seconds=1.25,
        )

        with tempfile.TemporaryDirectory() as tmp:
            segment_dir = Path(tmp)
            artifact = publish_horizon_maintenance_result(
                result=result,
                segment_dir=segment_dir,
            )
            loaded = load_ready_artifact(
                artifact,
                expected_request_hash=request.request_hash,
                expected_next_horizon_id="H1",
            )

        self.assertEqual(artifact.name, READY_ARTIFACT_NAME)
        self.assertEqual(loaded["status"], "ready")
        self.assertEqual(loaded["queued_preload_record_count"], 1)
        self.assertEqual(loaded["inactive_preload_record_count"], 1)
        self.assertTrue(loaded["forbidden_work_audit"]["passed"])
        self.assertIn("interface_binding_freeze_report", loaded)
        self.assertIn("artifact_hash", loaded)
        self.assertNotIn("compiled_runtime_inputs", loaded["queued_preload_records"][0])
        self.assertIn(
            "compiled_runtime_inputs_summary",
            loaded["queued_preload_records"][0],
        )
        self.assertNotIn("preload_report", loaded["inactive_preload_records"][0])
        self.assertIn(
            "preload_report_summary",
            loaded["inactive_preload_records"][0],
        )
        self.assertEqual(loaded["wall_seconds"], 1.25)
        self.assertEqual(loaded["worker_wall_seconds"], 1.25)
        self.assertIn("wall_seconds_scope", loaded)
        self.assertEqual(
            loaded["subtask_wall_seconds"]["host_additive_prepare_wall_seconds"],
            2.5,
        )
        self.assertTrue(
            loaded["inactive_preload_records"][0]["manifest_only_map_write_used"]
        )
        self.assertTrue(
            loaded["inactive_preload_records"][0]["bpf_map_write_manifest_only"]
        )

    def test_partial_stage_reports_do_not_mark_ready(self) -> None:
        cases = [
            (_host_report(ok=False, reason="host_partial"), _attach_report(), [_queued_record()]),
            (_host_report(), _attach_report(ok=False, reason="attach_partial"), [_queued_record()]),
            (_host_report(), _attach_report(), [], []),
            (_host_report(), _attach_report(), [_queued_record()], []),
            (_host_report(), _attach_report(), [_queued_record(ok=False)], []),
        ]
        for case in cases:
            host, attach, queued, inactive = (
                case if len(case) == 4 else (*case, [_inactive_record()])
            )
            with self.subTest(host=host.get("reason"), attach=attach.get("reason")):
                result = classify_horizon_maintenance(
                    request=_request(),
                    host_additive_prepare=host,
                    attach_capability_prepare=attach,
                    base_generation_record=_base_record(),
                    queued_preload_records=queued,
                    inactive_preload_records=inactive,
                    retired_cleanup_report=retired_cleanup_dependency_report(
                        previous_horizon_id=""
                    ),
                )
                self.assertIn(result["status"], {"failed", "blocked"})
                self.assertGreater(result["blocker_count"], 0)

    def test_forbidden_work_counters_fail(self) -> None:
        result = classify_horizon_maintenance(
            request=_request(),
            host_additive_prepare=_host_report(future_host_link_activation_count=1),
            attach_capability_prepare=_attach_report(full_attach_count=1),
            base_generation_record=_base_record(),
            queued_preload_records=[
                _queued_record(runtime_full_generation_reload_count=1)
            ],
            inactive_preload_records=[_inactive_record()],
            retired_cleanup_report=retired_cleanup_dependency_report(
                previous_horizon_id="H0"
            ),
        )

        self.assertEqual(result["status"], "failed")
        audit = result["forbidden_work_audit"]
        self.assertFalse(audit["passed"])
        self.assertGreaterEqual(audit["violation_count"], 3)

    def test_staging_artifact_not_consumed_and_hash_mismatch_fails_closed(self) -> None:
        request = _request()
        result = classify_horizon_maintenance(
            request=request,
            host_additive_prepare=_host_report(),
            attach_capability_prepare=_attach_report(),
            base_generation_record=_base_record(),
            queued_preload_records=[_queued_record()],
            inactive_preload_records=[_inactive_record()],
            retired_cleanup_report=retired_cleanup_dependency_report(
                previous_horizon_id=""
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            segment_dir = Path(tmp)
            staging = segment_dir / ".horizon_maintenance_staging" / READY_ARTIFACT_NAME
            staging.parent.mkdir(parents=True)
            staging.write_text(json.dumps(result), encoding="utf-8")

            with self.assertRaises(RuntimeError):
                load_ready_artifact(staging, expected_request_hash=request.request_hash)

            artifact = publish_horizon_maintenance_result(
                result=result,
                segment_dir=segment_dir,
            )
            with self.assertRaises(RuntimeError):
                load_ready_artifact(
                    artifact,
                    expected_request_hash="not-" + request.request_hash,
                )
            tampered = json.loads(artifact.read_text(encoding="utf-8"))
            tampered["artifact_hash"] = "bad-hash"
            artifact.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_ready_artifact(
                    artifact,
                    expected_request_hash=request.request_hash,
                )

    def test_lock_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "maintenance.lock"
            with MaintenanceLock(lock_path):
                with self.assertRaises(RuntimeError):
                    with MaintenanceLock(lock_path):
                        pass
            with MaintenanceLock(lock_path):
                self.assertTrue(lock_path.exists())

    def test_process_worker_publishes_ready_from_frozen_inputs(self) -> None:
        request = _request()
        queued = _queued_record(
            segment_epoch_key="segment_001:slice_001",
            horizon_id="H1",
            generation_id=8,
            physical_generation_id=8,
            physical_horizon_generation_key="H1:8",
        )
        frozen_inputs = {
            **_compiled_inputs(),
            "active_generation": {"value": 8},
            "attached_interfaces": ["sat1:if0"],
            "attached_interface_details": [
                {
                    "interface_ref": "sat1:if0",
                    "expected_roles": ["g_to_z_classifier"],
                    "attach_directions": ["ingress"],
                }
            ],
            "attach_plan": {"sat1:if0": ["ingress"]},
        }
        payload = {
            "schema_version": "skyseed-horizon-maintenance-process-work-v3",
            "frozen_container_pid_cache": _frozen_container_pid_cache(),
            "segment_dir": "",
            "topology_path": "topology.json",
            "link_state_path": "link_state.json",
            "segment_id": "segment_001",
            "horizon_id": "H1",
            "max_node_workers": 2,
            "request": request.to_payload(),
            "retain_generation_ids": [7, 8],
            "prepared_generation_window_count": 2,
            "delta_prepare_scope_by_slot": {},
            "resource_coverage_plan": {},
            "attach_frozen_compiled_runtime_inputs": frozen_inputs,
            "bpf_owned_readiness": _readiness(),
            "base_generation_record": _base_record(),
            "preload_work_items": [
                {
                    "queued_record": queued,
                    "context": _worker_context(),
                    "physical_generation_id": 8,
                    "base_generation_compiled_inputs": _compiled_inputs(),
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            payload["segment_dir"] = tmp
            payload["topology_path"], payload["link_state_path"] = (
                _write_worker_input_files(Path(tmp))
            )
            with patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs",
                return_value=_attach_report(
                    ok=True,
                    attached_interfaces=["sat1:if0"],
                    attached_interface_details=[
                        {
                            "interface_ref": "sat1:if0",
                            "expected_roles": ["g_to_z_classifier"],
                            "attach_directions": ["ingress"],
                        }
                    ],
                    attach_plan={"sat1:if0": ["ingress"]},
                    prepared_bpf_attach_capability_count=1,
                    prepare_steps={
                        "real_backend_prepare": {
                            "compiled_runtime_inputs": frozen_inputs,
                        }
                    },
                    packet_route_entries=[{"route": "full-handoff-only"}],
                ),
            ) as attach_mock, patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_preload_from_frozen_inputs",
                return_value=_inactive_record(
                    ok=True,
                    inactive_generation_written=True,
                    compiled_runtime_inputs=frozen_inputs,
                    preactivation_preload={
                        "manifest_only_loader": True,
                        "batch_map_write_used": True,
                        "actual_data_write_count": 4,
                    },
                ),
            ) as preload_mock:
                _externalize_worker_contexts(payload, Path(payload["segment_dir"]))
                summary = run_horizon_maintenance_process_payload(payload)
                loaded = load_ready_artifact(
                    Path(summary["artifact_path"]),
                    expected_request_hash=request.request_hash,
                    expected_next_horizon_id="H1",
                )
                attach_handoff = json.loads(
                    Path(loaded["handoff_attach_capability_artifact"]).read_text(
                        encoding="utf-8"
                    )
                )

        self.assertEqual(summary["status"], "ready")
        self.assertTrue(loaded["ready"])
        expected_stage_timing_keys = {
            "prepared_state_restore",
            "host_additive_prepare",
            "binding_requirement_derivation",
            "final_binding_prepare",
            "edge_route_prepare",
            "strict_generation_compile_from_frozen_context",
            "bpf_attach_capability_prepare",
            "inactive_generation_preload",
            "retired_generation_cleanup",
            "summed_subtask_wall_seconds",
            "unattributed_before_publish",
        }
        self.assertEqual(
            set(loaded["subtask_wall_seconds"]),
            expected_stage_timing_keys,
        )
        self.assertTrue(
            all(
                isinstance(value, float) and value >= 0.0
                for value in loaded["subtask_wall_seconds"].values()
            )
        )
        self.assertEqual(summary["inactive_preload_record_count"], 1)
        attach_mock.assert_called_once()
        preload_mock.assert_called_once()
        self.assertTrue(summary["attach_capability_prepare_ok"])
        self.assertTrue(summary["host_additive_prepare_ok"])
        self.assertTrue(summary["edge_route_baseline_prepare_ok"])
        self.assertTrue(
            loaded["edge_route_baseline_prepare_report"]["process_owned"]
        )
        self.assertEqual(
            loaded["edge_route_baseline_prepare_report"]["maintenance_stage"],
            "runtime/horizon_replenishment/edge_route_additive_prepare",
        )
        self.assertFalse(loaded["bpf_attach_capability_report"]["active_generation_flip_allowed"])
        self.assertNotIn(
            "prepare_steps",
            loaded["bpf_attach_capability_report"],
        )
        self.assertTrue(
            loaded["bpf_attach_capability_report"]["prepare_steps_externalized"]
        )
        self.assertNotIn(
            "packet_route_entries",
            loaded["bpf_attach_capability_report"],
        )
        self.assertEqual(
            attach_handoff["schema_version"],
            "skyseed-horizon-maintenance-handoff-attach-capability-v1",
        )
        self.assertIn("prepare_steps", attach_handoff["report"])
        self.assertEqual(
            attach_handoff["report"]["packet_route_entries"],
            [{"route": "full-handoff-only"}],
        )
        self.assertFalse(
            loaded["host_additive_prepare_report"][
                "future_host_link_state_apply_allowed"
            ]
        )

    def test_process_worker_edge_route_prepare_failure_cannot_publish_ready(
        self,
    ) -> None:
        request = _request()
        frozen_inputs = {**_compiled_inputs(), "active_generation": {"value": 8}}
        payload = {
            "schema_version": "skyseed-horizon-maintenance-process-work-v3",
            "frozen_container_pid_cache": _frozen_container_pid_cache(),
            "segment_dir": "",
            "topology_path": "topology.json",
            "link_state_path": "link_state.json",
            "segment_id": "segment_001",
            "horizon_id": "H1",
            "max_node_workers": 2,
            "request": request.to_payload(),
            "retain_generation_ids": [7, 8],
            "prepared_generation_window_count": 2,
            "delta_prepare_scope_by_slot": {},
            "resource_coverage_plan": {},
            "attach_frozen_compiled_runtime_inputs": frozen_inputs,
            "bpf_owned_readiness": _readiness(),
            "base_generation_record": _base_record(),
            "preload_work_items": [
                {
                    "queued_record": _queued_record(horizon_id="H1"),
                    "context": _worker_context(),
                    "physical_generation_id": 8,
                    "base_generation_compiled_inputs": _compiled_inputs(),
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            segment_dir = Path(tmp)
            payload["segment_dir"] = tmp
            payload["topology_path"], payload["link_state_path"] = (
                _write_worker_input_files(segment_dir)
            )
            with patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_edge_route_baseline",
                return_value={
                    "report": {
                        "ready": False,
                        "ok": False,
                        "reason": "route_write_failed",
                    }
                },
            ), patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs"
            ) as attach_mock, patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_preload_from_frozen_inputs"
            ) as preload_mock:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "horizon_maintenance_edge_route_baseline_prepare_failed:"
                    "route_write_failed",
                ):
                    _externalize_worker_contexts(payload, Path(payload["segment_dir"]))
                    run_horizon_maintenance_process_payload(payload)

            attach_mock.assert_not_called()
            preload_mock.assert_not_called()
            self.assertFalse(
                (segment_dir / "horizon_maintenance_ready.json").exists()
            )

    def test_process_worker_freezes_from_context_after_additive_prepare(self) -> None:
        request = _request()
        queued = _queued_record(
            segment_epoch_key="segment_001:slice_001",
            horizon_id="H1",
            generation_id=8,
            physical_generation_id=8,
            physical_horizon_generation_key="H1:8",
        )
        compiled = {
            "active_generation": {"value": 8},
            "ingress_classifier_map": [],
                "ut_gateway_policy_map": [],
                "gateway_selector_ingress_map": [],
            "core_lfib_map": [],
            "egress_handoff_map": [],
                "zone_edge_delivery_map": [],
            "generation_base_map": [],
            "attached_interfaces": ["netns:stl-future:ua-u2001s0"],
            "attached_interface_details": [
                {
                    "interface_ref": "netns:stl-future:ua-u2001s0",
                    "expected_roles": ["g_to_z_classifier"],
                    "attach_directions": ["ingress"],
                }
            ],
            "attach_plan": {"netns:stl-future:ua-u2001s0": ["ingress"]},
        }
        payload = {
            "schema_version": "skyseed-horizon-maintenance-process-work-v3",
            "frozen_container_pid_cache": _frozen_container_pid_cache(),
            "segment_dir": "",
            "topology_path": "topology.json",
            "link_state_path": "link_state.json",
            "segment_id": "segment_001",
            "horizon_id": "H1",
            "max_node_workers": 2,
            "request": request.to_payload(),
            "retain_generation_ids": [7, 8],
            "prepared_generation_window_count": 2,
            "delta_prepare_scope_by_slot": {"slot-a": ["resource-a"]},
            "resource_coverage_plan": {},
            "base_generation_record": _base_record(),
            "resident_attach_coverage_budget": {
                "budget_source": "unit-test-dynamic-budget",
                "max_attach_interfaces": 773,
            },
            "bpf_owned_readiness": _readiness(
                "netns:stl-existing:if0"
            ),
            "preload_work_items": [
                {
                    "queued_record": queued,
                    "generation_id": 8,
                    "physical_generation_id": 8,
                    "process_worker_compile_from_context_allowed": True,
                    "context": {
                        "snapshot": {
                            "schema_version": "skyseed-topology-snapshot-v1",
                            "snapshot_id": "slice-001",
                            "time_slice": {"slice_id": "slice-001"},
                        },
                        "runtime_update_plan": {
                            "schema_version": "skyseed-runtime-update-plan-v1",
                            "plan_id": "plan-001",
                            "target_snapshot": {"snapshot_id": "slice-001"},
                            "planner": {},
                            "apply_model": {},
                            "node_subplans": {},
                            "replacement_semantics": {},
                            "validation_expectations": {},
                        },
                    },
                }
            ],
        }
        order = []

        class FakeExecutor:
            def restore_prepared_runtime_state(self, **_: object) -> dict:
                order.append("restore")
                return {"ok": True}

            def prepare_additive_resources(self, **_: object) -> dict:
                order.append("prepare")
                return {
                    "ok": True,
                    "additive_prepare_completed": True,
                    "prepared_resource_scope_by_slot": {"slot-a": ["resource-a"]},
                    "created_container_interface_bindings": [
                        {
                            "interface_ref": "netns:stl-future:ua-u2001s0",
                            "ifindex": 202,
                            "mac_address": "02:00:00:00:00:02",
                            "binding_source": "created_veth_operation_result",
                        }
                    ],
                }

            def capture_existing_container_interface_bindings(
                self, *, interface_refs: object
            ) -> dict:
                order.append("capture")
                if list(interface_refs) != ["netns:stl-future:ua-u2001s0"]:
                    raise AssertionError(interface_refs)
                return {
                    "ok": True,
                    "full_scan": False,
                    "bindings": [
                        {
                            "interface_ref": "netns:stl-future:ua-u2001s0",
                            "ifindex": 303,
                            "mac_address": "02:00:00:00:00:03",
                            "binding_source": (
                                "targeted_existing_interface_prepare_operation_result"
                            ),
                        }
                    ],
                }

        def compile_after_prepare(**kwargs: object) -> dict:
            order.append("compile")
            self.assertIn("prepare", order)
            self.assertIn("capture", order)
            self.assertEqual(
                kwargs["interface_ifindex_cache"],
                {
                    "sat1:if0": 101,
                    "netns:stl-future:ua-u2001s0": 303,
                },
            )
            self.assertEqual(
                kwargs["interface_mac_cache"]["netns:stl-future:ua-u2001s0"],
                "02:00:00:00:00:03",
            )
            return compiled

        with tempfile.TemporaryDirectory() as tmp:
            payload["segment_dir"] = tmp
            payload["topology_path"], payload["link_state_path"] = (
                _write_worker_input_files(Path(tmp))
            )
            with patch(
                "horizon_maintenance_process_worker.MinimalRealNetworkMutationExecutor",
                return_value=FakeExecutor(),
            ), patch(
                "horizon_maintenance_process_worker._realization_contexts",
                return_value=("ctx",),
            ) as contexts_mock, patch(
                "horizon_maintenance_process_worker."
                "_bpf_owned_interface_binding_refs_for_contexts",
                side_effect=lambda contexts: (
                    order.append("requirements")
                    or ["netns:stl-future:ua-u2001s0"]
                ),
            ), patch(
                "horizon_maintenance_process_worker."
                "_bpf_owned_freeze_generation_compiled_runtime_inputs",
                side_effect=compile_after_prepare,
            ), patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs",
                return_value=_attach_report(
                    ok=True,
                    attached_interfaces=["netns:stl-future:ua-u2001s0"],
                    attached_interface_details=[
                        {
                            "interface_ref": "netns:stl-future:ua-u2001s0",
                            "expected_roles": ["g_to_z_classifier"],
                            "attach_directions": ["ingress"],
                        }
                    ],
                    attach_plan={"netns:stl-future:ua-u2001s0": ["ingress"]},
                    prepared_bpf_attach_capability_count=1,
                    attach_cardinality_budget={
                        "budget_source": "unit-test-dynamic-budget",
                        "max_attach_interfaces": 773,
                    },
                ),
            ), patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_preload_from_frozen_inputs",
                return_value=_inactive_record(
                    ok=True,
                    inactive_generation_written=True,
                    compiled_runtime_inputs=compiled,
                    preactivation_preload={
                        "manifest_only_loader": True,
                        "batch_map_write_used": True,
                        "actual_data_write_count": 4,
                    },
                ),
            ):
                _externalize_worker_contexts(payload, Path(payload["segment_dir"]))
                summary = run_horizon_maintenance_process_payload(payload)
                loaded = load_ready_artifact(
                    Path(summary["artifact_path"]),
                    expected_request_hash=request.request_hash,
                    expected_next_horizon_id="H1",
                )

        self.assertEqual(
            order[:5],
            ["restore", "prepare", "requirements", "capture", "compile"],
        )
        self.assertEqual(summary["status"], "ready")
        self.assertTrue(loaded["ready"])
        self.assertEqual(summary["inactive_preload_record_count"], 1)
        self.assertTrue(summary["host_additive_prepare_ok"])
        self.assertTrue(summary["attach_capability_prepare_ok"])
        final_binding_report = dict(
            dict(loaded["host_additive_prepare_report"]).get(
                "final_interface_binding_prepare"
            )
            or {}
        )
        self.assertEqual(final_binding_report["binding_count"], 1)
        self.assertEqual(final_binding_report["superseded_ifindex_count"], 1)
        self.assertEqual(final_binding_report["superseded_mac_count"], 1)
        self.assertNotIn("bindings", final_binding_report)
        self.assertEqual(
            contexts_mock.call_args.kwargs["topology_path"],
            Path(payload["topology_path"]),
        )

    def test_process_worker_25_generations_share_frozen_binding_cache(self) -> None:
        request = _request(expected_preload_count=25)
        interface_ref = "sat1:if0"
        items = []
        for generation_id in range(32, 57):
            items.append(
                {
                    "queued_record": _queued_record(
                        segment_epoch_key=f"H1:slice-{generation_id}",
                        horizon_id="H1",
                        generation_id=generation_id,
                        physical_generation_id=generation_id,
                    ),
                    "physical_generation_id": generation_id,
                    "process_worker_compile_from_context_allowed": True,
                    "context": {
                        "snapshot": {
                            "schema_version": "skyseed-topology-snapshot-v1",
                            "snapshot_id": f"slice-{generation_id}",
                            "time_slice": {"slice_id": f"slice-{generation_id}"},
                        },
                        "runtime_update_plan": {
                            "schema_version": "skyseed-runtime-update-plan-v1",
                            "plan_id": f"plan-{generation_id}",
                            "target_snapshot": {"snapshot_id": f"slice-{generation_id}"},
                            "planner": {},
                            "apply_model": {},
                            "node_subplans": {},
                            "replacement_semantics": {},
                            "validation_expectations": {},
                        },
                    },
                }
            )
        payload = {
            "schema_version": "skyseed-horizon-maintenance-process-work-v3",
            "frozen_container_pid_cache": _frozen_container_pid_cache(),
            "segment_dir": "",
            "topology_path": "topology.json",
            "link_state_path": "link_state.json",
            "segment_id": "segment_001",
            "horizon_id": "H1",
            "request": request.to_payload(),
            "retain_generation_ids": list(range(32, 57)),
            "prepared_generation_window_count": 96,
            "max_node_workers": 2,
            "delta_prepare_scope_by_slot": {},
            "resource_coverage_plan": {},
            "resident_attach_coverage_budget": {
                "budget_source": "unit-test",
                "max_attach_interfaces": 1,
            },
            "bpf_owned_readiness": _readiness(interface_ref),
            "base_generation_record": _base_record(),
            "preload_work_items": items,
        }
        ifindex_cache_ids = []
        mac_cache_ids = []

        def compile_from_frozen(**kwargs: object) -> dict:
            ifindex_cache_ids.append(id(kwargs["interface_ifindex_cache"]))
            mac_cache_ids.append(id(kwargs["interface_mac_cache"]))
            generation_id = int(kwargs["generation_id"])
            return {
                **_compiled_inputs(),
                "active_generation": {"value": generation_id},
                "ingress_classifier_map": [],
                "ut_gateway_policy_map": [],
                "gateway_selector_ingress_map": [],
                "core_lfib_map": [],
                "egress_handoff_map": [],
                "zone_edge_delivery_map": [],
                "generation_base_map": [],
                "attach_plan": {interface_ref: ["ingress"]},
                "attached_interfaces": [interface_ref],
                "attached_interface_details": _readiness(interface_ref)[
                    "attached_interface_details"
                ],
            }

        class FakeExecutor:
            restore_calls: list[dict] = []

            def restore_prepared_runtime_state(self, **kwargs: object) -> dict:
                self.restore_calls.append(dict(kwargs))
                return {
                    "ok": True,
                    "restored_for_process_worker": True,
                    "future_host_link_state_apply_allowed": False,
                }

            def capture_existing_container_interface_bindings(
                self, *, interface_refs: object
            ) -> dict:
                if list(interface_refs) != [interface_ref]:
                    raise AssertionError(interface_refs)
                return {
                    "ok": True,
                    "full_scan": False,
                    "bindings": [
                        {
                            "interface_ref": interface_ref,
                            "ifindex": 101,
                            "mac_address": "02:00:00:00:00:01",
                            "binding_source": (
                                "targeted_existing_interface_prepare_operation_result"
                            ),
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as tmp:
            payload["segment_dir"] = tmp
            payload["topology_path"], payload["link_state_path"] = (
                _write_worker_input_files(Path(tmp))
            )
            with patch(
                "horizon_maintenance_process_worker.MinimalRealNetworkMutationExecutor",
                return_value=FakeExecutor(),
            ), patch(
                "horizon_maintenance_process_worker._realization_contexts",
                return_value=("ctx",),
            ), patch(
                "horizon_maintenance_process_worker."
                "_bpf_owned_interface_binding_refs_for_contexts",
                return_value=[interface_ref],
            ), patch(
                "horizon_maintenance_process_worker."
                "_bpf_owned_freeze_generation_compiled_runtime_inputs",
                side_effect=compile_from_frozen,
            ), patch(
                "horizon_maintenance_process_worker._prepare_bpf_owned_preactivation_preload_from_frozen_inputs",
                side_effect=lambda **kwargs: _inactive_record(
                    ok=True,
                    inactive_generation_written=True,
                    compiled_runtime_inputs=dict(
                        kwargs["frozen_compiled_runtime_inputs"]
                    ),
                    preactivation_preload={
                        "manifest_only_loader": True,
                        "batch_map_write_used": True,
                        "actual_data_write_count": 1,
                    },
                ),
            ):
                _externalize_worker_contexts(payload, Path(payload["segment_dir"]))
                summary = run_horizon_maintenance_process_payload(payload)
                loaded = load_ready_artifact(
                    Path(summary["artifact_path"]),
                    expected_request_hash=request.request_hash,
                    expected_next_horizon_id="H1",
                )

        self.assertTrue(summary["ready"])
        self.assertEqual(summary["inactive_preload_record_count"], 25)
        self.assertEqual(len(set(ifindex_cache_ids)), 1)
        self.assertEqual(len(set(mac_cache_ids)), 1)
        self.assertEqual(len(FakeExecutor.restore_calls), 1)
        self.assertEqual(
            FakeExecutor.restore_calls[0]["frozen_container_pid_cache"],
            _frozen_container_pid_cache(),
        )
        no_delta_host_report = dict(loaded["host_additive_prepare_report"])
        self.assertEqual(no_delta_host_report["topology_path"], payload["topology_path"])
        self.assertEqual(no_delta_host_report["link_state_path"], payload["link_state_path"])
        self.assertTrue(no_delta_host_report["prepared_resource_counts"])

    def test_process_worker_rejects_missing_payload_physical_paths(self) -> None:
        payload = {
            "schema_version": "skyseed-horizon-maintenance-process-work-v3",
            "frozen_container_pid_cache": _frozen_container_pid_cache(),
            "segment_dir": "",
            "segment_id": "segment_001",
            "horizon_id": "H1",
            "max_node_workers": 2,
            "request": _request().to_payload(),
            "retain_generation_ids": [7, 8],
            "prepared_generation_window_count": 2,
            "delta_prepare_scope_by_slot": {},
            "resource_coverage_plan": {},
            "base_generation_record": _base_record(),
            "preload_work_items": [
                {
                    "queued_record": _queued_record(horizon_id="H1"),
                    "generation_id": 8,
                    "physical_generation_id": 8,
                    "process_worker_compile_from_context_allowed": True,
                    "context": {
                        "snapshot": {
                            "schema_version": "skyseed-topology-snapshot-v1",
                            "snapshot_id": "slice-001",
                        },
                        "runtime_update_plan": {
                            "schema_version": "skyseed-runtime-update-plan-v1",
                            "plan_id": "plan-001",
                        },
                    },
                }
            ],
        }

        with self.assertRaisesRegex(RuntimeError, "process_worker_topology_path_missing"):
            run_horizon_maintenance_process_payload(payload)

    def test_process_worker_rejects_missing_attach_worker_limit_before_physical_work(
        self,
    ) -> None:
        payload = {
            "schema_version": "skyseed-horizon-maintenance-process-work-v3",
            "frozen_container_pid_cache": _frozen_container_pid_cache(),
        }
        with patch(
            "horizon_maintenance_process_worker.MinimalRealNetworkMutationExecutor"
        ) as executor:
            with self.assertRaisesRegex(
                RuntimeError,
                "horizon_maintenance_max_attach_workers_missing",
            ):
                run_horizon_maintenance_process_payload(payload)
        executor.assert_not_called()

    def test_process_worker_executes_retired_cleanup_before_ready(self) -> None:
        request = _request(previous_horizon_id="H0")
        queued = _queued_record(
            segment_epoch_key="segment_001:slice_001",
            horizon_id="H1",
            generation_id=8,
            physical_generation_id=8,
        )
        frozen_inputs = {**_compiled_inputs(), "active_generation": {"value": 8}}
        cleanup_work = {
            "schema_version": "skyseed-retired-bpf-generation-cleanup-work-v1",
            "work_type": "delete_retired_generation_manifest_keys",
            "record_id": "retiredcleanup-h0",
            "horizon_id": "H0",
            "generation_id": 7,
            "delete_manifest": [{"map": "ingress", "key": "old"}],
            "delete_manifest_count": 1,
            "status": "queued",
        }
        payload = {
            "schema_version": "skyseed-horizon-maintenance-process-work-v3",
            "frozen_container_pid_cache": _frozen_container_pid_cache(),
            "segment_dir": "",
            "topology_path": "topology.json",
            "link_state_path": "link_state.json",
            "segment_id": "segment_001",
            "horizon_id": "H1",
            "max_node_workers": 2,
            "request": request.to_payload(),
            "retain_generation_ids": [7, 8],
            "prepared_generation_window_count": 2,
            "delta_prepare_scope_by_slot": {},
            "resource_coverage_plan": {},
            "attach_frozen_compiled_runtime_inputs": frozen_inputs,
            "bpf_owned_readiness": _readiness(),
            "base_generation_record": _base_record(),
            "preload_work_items": [
                {
                    "queued_record": queued,
                    "context": _worker_context(),
                    "physical_generation_id": 8,
                    "base_generation_compiled_inputs": _compiled_inputs(),
                }
            ],
            "retired_cleanup_work_items": [cleanup_work],
        }

        with tempfile.TemporaryDirectory() as tmp:
            payload["segment_dir"] = tmp
            payload["topology_path"], payload["link_state_path"] = (
                _write_worker_input_files(Path(tmp))
            )
            with patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs",
                return_value=_attach_report(ok=True),
            ), patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_preload_from_frozen_inputs",
                return_value=_inactive_record(
                    ok=True,
                    inactive_generation_written=True,
                    compiled_runtime_inputs=frozen_inputs,
                ),
            ), patch(
                "bpf_mpls_live.load_mpls_maps",
                return_value={
                    "ok": True,
                    "manifest_only_loader": True,
                    "batch_map_write_used": True,
                    "actual_data_delete_count": 1,
                    "map_write_wall_seconds": 0.01,
                },
            ) as load_maps:
                _externalize_worker_contexts(payload, Path(payload["segment_dir"]))
                summary = run_horizon_maintenance_process_payload(payload)
                loaded = load_ready_artifact(
                    Path(summary["artifact_path"]),
                    expected_request_hash=request.request_hash,
                    expected_next_horizon_id="H1",
                )
                cleanup_handoff = json.loads(
                    Path(loaded["retired_cleanup_handoff_artifact"]).read_text(
                        encoding="utf-8"
                    )
                )

        self.assertEqual(summary["status"], "ready")
        self.assertTrue(summary["retired_cleanup_process_owned"])
        self.assertEqual(summary["retired_cleanup_completed_count"], 1)
        self.assertEqual(
            loaded["retired_cleanup_dependency_report"]["status"],
            "completed",
        )
        self.assertEqual(cleanup_handoff["completed_count"], 1)
        load_maps.assert_called_once()
        self.assertEqual(
            load_maps.call_args.args[0]["bpf_runtime_update_phase"],
            "retired_generation_cleanup",
        )
        self.assertTrue(load_maps.call_args.args[0]["bpf_map_write_manifest_only"])
        self.assertTrue(load_maps.call_args.args[0]["defer_active_generation_flip"])

    def test_process_worker_cleanup_failure_blocks_ready(self) -> None:
        request = _request(previous_horizon_id="H0")
        frozen_inputs = {**_compiled_inputs(), "active_generation": {"value": 8}}
        payload = {
            "schema_version": "skyseed-horizon-maintenance-process-work-v3",
            "frozen_container_pid_cache": _frozen_container_pid_cache(),
            "segment_dir": "",
            "topology_path": "topology.json",
            "link_state_path": "link_state.json",
            "segment_id": "segment_001",
            "horizon_id": "H1",
            "max_node_workers": 2,
            "request": request.to_payload(),
            "retain_generation_ids": [7, 8],
            "prepared_generation_window_count": 2,
            "delta_prepare_scope_by_slot": {},
            "resource_coverage_plan": {},
            "attach_frozen_compiled_runtime_inputs": frozen_inputs,
            "bpf_owned_readiness": _readiness(),
            "base_generation_record": _base_record(),
            "preload_work_items": [
                {
                    "queued_record": _queued_record(horizon_id="H1"),
                    "context": _worker_context(),
                    "physical_generation_id": 8,
                    "base_generation_compiled_inputs": _compiled_inputs(),
                }
            ],
            "retired_cleanup_work_items": [
                {
                    "record_id": "retiredcleanup-h0",
                    "horizon_id": "H0",
                    "generation_id": 7,
                    "delete_manifest": [{"map": "ingress", "key": "old"}],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            payload["segment_dir"] = tmp
            payload["topology_path"], payload["link_state_path"] = (
                _write_worker_input_files(Path(tmp))
            )
            with patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs",
                return_value=_attach_report(ok=True),
            ), patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_preload_from_frozen_inputs",
                return_value=_inactive_record(
                    ok=True,
                    inactive_generation_written=True,
                    compiled_runtime_inputs=frozen_inputs,
                ),
            ), patch(
                "bpf_mpls_live.load_mpls_maps",
                return_value={"ok": False, "reason": "delete_failed"},
            ):
                _externalize_worker_contexts(payload, Path(payload["segment_dir"]))
                summary = run_horizon_maintenance_process_payload(payload)
                self.assertTrue(Path(summary["artifact_path"]).exists())

        self.assertEqual(summary["status"], "failed")
        self.assertFalse(summary["ready"])
        self.assertEqual(summary["retired_cleanup_blocked_count"], 1)

    def test_process_worker_loads_compact_manifest_items_and_restores_prepared_state(self) -> None:
        request = _request()
        frozen_inputs = {**_compiled_inputs(), "active_generation": {"value": 8}}
        queued = _queued_record(
            segment_epoch_key="segment_001:slice_001",
            horizon_id="H1",
            generation_id=8,
            physical_generation_id=8,
        )

        class FakeExecutor:
            instances: list["FakeExecutor"] = []

            def __init__(self) -> None:
                self.restore_calls: list[dict] = []
                self.prepare_calls: list[dict] = []
                FakeExecutor.instances.append(self)

            def restore_prepared_runtime_state(self, **kwargs: object) -> dict:
                self.restore_calls.append(dict(kwargs))
                return {
                    "ok": True,
                    "restored_for_process_worker": True,
                    "future_host_link_state_apply_allowed": False,
                }

            def prepare_additive_resources(self, **kwargs: object) -> dict:
                self.prepare_calls.append(dict(kwargs))
                return _host_report(
                    prepared_resource_scope_by_slot={
                        "slot-existing": ["resource-existing"],
                        "slot-new": ["resource-new"],
                    },
                    delta_prepare_resource_scope_by_slot={
                        "slot-new": ["resource-new"]
                    },
                    wall_seconds=0.01,
                )

            def capture_existing_container_interface_bindings(
                self, *, interface_refs: object
            ) -> dict:
                if list(interface_refs):
                    raise AssertionError(interface_refs)
                return {"ok": True, "full_scan": False, "bindings": []}

        with tempfile.TemporaryDirectory() as tmp:
            segment_dir = Path(tmp)
            topology_path, link_state_path = _write_worker_input_files(segment_dir)
            items_path = segment_dir / "horizon_maintenance_process_work_items.jsonl"
            items_path.write_text(
                json.dumps(
                    {
                        "queued_record": queued,
                        "context": _worker_context(),
                        "physical_generation_id": 8,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            payload = {
                "schema_version": "skyseed-horizon-maintenance-process-work-v3",
            "frozen_container_pid_cache": _frozen_container_pid_cache(),
                "segment_dir": str(segment_dir),
                "topology_path": topology_path,
                "link_state_path": link_state_path,
                "segment_id": "segment_001",
                "horizon_id": "H1",
                "max_node_workers": 2,
                "request": request.to_payload(),
                "retain_generation_ids": [7, 8],
                "prepared_generation_window_count": 2,
                "existing_prepared_resource_scope_by_slot": {
                    "slot-existing": ["resource-existing"]
                },
                "delta_prepare_scope_by_slot": {"slot-new": ["resource-new"]},
                "prepared_resource_scope_by_slot": {
                    "slot-existing": ["resource-existing"],
                    "slot-new": ["resource-new"],
                },
                "resource_coverage_plan": {},
                "attach_frozen_compiled_runtime_inputs": frozen_inputs,
                "bpf_owned_readiness": _readiness(),
                "base_generation_record": _base_record(),
                "preload_work_items": [],
                "preload_work_items_artifact": str(items_path),
                "process_input_compact_manifest": True,
            }
            with patch(
                "horizon_maintenance_process_worker.MinimalRealNetworkMutationExecutor",
                FakeExecutor,
            ), patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs",
                return_value=_attach_report(ok=True),
            ), patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_preload_from_frozen_inputs",
                return_value=_inactive_record(
                    ok=True,
                    inactive_generation_written=True,
                    compiled_runtime_inputs=frozen_inputs,
                ),
            ):
                _externalize_worker_contexts(payload, Path(payload["segment_dir"]))
                summary = run_horizon_maintenance_process_payload(payload)
                loaded = load_ready_artifact(
                    Path(summary["artifact_path"]),
                    expected_request_hash=request.request_hash,
                    expected_next_horizon_id="H1",
                )

        self.assertEqual(summary["status"], "ready")
        self.assertTrue(summary["process_input_compact_manifest"])
        self.assertEqual(summary["preload_work_item_count"], 1)
        self.assertEqual(len(FakeExecutor.instances), 1)
        self.assertEqual(
            FakeExecutor.instances[0].restore_calls[0][
                "prepared_resource_scope_by_slot"
            ],
            {"slot-existing": ["resource-existing"]},
        )
        self.assertEqual(
            FakeExecutor.instances[0].prepare_calls[0][
                "prepared_resource_scope_by_slot"
            ],
            {"slot-new": ["resource-new"]},
        )
        self.assertEqual(
            loaded["host_additive_prepare_report"][
                "prepared_resource_scope_by_slot"
            ],
            {
                "slot-existing": ["resource-existing"],
                "slot-new": ["resource-new"],
            },
        )

    def test_in_process_manager_restores_prepared_state_for_process_worker(self) -> None:
        class FakeInstaller:
            execution_transport = "fake"

            def __init__(self) -> None:
                self.restore_calls: list[dict] = []

            def restore_prepared_runtime_state(self, **kwargs: object) -> dict:
                self.restore_calls.append(dict(kwargs))
                return {
                    "ok": True,
                    "restored_for_process_worker": True,
                    "prepared_resource_scope_slot_count": 1,
                }

            def state_by_name(self, *, link_state_path: Path) -> dict:
                return {
                    "slot-a": {
                        "slot_name": "slot-a",
                        "slot_type": "ut_access",
                        "occupied": False,
                        "binding": {},
                        "endpoints": [],
                        "runtime_parameters": {},
                    }
                }

            def public_runtime_summary(self) -> dict:
                return {"installer_runtime_prepared": True}

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            link_state_path = tmp_path / "link_state.json"
            link_state_path.write_text(
                json.dumps({"runtime_initialized": True}),
                encoding="utf-8",
            )
            installer = FakeInstaller()
            manager = InProcessHostRuntimeManager(installer=installer)

            report = manager.restore_prepared_runtime_state(
                topology_path=tmp_path / "topology.json",
                link_state_path=link_state_path,
                prepared_resource_scope_by_slot={"slot-a": ["resource-a"]},
                prepared_resource_counts={"bridge_count": 1},
                frozen_container_pid_cache=_frozen_container_pid_cache(),
            )

        self.assertTrue(report["restored_for_process_worker"])
        self.assertEqual(report["host_runtime_state"]["owned_slot_state_count"], 1)
        self.assertEqual(
            installer.restore_calls[0]["prepared_resource_scope_by_slot"],
            {"slot-a": ["resource-a"]},
        )
        self.assertEqual(
            installer.restore_calls[0]["frozen_container_pid_cache"],
            _frozen_container_pid_cache(),
        )

    def test_process_worker_partial_preload_publishes_failure_not_ready(self) -> None:
        request = _request()
        frozen_inputs = {**_compiled_inputs(), "active_generation": {"value": 8}}
        payload = {
            "schema_version": "skyseed-horizon-maintenance-process-work-v3",
            "frozen_container_pid_cache": _frozen_container_pid_cache(),
            "segment_dir": "",
            "topology_path": "topology.json",
            "link_state_path": "link_state.json",
            "segment_id": "segment_001",
            "horizon_id": "H1",
            "max_node_workers": 2,
            "request": request.to_payload(),
            "retain_generation_ids": [7, 8],
            "prepared_generation_window_count": 2,
            "delta_prepare_scope_by_slot": {},
            "resource_coverage_plan": {},
            "attach_frozen_compiled_runtime_inputs": frozen_inputs,
            "bpf_owned_readiness": _readiness(),
            "base_generation_record": _base_record(),
            "preload_work_items": [
                {
                    "queued_record": _queued_record(horizon_id="H1"),
                    "context": _worker_context(),
                    "physical_generation_id": 8,
                    "base_generation_compiled_inputs": _compiled_inputs(),
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            payload["segment_dir"] = tmp
            payload["topology_path"], payload["link_state_path"] = (
                _write_worker_input_files(Path(tmp))
            )
            with patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs",
                return_value=_attach_report(ok=True),
            ), patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_preload_from_frozen_inputs",
                return_value=_inactive_record(
                    ok=False,
                    reason="map_write_failed",
                    inactive_generation_written=False,
                    compiled_runtime_inputs={},
                ),
            ):
                _externalize_worker_contexts(payload, Path(payload["segment_dir"]))
                summary = run_horizon_maintenance_process_payload(payload)

            self.assertEqual(Path(summary["artifact_path"]).name, "horizon_maintenance_failure.json")
            self.assertEqual(summary["status"], "failed")
            with self.assertRaises(RuntimeError):
                load_ready_artifact(Path(summary["artifact_path"]))

    def test_process_worker_rejects_ok_preload_without_inactive_write(self) -> None:
        request = _request()
        frozen_inputs = {**_compiled_inputs(), "active_generation": {"value": 8}}
        payload = {
            "schema_version": "skyseed-horizon-maintenance-process-work-v3",
            "frozen_container_pid_cache": _frozen_container_pid_cache(),
            "segment_dir": "",
            "topology_path": "topology.json",
            "link_state_path": "link_state.json",
            "segment_id": "segment_001",
            "horizon_id": "H1",
            "max_node_workers": 2,
            "request": request.to_payload(),
            "retain_generation_ids": [7, 8],
            "prepared_generation_window_count": 2,
            "delta_prepare_scope_by_slot": {},
            "resource_coverage_plan": {},
            "attach_frozen_compiled_runtime_inputs": frozen_inputs,
            "bpf_owned_readiness": _readiness(),
            "base_generation_record": _base_record(),
            "preload_work_items": [
                {
                    "queued_record": _queued_record(horizon_id="H1"),
                    "context": _worker_context(),
                    "physical_generation_id": 8,
                    "base_generation_compiled_inputs": _compiled_inputs(),
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            payload["segment_dir"] = tmp
            payload["topology_path"], payload["link_state_path"] = (
                _write_worker_input_files(Path(tmp))
            )
            with patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_attach_capability_from_frozen_inputs",
                return_value=_attach_report(ok=True),
            ), patch(
                "horizon_maintenance_process_worker."
                "_prepare_bpf_owned_preactivation_preload_from_frozen_inputs",
                return_value=_inactive_record(
                    ok=True,
                    inactive_generation_written=False,
                    compiled_runtime_inputs=frozen_inputs,
                ),
            ):
                _externalize_worker_contexts(payload, Path(payload["segment_dir"]))
                summary = run_horizon_maintenance_process_payload(payload)

            self.assertEqual(summary["status"], "failed")
            self.assertEqual(summary["result_blockers"][0]["reason"], "preload_record_not_written")

    def test_process_worker_exception_publishes_failure_artifact(self) -> None:
        request = _request()
        with tempfile.TemporaryDirectory() as tmp:
            segment_dir = Path(tmp)
            input_path = segment_dir / "horizon_maintenance_process_work.json"
            output_path = segment_dir / "horizon_maintenance_process_summary.json"
            input_path.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "skyseed-horizon-maintenance-process-work-v3"
                        ),
                        "segment_dir": str(segment_dir),
                        "request": request.to_payload(),
                        "horizon_id": "H1",
                        "preload_work_item_count": 1,
                    }
                ),
                encoding="utf-8",
            )

            summary = _publish_process_exception_failure(
                input_path=input_path,
                output_path=output_path,
                exc=RuntimeError("additive_prepare_requires_existing_runtime_prepared"),
            )

            failure_path = Path(summary["artifact_path"])
            failure = json.loads(failure_path.read_text(encoding="utf-8"))

        self.assertEqual(failure_path.name, "horizon_maintenance_failure.json")
        self.assertEqual(summary["status"], "failed")
        self.assertTrue(summary["failure_artifact_published"])
        self.assertEqual(failure["status"], "failed")
        self.assertFalse(failure["ready"])
        self.assertTrue(failure["process_exception"])
        self.assertEqual(failure["queued_preload_record_count"], 1)

    def test_process_audit_accepts_complete_ready_process_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            segment_dir = Path(tmp) / "replenished_segments" / "segment_001"
            segment_dir.mkdir(parents=True)
            request = _request()
            work = {
                "schema_version": "skyseed-horizon-maintenance-process-work-v3",
            "frozen_container_pid_cache": _frozen_container_pid_cache(),
                "request": request.to_payload(),
                "horizon_id": "H1",
                "future_host_link_activation_allowed": False,
                "runtime_full_reload_allowed": False,
            }
            queued = {
                "schema_version": "skyseed-horizon-maintenance-queued-v1",
                "request": request.to_payload(),
                "horizon_id": "H1",
                "worker_mode": "process",
                "expected_preload_count": 1,
            }
            summary = {
                "schema_version": "skyseed-horizon-maintenance-process-summary-v1",
                "status": "ready",
                "ready": True,
            }
            ready = {
                "schema_version": "skyseed-horizon-maintenance-ready-v1",
                "status": "ready",
                "ready": True,
                "horizon_id": "H1",
                "worker_mode": "process",
                "process_boundary": True,
                "inactive_preload_record_count": 1,
                "active_generation_flip_allowed": False,
                "runtime_full_reload_allowed": False,
            }
            (segment_dir / "horizon_maintenance_process_work.json").write_text(
                json.dumps(work),
                encoding="utf-8",
            )
            (segment_dir / "horizon_maintenance_queued.json").write_text(
                json.dumps(queued),
                encoding="utf-8",
            )
            (segment_dir / "horizon_maintenance_process_summary.json").write_text(
                json.dumps(summary),
                encoding="utf-8",
            )
            (segment_dir / "horizon_maintenance_ready.json").write_text(
                json.dumps(ready),
                encoding="utf-8",
            )

            report = audit_horizon_maintenance_process(Path(tmp))

        self.assertTrue(report["ok"])
        self.assertEqual(report["process_horizon_count"], 1)
        self.assertEqual(report["blocker_count"], 0)
        self.assertTrue(report["rows"][0]["process_boundary"])

    def test_process_audit_blocks_forbidden_ready_and_missing_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            segment_1 = root / "replenished_segments" / "segment_001"
            segment_2 = root / "replenished_segments" / "segment_002"
            segment_1.mkdir(parents=True)
            segment_2.mkdir(parents=True)
            request = _request()
            for segment_dir in (segment_1, segment_2):
                (segment_dir / "horizon_maintenance_process_work.json").write_text(
                    json.dumps(
                        {
                            "schema_version": (
                                "skyseed-horizon-maintenance-process-work-v3"
                            ),
                            "request": request.to_payload(),
                            "horizon_id": "H1",
                            "active_generation_flip_allowed": False,
                        }
                    ),
                    encoding="utf-8",
                )
                (segment_dir / "horizon_maintenance_queued.json").write_text(
                    json.dumps(
                        {
                            "schema_version": (
                                "skyseed-horizon-maintenance-queued-v1"
                            ),
                            "request": request.to_payload(),
                            "horizon_id": "H1",
                            "worker_mode": "process",
                            "expected_preload_count": 1,
                        }
                    ),
                    encoding="utf-8",
                )
            (segment_1 / "horizon_maintenance_ready.json").write_text(
                json.dumps(
                    {
                        "schema_version": "skyseed-horizon-maintenance-ready-v1",
                        "status": "ready",
                        "ready": True,
                        "horizon_id": "H1",
                        "worker_mode": "process",
                        "process_boundary": True,
                        "inactive_preload_record_count": 1,
                        "active_generation_flip_allowed": True,
                    }
                ),
                encoding="utf-8",
            )

            report = audit_horizon_maintenance_process(root)

        self.assertFalse(report["ok"])
        reasons = {blocker["reason"] for blocker in report["blockers"]}
        self.assertIn("forbidden_true_fields", reasons)
        self.assertIn("process_worker_no_terminal_artifact", reasons)

    def test_process_audit_classifies_summary_failure_and_oversize_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            segment_dir = root / "replenished_segments" / "segment_001"
            segment_dir.mkdir(parents=True)
            request = _request()
            (segment_dir / "horizon_maintenance_process_work.json").write_bytes(
                b'{"schema_version":"skyseed-horizon-maintenance-process-work-v3",'
                + b'"padding":"'
                + (b"x" * (6 * 1024 * 1024))
                + b'"}'
            )
            (segment_dir / "horizon_maintenance_queued.json").write_text(
                json.dumps(
                    {
                        "schema_version": "skyseed-horizon-maintenance-queued-v1",
                        "request": request.to_payload(),
                        "horizon_id": "H1",
                        "worker_mode": "process",
                        "expected_preload_count": 1,
                    }
                ),
                encoding="utf-8",
            )
            (segment_dir / "horizon_maintenance_process_summary.json").write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "skyseed-horizon-maintenance-process-summary-v1"
                        ),
                        "status": "failed",
                        "ready": False,
                        "reason": "additive_prepare_requires_existing_runtime_prepared",
                    }
                ),
                encoding="utf-8",
            )

            report = audit_horizon_maintenance_process(root)

        self.assertFalse(report["ok"])
        row = report["rows"][0]
        self.assertTrue(row["process_work_too_large_for_inline_audit"])
        reasons = {blocker["reason"] for blocker in report["blockers"]}
        self.assertIn("process_worker_summary_failed", reasons)
        self.assertIn("process_worker_failed_without_failure_artifact", reasons)
        self.assertIn("process_work_artifact_too_large_not_compact_manifest", reasons)


if __name__ == "__main__":
    unittest.main()
