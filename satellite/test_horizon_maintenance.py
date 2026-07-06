import json
import tempfile
import unittest
from pathlib import Path

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


def _compiled_inputs() -> dict:
    return {
        "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v1",
        "active_generation": {"value": 7},
        "ingress_classifier_map": {"path": "ingress.manifest.json"},
        "core_lfib_map": {"path": "core.manifest.json"},
        "egress_handoff_map": {"path": "egress.manifest.json"},
    }


def _base_record() -> dict:
    return {
        "ok": True,
        "generation_id": 7,
        "physical_generation_id": 7,
        "source": "readiness.active_generation_compiled_runtime_inputs",
        "compiled_runtime_inputs": _compiled_inputs(),
    }


def _request(expected_preload_count: int = 1) -> HorizonMaintenanceRequest:
    return HorizonMaintenanceRequest(
        segment_index=1,
        segment_id="segment_001",
        current_horizon_id="H0",
        next_horizon_id="H1",
        previous_horizon_id="",
        source_state_id="live_state:H0",
        base_generation_id=7,
        prepared_generation_ids=(7, 8),
        expected_preload_count=expected_preload_count,
        worker_mode="synchronous",
    )


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
    def test_missing_base_compiled_inputs_fail_closed_and_reject_summary(self) -> None:
        compact = {
            "ok": True,
            "generation_id": 7,
            "compiled_runtime_inputs": {
                "compiled_runtime_inputs_summary": {"present": True},
                "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v1",
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
                    "schema_version": "skyseed-bpf-owned-compiled-runtime-inputs-v1",
                    "compiled_runtime_inputs_summary": {"present": True},
                },
            }
        )
        self.assertFalse(compact_check["ready"])
        self.assertTrue(compact_check["compact_summary_rejected"])

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


if __name__ == "__main__":
    unittest.main()
