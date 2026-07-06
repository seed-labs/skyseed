#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from apps.run_skyseed_validation_profile import (
    ProfileContractError,
    _cleanup_evidence_contract,
    _first_failure_summary,
    _normal_failure_cleanup_required,
    _persistent_artifact_contract,
    build_underlying_command,
    detect_cleanup_residue,
    docker_dummy_network_capacity_gate,
    evaluate_normal_path_artifact_contract,
    evaluate_static_inventory_precreated_connection_audit,
    evaluate_runtime_preload_diff_contract,
    evaluate_startup_operation_audit,
    evaluate_startup_materialization_contract,
    evaluate_startup_state_supervision_contract,
    external_cleanup_success,
    load_profile,
    normalize_cleanup_report_contract,
    reconcile_lifecycle_horizon_completion_with_execution,
    refresh_profile_run_summary_derived_artifacts,
    resolve_catalog_contract,
    run_cleanup_only,
    write_runtime_update_timing_evidence,
)


def _completed(
    stdout: str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class SkyseedValidationProfileTests(unittest.TestCase):
    def _subprocess_env_without_pythonpath(self) -> dict[str, str]:
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        return env

    def test_profile_runner_import_smoke_without_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    "python3",
                    "satellite/apps/run_skyseed_validation_profile.py",
                    "--profile",
                    "rich_s4_operational_clean_bpf_smoke",
                    "--run-root",
                    tmp,
                    "--run-id",
                    "profile-import-smoke",
                    "--dry-run",
                    "--format",
                    "json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=self._subprocess_env_without_pythonpath(),
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("ModuleNotFoundError", completed.stderr)

    def test_underlying_runner_import_smoke_without_pythonpath(self) -> None:
        completed = subprocess.run(
            [
                "python3",
                "satellite/apps/run_starlink_shell_experiment.py",
                "--help",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=self._subprocess_env_without_pythonpath(),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("ModuleNotFoundError", completed.stderr)
        self.assertIn("usage:", completed.stdout)

    def test_bpf_backend_imports_from_app_runner_context_without_pythonpath(self) -> None:
        completed = subprocess.run(
            [
                "python3",
                "-c",
                (
                    "import sys; "
                    "sys.path.insert(0, 'satellite/apps'); "
                    "from _path_setup import bootstrap_app_import_path; "
                    "bootstrap_app_import_path(); "
                    "import bpf_backend; "
                    "import satellite.bpf_backend; "
                    "print('NO_RICH_S4_RERUN_NEEDED_FOR_IMPORT')"
                ),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=self._subprocess_env_without_pythonpath(),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("ModuleNotFoundError: No module named 'satellite'", completed.stderr)
        self.assertIn("NO_RICH_S4_RERUN_NEEDED_FOR_IMPORT", completed.stdout)

    def test_slow_but_progressing_stage_timeout_is_resource_abort_not_soft_budget(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        runner_report = {
            "failure_reason": "docker_compose_up_timeout",
            "runtime_blocker": {
                "classified_blocker": "RESOURCE_DEADLOCK_STARTUP_ABORT",
                "kill_reason": "resource_exhaustion_or_deadlock",
                "stage": "docker_compose_up",
                "compose_up_stage": "satellites-29",
                "timeout_seconds": 900.0,
                "wall_seconds": 905.0,
                "startup_progress_contract": {
                    "soft_budget_exceeded": True,
                    "progress_grace_applied": True,
                    "high_progress_continue": True,
                    "no_progress_stall": False,
                    "hard_cap_exceeded": False,
                },
            },
            "timing_analysis": {
                "startup_preparation": {"compose_up_wall_seconds": 905.0}
            },
            "startup_strategy": {
                "compose_stage_count": 84,
                "footprint": {
                    "service_count": 468,
                    "network_count": 46,
                    "attachment_count": 518,
                },
                "compose_stage_results": [
                    {
                        "stage_id": f"stage-{index}",
                        "stage_type": "satellite_batch",
                        "service_count": 12,
                        "wall_seconds": 15.0,
                        "timeout_seconds": 90.0,
                        "timed_out": False,
                    }
                    for index in range(83)
                ]
                + [
                    {
                        "stage_id": "satellites-29",
                        "stage_type": "satellite_batch",
                        "service_count": 12,
                        "wall_seconds": 14.8,
                        "timeout_seconds": 14.8,
                        "timed_out": True,
                    }
                ],
            },
        }

        contract = evaluate_startup_materialization_contract(
            profile=profile,
            runner_report=runner_report,
            capacity_gate={"required_dummy_networks": 772},
        )

        self.assertEqual(
            contract["timeout_classification"],
            "resource_deadlock_startup_abort",
        )
        self.assertTrue(contract["progress_was_happening_at_timeout"])
        self.assertFalse(contract["actual_stall_detected"])
        self.assertIn("STARTUP_SOFT_BUDGET_EXCEEDED_WARNING", contract["outcome_labels"])
        self.assertIn("STARTUP_MATERIALIZATION_COST_ANOMALY", contract["outcome_labels"])
        self.assertIn("SLOW_BUT_PROGRESSING_CONTINUE", contract["outcome_labels"])
        self.assertIn("RESOURCE_DEADLOCK_STARTUP_ABORT", contract["outcome_labels"])
        self.assertEqual(contract["kill_reason"], "resource_exhaustion_or_deadlock")
        self.assertEqual(contract["completed_batches"], 83)
        self.assertEqual(contract["remaining_batches"], 1)

    def test_progressing_startup_after_soft_budget_is_not_contract_failure(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        runner_report = {
            "failure_reason": "",
            "timing_analysis": {
                "startup_preparation": {"compose_up_wall_seconds": 1090.0}
            },
            "startup_strategy": {
                "compose_stage_count": 72,
                "footprint": {
                    "service_count": 468,
                    "network_count": 46,
                    "attachment_count": 518,
                },
                "compose_stage_results": [
                    {
                        "stage_id": f"stage-{index}",
                        "stage_type": "satellite_batch",
                        "service_count": 12,
                        "wall_seconds": 15.0,
                        "timeout_seconds": 90.0,
                        "timed_out": False,
                        "startup_progress_contract": {
                            "soft_budget_exceeded": True,
                            "progress_grace_applied": True,
                            "high_progress_continue": True,
                            "no_progress_stall": False,
                            "absolute_max_exceeded": False,
                        },
                    }
                    for index in range(72)
                ],
            },
        }

        contract = evaluate_startup_materialization_contract(
            profile=profile,
            runner_report=runner_report,
            capacity_gate={"required_dummy_networks": 772},
        )

        self.assertTrue(contract["ok"])
        self.assertTrue(contract["soft_budget_exceeded"])
        self.assertTrue(contract["progress_grace_applied"])
        self.assertTrue(contract["high_progress_continue"])
        self.assertFalse(contract["no_progress_stall"])
        self.assertFalse(contract["absolute_cap_exceeded"])
        self.assertIn("STARTUP_SOFT_BUDGET_EXCEEDED_WARNING", contract["outcome_labels"])
        self.assertIn("STARTUP_MATERIALIZATION_COST_ANOMALY", contract["outcome_labels"])
        self.assertIn("SLOW_BUT_PROGRESSING_CONTINUE", contract["outcome_labels"])
        self.assertNotIn("STARTUP_MATERIALIZATION_COST_BLOCKER", contract["outcome_labels"])

    def test_stalled_startup_timeout_is_not_reported_as_progressing(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        runner_report = {
            "failure_reason": "docker_compose_up_timeout",
            "runtime_blocker": {
                "compose_up_stage": "helpers-one-shot",
                "timeout_seconds": 900.0,
                "wall_seconds": 900.0,
            },
            "startup_strategy": {
                "compose_stage_count": 84,
                "footprint": {
                    "service_count": 468,
                    "network_count": 46,
                    "attachment_count": 518,
                },
                "compose_stage_results": [
                    {
                        "stage_id": "helpers-one-shot",
                        "stage_type": "helper",
                        "service_count": 2,
                        "wall_seconds": 180.0,
                        "timeout_seconds": 180.0,
                        "timed_out": True,
                    }
                ],
            },
        }

        contract = evaluate_startup_materialization_contract(
            profile=profile,
            runner_report=runner_report,
            capacity_gate={"required_dummy_networks": 772},
        )

        self.assertEqual(contract["timeout_classification"], "resource_deadlock_startup_abort")
        self.assertFalse(contract["progress_was_happening_at_timeout"])
        self.assertTrue(contract["actual_stall_detected"])
        self.assertIn("RESOURCE_DEADLOCK_STARTUP_ABORT", contract["outcome_labels"])

    def test_compose_timeout_invokes_external_cleanup_contract(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        residue = {
            "counts": {
                "compose_containers": 1,
                "compose_networks": 1,
                "direct_backend_host_interfaces": 0,
                "bpf_pins": 0,
                "runtime_locks": 0,
                "docker_network_pool_overlaps": 1,
            }
        }

        self.assertTrue(
            _normal_failure_cleanup_required(
                profile=profile,
                completed_returncode=0,
                runner_report={"failure_reason": "docker_compose_up_timeout"},
                residue=residue,
            )
        )

    def test_retryable_final_batch_failure_produces_recoverable_checkpoint(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        runner_report = {
            "runtime_blocker": {
                "stage": "docker_compose_up",
                "classified_blocker": "STARTUP_RECOVERABLE_STAGE_FAILURE",
                "compose_up_stage": "satellites-29",
                "kill_reason": None,
            },
            "startup_strategy": {
                "compose_stage_count": 3,
                "footprint": {
                    "service_count": 36,
                    "network_count": 4,
                    "attachment_count": 36,
                },
                "compose_stage_results": [
                    {
                        "stage_id": "satellites-27",
                        "stage_type": "satellite_batch",
                        "service_names": ["sat-a"],
                        "service_count": 1,
                        "timed_out": False,
                        "returncode": 0,
                    },
                    {
                        "stage_id": "satellites-28",
                        "stage_type": "satellite_batch",
                        "service_names": ["sat-b"],
                        "service_count": 1,
                        "timed_out": False,
                        "returncode": 0,
                    },
                    {
                        "stage_id": "satellites-29",
                        "batch_id": "batch-final",
                        "stage_type": "satellite_batch",
                        "service_names": ["sat-c"],
                        "service_count": 1,
                        "timed_out": True,
                        "returncode": None,
                    },
                ],
            },
        }
        startup_contract = evaluate_startup_materialization_contract(
            profile=profile,
            runner_report=runner_report,
            capacity_gate={"required_dummy_networks": 772},
        )
        supervision = evaluate_startup_state_supervision_contract(
            profile=profile,
            runner_report=runner_report,
            startup_contract=startup_contract,
        )

        self.assertEqual(startup_contract["timeout_classification"], "recoverable_stage_failure")
        self.assertFalse(startup_contract["resource_deadlock_startup_abort"])
        self.assertTrue(supervision["resume_checkpoint_available"])
        self.assertFalse(supervision["cleanup_required"])
        self.assertEqual(supervision["exact_blocked_stage"], "satellites-29")
        self.assertEqual(supervision["exact_blocked_batch"], "batch-final")
        self.assertIn("STARTUP_RECOVERABLE_STAGE_FAILURE", supervision["outcome_labels"])
        self.assertIn("STARTUP_RESUMABLE_CHECKPOINT_AVAILABLE", supervision["outcome_labels"])
        self.assertIn(
            "STARTUP_RECOVERABLE_BUT_RESUME_NOT_IMPLEMENTED",
            supervision["outcome_labels"],
        )
        self.assertIn(
            "CLEANUP_NOT_REQUIRED_RECOVERABLE_STATE",
            supervision["outcome_labels"],
        )
        self.assertFalse(
            _normal_failure_cleanup_required(
                profile=profile,
                completed_returncode=1,
                runner_report=runner_report,
                residue={
                    "counts": {
                        "bpf_pins": 0,
                        "compose_containers": 0,
                        "compose_networks": 0,
                        "direct_backend_host_interfaces": 0,
                        "docker_network_pool_overlaps": 0,
                        "runtime_locks": 0,
                    }
                },
                startup_state_supervision=supervision,
            )
        )

    def test_correctness_threatening_startup_error_requires_cleanup(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        runner_report = {
            "runtime_blocker": {
                "stage": "docker_compose_up",
                "classified_blocker": "STARTUP_CORRECTNESS_FATAL",
                "compose_up_stage": "helpers-one-shot",
            },
            "startup_strategy": {
                "compose_stage_count": 1,
                "compose_stage_results": [
                    {
                        "stage_id": "helpers-one-shot",
                        "stage_type": "helper",
                        "service_names": ["host-setup"],
                        "service_count": 1,
                        "timed_out": False,
                        "returncode": 2,
                        "correctness_fatal": True,
                    }
                ],
            },
        }
        startup_contract = evaluate_startup_materialization_contract(
            profile=profile,
            runner_report=runner_report,
            capacity_gate={"required_dummy_networks": 772},
        )
        supervision = evaluate_startup_state_supervision_contract(
            profile=profile,
            runner_report=runner_report,
            startup_contract=startup_contract,
        )

        self.assertTrue(supervision["cleanup_required"])
        self.assertIn("STARTUP_CORRECTNESS_FATAL", supervision["outcome_labels"])
        self.assertIn("CLEANUP_REQUIRED", supervision["outcome_labels"])

    def test_resource_deadlock_startup_requires_cleanup(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        runner_report = {
            "runtime_blocker": {
                "stage": "docker_compose_up",
                "classified_blocker": "RESOURCE_DEADLOCK_STARTUP_ABORT",
                "compose_up_stage": "satellites-12",
                "kill_reason": "resource_exhaustion_or_deadlock",
            },
            "startup_strategy": {
                "compose_stage_count": 2,
                "compose_stage_results": [
                    {
                        "stage_id": "satellites-11",
                        "stage_type": "satellite_batch",
                        "service_names": ["sat-a"],
                        "service_count": 1,
                        "timed_out": False,
                        "returncode": 0,
                    },
                    {
                        "stage_id": "satellites-12",
                        "stage_type": "satellite_batch",
                        "service_names": ["sat-b"],
                        "service_count": 1,
                        "timed_out": True,
                        "returncode": None,
                    },
                ],
            },
        }
        startup_contract = evaluate_startup_materialization_contract(
            profile=profile,
            runner_report=runner_report,
            capacity_gate={"required_dummy_networks": 772},
        )
        supervision = evaluate_startup_state_supervision_contract(
            profile=profile,
            runner_report=runner_report,
            startup_contract=startup_contract,
        )

        self.assertTrue(supervision["cleanup_required"])
        self.assertIn("STARTUP_RESOURCE_DEADLOCK", supervision["outcome_labels"])
        self.assertIn("CLEANUP_REQUIRED", supervision["outcome_labels"])

    def test_startup_operation_audit_flags_duplicate_noop_and_full_reconcile(self) -> None:
        runner_report = {
            "startup_operation_trace": [
                {
                    "operation_id": "op-create-a-1",
                    "operation_type": "resource_create",
                    "owner_module": "startup",
                    "stage": "satellites-01",
                    "batch": "batch-01",
                    "target_resource": "sat-a",
                    "target_resource_type": "container",
                    "intent": "materialize satellite container",
                    "reason": "initial materialization",
                    "idempotency_key": "container:sat-a",
                    "expected_cardinality": 1,
                    "actual_attempt_count": 1,
                    "duplicate_count": 0,
                    "retry_count": 0,
                    "duration_seconds": 2.0,
                    "state_changed": True,
                    "result": "created",
                    "error": "",
                },
                {
                    "operation_id": "op-create-a-2",
                    "operation_type": "resource_create",
                    "owner_module": "startup",
                    "stage": "satellites-01",
                    "batch": "batch-01",
                    "target_resource": "sat-a",
                    "target_resource_type": "container",
                    "intent": "materialize satellite container",
                    "reason": "duplicate retry",
                    "idempotency_key": "container:sat-a",
                    "expected_cardinality": 1,
                    "actual_attempt_count": 1,
                    "duplicate_count": 1,
                    "retry_count": 1,
                    "duration_seconds": 3.0,
                    "state_changed": False,
                    "result": "noop",
                    "error": "",
                },
                {
                    "operation_id": "op-full-1",
                    "operation_type": "full_reconcile_prewarm",
                    "owner_module": "startup",
                    "stage": "prewarm",
                    "batch": "all",
                    "target_resource": "static-inventory",
                    "target_resource_type": "inventory",
                    "intent": "prewarm static inventory",
                    "reason": "startup static inventory prewarm pass",
                    "idempotency_key": "inventory:full",
                    "expected_cardinality": 1,
                    "actual_attempt_count": 2,
                    "duplicate_count": 0,
                    "retry_count": 0,
                    "duration_seconds": 5.0,
                    "state_changed": False,
                    "result": "noop",
                    "error": "",
                    "full_scope": True,
                },
                {
                    "operation_id": "op-full-2",
                    "operation_type": "full_reconcile_prewarm",
                    "owner_module": "startup",
                    "stage": "prewarm",
                    "batch": "all",
                    "target_resource": "static-inventory",
                    "target_resource_type": "inventory",
                    "intent": "prewarm static inventory",
                    "reason": "startup static inventory prewarm pass",
                    "idempotency_key": "inventory:full",
                    "expected_cardinality": 1,
                    "actual_attempt_count": 1,
                    "duplicate_count": 1,
                    "retry_count": 0,
                    "duration_seconds": 7.0,
                    "state_changed": False,
                    "result": "noop",
                    "error": "",
                    "full_scope": True,
                },
                {
                    "operation_id": "op-wait",
                    "operation_type": "readiness_wait",
                    "owner_module": "startup",
                    "stage": "readiness",
                    "batch": "all",
                    "target_resource": "compose-services",
                    "target_resource_type": "container_set",
                    "intent": "wait for readiness",
                    "reason": "containers starting",
                    "idempotency_key": "readiness:all",
                    "expected_cardinality": 1,
                    "actual_attempt_count": 1,
                    "duplicate_count": 0,
                    "retry_count": 0,
                    "duration_seconds": 11.0,
                    "state_changed": False,
                    "result": "ready",
                    "error": "",
                },
            ]
        }

        audit = evaluate_startup_operation_audit(runner_report=runner_report)

        self.assertTrue(audit["operation_trace_complete"])
        self.assertIn("STARTUP_REDUNDANT_OPERATION_LOOP", audit["outcome_labels"])
        self.assertIn("STARTUP_DUPLICATE_RESOURCE_OPERATION", audit["outcome_labels"])
        self.assertIn("STARTUP_NOOP_RETRY_LOOP", audit["outcome_labels"])
        self.assertIn("STARTUP_FULL_RECONCILE_REPEATED", audit["outcome_labels"])
        self.assertIn("STARTUP_OPERATION_CARDINALITY_ANOMALY", audit["outcome_labels"])
        self.assertGreater(audit["cost_breakdown"]["real_materialization_work_seconds"], 0.0)
        self.assertGreater(audit["cost_breakdown"]["readiness_waiting_seconds"], 0.0)
        self.assertGreater(audit["cost_breakdown"]["retry_cost_seconds"], 0.0)
        self.assertGreater(audit["cost_breakdown"]["duplicate_noop_cost_seconds"], 0.0)
        self.assertTrue(audit["summary"]["operation_trace_complete"])
        self.assertTrue(audit["summary"]["has_redundant_operation_loop"])
        self.assertTrue(audit["summary"]["has_duplicate_resource_operation"])
        self.assertTrue(audit["summary"]["has_noop_retry_loop"])
        self.assertTrue(audit["summary"]["has_repeated_full_reconcile"])
        self.assertTrue(audit["summary"]["has_cardinality_anomaly"])

    def test_legitimate_transient_retry_is_allowed_but_counted(self) -> None:
        runner_report = {
            "startup_operation_trace": [
                {
                    "operation_id": "op-retry-ok",
                    "operation_type": "resource_create",
                    "owner_module": "startup",
                    "stage": "satellites-01",
                    "batch": "batch-01",
                    "target_resource": "sat-b",
                    "target_resource_type": "container",
                    "intent": "materialize satellite container",
                    "reason": "transient docker conflict recovered",
                    "idempotency_key": "container:sat-b",
                    "expected_cardinality": 2,
                    "actual_attempt_count": 2,
                    "duplicate_count": 0,
                    "retry_count": 1,
                    "duration_seconds": 4.0,
                    "state_changed": True,
                    "result": "created_after_retry",
                    "error": "",
                }
            ]
        }

        audit = evaluate_startup_operation_audit(runner_report=runner_report)

        self.assertTrue(audit["operation_trace_complete"])
        self.assertNotIn("STARTUP_NOOP_RETRY_LOOP", audit["outcome_labels"])
        self.assertNotIn("STARTUP_OPERATION_CARDINALITY_ANOMALY", audit["outcome_labels"])
        self.assertEqual(audit["operations"][0]["retry_count"], 1)

    def test_missing_operation_trace_is_reported_incomplete(self) -> None:
        audit = evaluate_startup_operation_audit(
            runner_report={
                "startup_strategy": {
                    "compose_stage_results": [
                        {
                            "stage_id": "satellites-01",
                            "stage_type": "satellite_batch",
                            "service_names": ["sat-a"],
                            "service_count": 1,
                            "timed_out": False,
                            "wall_seconds": 2.0,
                        }
                    ]
                }
            }
        )

        self.assertFalse(audit["startup_cost_understood"])
        self.assertIn("STARTUP_OPERATION_TRACE_INCOMPLETE", audit["outcome_labels"])
        self.assertFalse(audit["summary"]["operation_trace_complete"])
        self.assertTrue(
            audit["summary"]["operation_trace_synthesized_from_stage_results"]
        )

    def test_first_failure_summary_preserves_startup_timeout(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            summary = _first_failure_summary(
                profile=profile,
                run_dir=run_dir,
                runner_report={
                    "failure_reason": "docker_compose_up_timeout",
                    "runtime_blocker": {
                        "classified_blocker": "RESOURCE_DEADLOCK_STARTUP_ABORT",
                        "kill_reason": "resource_exhaustion_or_deadlock",
                        "compose_up_stage": "satellites-26",
                    },
                },
                startup_contract={
                    "outcome_labels": ["RESOURCE_DEADLOCK_STARTUP_ABORT"],
                    "timeout_classification": "resource_deadlock_startup_abort",
                    "completed_batches": 80,
                    "total_expected_batches": 81,
                    "progress_ratio": 0.987654,
                    "last_compose_up_stage": "satellites-26",
                },
                returncode=0,
            )

        self.assertEqual(summary["first_failure_label"], "RESOURCE_DEADLOCK_STARTUP_ABORT")
        self.assertFalse(summary["runtime_or_bpf_reached"])
        self.assertEqual(summary["startup_materialization"]["completed_batches"], 80)

    def test_first_failure_summary_preserves_completed_epoch_count_from_runtime_report(
        self,
    ) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            (run_dir / "real_runtime_report.json").write_text(
                __import__("json").dumps(
                    {
                        "failure_context": {
                            "current_epoch_context": {
                                "runtime_epochs_completed": 3,
                                "epoch_id": "runtimeepoch-failed",
                            }
                        },
                        "timing_summary": {"runtime_epoch_count": 3},
                        "iterations": [{"epoch_id": "a"}, {"epoch_id": "b"}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            summary = _first_failure_summary(
                profile=profile,
                run_dir=run_dir,
                runner_report={
                    "failure_reason": "bpf_owned_runtime_readiness_failed",
                },
                startup_contract={},
                returncode=1,
            )

        self.assertTrue(summary["runtime_or_bpf_reached"])
        self.assertEqual(summary["runtime_epochs_completed"], 3)

    def test_first_failure_summary_reports_bpf_generation_capacity_blocker(
        self,
    ) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "failure_context": {
                            "current_epoch_context": {
                                "runtime_epochs_completed": 1,
                                "epoch_id": "runtimeepoch-capacity",
                            },
                            "exception": {
                                "message": (
                                    "bpf_owned_runtime_readiness_failed:"
                                    "reason=operational_clean_bpf_map_reload_failed"
                                ),
                                "skyseed_details": {
                                    "bpf_owned_readiness": {
                                        "reason": "operational_clean_bpf_map_reload_failed",
                                        "blocked_stage": "bpf_hotpath_map_reload",
                                        "prepare_steps": {
                                            "real_backend_prepare": {
                                                "reason": (
                                                    "operational_clean_bpf_map_reload_failed"
                                                ),
                                                "blocked_stage": "bpf_hotpath_map_reload",
                                                "delta_attach_count": 0,
                                                "full_attach_count": 0,
                                                "reused_attach_count": 98,
                                                "outcome_labels": [
                                                    "RUNTIME_PRELOAD_SCHEDULED",
                                                ],
                                                "steps": {
                                                    "preactivation_preload": {
                                                        "ok": True,
                                                        "map_write_update_count": 180,
                                                        "outcome_labels": [
                                                            "RUNTIME_MAP_DIFF_UPDATE_USED",
                                                            "RUNTIME_NOOP_MAP_WRITES_SKIPPED",
                                                        ],
                                                    },
                                                    "map_load": {
                                                        "ok": False,
                                                        "failed_map": (
                                                            "ingress_classifier_map"
                                                        ),
                                                        "failed_operation": "update",
                                                        "failed_generation": 1,
                                                        "map_write_update_count": 0,
                                                        "active_generation_update_count": 0,
                                                        "generation_capacity_contracts": {
                                                            "ingress_classifier_map": {
                                                                "label": (
                                                                    "BPF_MAP_GENERATION_CAPACITY_BLOCKER"
                                                                ),
                                                                "ok": False,
                                                                "active_generation": 0,
                                                                "target_generation": 1,
                                                                "live_max_entries": 348,
                                                                "required_capacity": 376,
                                                                "retained_entry_count_after_gc": 240,
                                                                "target_expected_entries": 120,
                                                            }
                                                        },
                                                    },
                                                },
                                            }
                                        },
                                    }
                                },
                            },
                        },
                        "timing_summary": {"runtime_epoch_count": 1},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = _first_failure_summary(
                profile=profile,
                run_dir=run_dir,
                runner_report={},
                startup_contract={},
                returncode=1,
            )

        self.assertEqual(
            summary["first_failure_label"],
            "BPF_MAP_GENERATION_CAPACITY_BLOCKER",
        )
        self.assertEqual(summary["blocked_stage"], "bpf_hotpath_map_reload")
        self.assertEqual(summary["failed_map"], "ingress_classifier_map")
        artifact_failure = summary["runtime_artifact_failure"]
        self.assertTrue(artifact_failure["preactivation_preload_scheduled"])
        self.assertEqual(artifact_failure["preactivation_preload_update_count"], 180)
        self.assertIn(
            "RUNTIME_MAP_DIFF_UPDATE_USED",
            artifact_failure["outcome_labels"],
        )
        self.assertEqual(
            artifact_failure["capacity_blockers"][0]["required_capacity"],
            376,
        )

    def test_first_failure_summary_reports_parameter_delta_attach_blocker(
        self,
    ) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "failure_context": {
                            "current_epoch_context": {
                                "classification": "parameter_epoch",
                                "runtime_epochs_completed": 4,
                                "parameter_epochs_completed": 0,
                                "slice_id": "starlink-shell-slice-0011",
                                "epoch_id": "parameterepoch-1",
                            },
                            "exception": {
                                "message": (
                                    "bpf_owned_runtime_readiness_failed:"
                                    "reason=tc_bpf_mpls_delta_attach_failed"
                                ),
                                "skyseed_details": {
                                    "bpf_owned_readiness": {
                                        "reason": "tc_bpf_mpls_delta_attach_failed",
                                        "blocked_stage": (
                                            "bpf_owned_runtime_readiness"
                                        ),
                                        "packet_path_diagnostics": {
                                            "packet_miss_delta": 0,
                                            "packet_drop_delta": 0,
                                        },
                                        "prepare_steps": {
                                            "real_backend_prepare": {
                                                "reason": (
                                                    "tc_bpf_mpls_delta_attach_failed"
                                                ),
                                                "delta_attach_count": 42,
                                                "full_attach_count": 0,
                                                "reused_attach_count": 98,
                                            }
                                        },
                                    }
                                },
                            },
                        },
                        "timing_summary": {"runtime_epoch_count": 4},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = _first_failure_summary(
                profile=profile,
                run_dir=run_dir,
                runner_report={},
                startup_contract={},
                returncode=1,
            )

        self.assertEqual(
            summary["first_failure_label"],
            "PARAMETER_UPDATE_TRIGGERED_DELTA_ATTACH_BLOCKER",
        )
        self.assertEqual(summary["runtime_epochs_completed"], 4)
        artifact_failure = summary["runtime_artifact_failure"]
        self.assertTrue(artifact_failure["parameter_delta_attach_blocker"])
        self.assertEqual(artifact_failure["delta_attach_count"], 42)
        self.assertIn(
            "PARAMETER_UPDATE_TRIGGERED_DELTA_ATTACH_BLOCKER",
            artifact_failure["outcome_labels"],
        )

    def test_first_failure_summary_reports_parameter_hidden_recovery_blocker(
        self,
    ) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "failure_context": {
                            "current_epoch_context": {
                                "classification": "parameter_epoch",
                                "runtime_epochs_completed": 4,
                                "parameter_epochs_completed": 0,
                            },
                            "exception": {
                                "message": (
                                    "PARAMETER_UPDATE_HIDDEN_BPF_RECOVERY_BLOCKER:"
                                    "reason=targeted_parameter_validation_failed"
                                ),
                                "skyseed_details": {},
                            },
                        },
                        "timing_summary": {"runtime_epoch_count": 4},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = _first_failure_summary(
                profile=profile,
                run_dir=run_dir,
                runner_report={},
                startup_contract={},
                returncode=1,
            )

        self.assertEqual(
            summary["first_failure_label"],
            "PARAMETER_UPDATE_HIDDEN_BPF_RECOVERY_BLOCKER",
        )
        artifact_failure = summary["runtime_artifact_failure"]
        self.assertTrue(artifact_failure["parameter_hidden_bpf_recovery_blocker"])
        self.assertIn(
            "PARAMETER_UPDATE_HIDDEN_BPF_RECOVERY_BLOCKER",
            artifact_failure["outcome_labels"],
        )

    def test_profile_run_summary_refreshes_derived_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            (run_dir / "profile_run_summary.json").write_text(
                json.dumps(
                    {
                        "schema_version": "skyseed-validation-profile-summary-v1",
                        "outcome_labels": [
                            "OLD_LABEL",
                            "NORMAL_PATH_OWNED_SCOPE_ONLY",
                            "NORMAL_PATH_NO_HIDDEN_REPAIR",
                        ],
                        "runtime_preload_diff_contract_audit": {
                            "outcome_labels": [],
                            "preactivation_preload_epoch_count": 0,
                        },
                        "normal_path_artifact_contract": {
                            "outcome_labels": [],
                            "normal_path_owned_scope_only": False,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "runtime_preload_diff_contract_audit.json").write_text(
                json.dumps(
                    {
                        "outcome_labels": [
                            "RUNTIME_PRELOAD_SCHEDULED",
                            "RUNTIME_MAP_DIFF_UPDATE_USED",
                        ],
                        "preactivation_preload_epoch_count": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "static_inventory_precreated_connection_audit.json").write_text(
                json.dumps(
                    {
                        "outcome_labels": [
                            "STATIC_INVENTORY_PRECREATED_CONNECTION_AUDIT_ADDED",
                            "GW_SLOT_DERIVATION_CONFIRMED",
                        ],
                        "slot_derivation_audit": {
                            "gw_max_simultaneous_slots": {
                                "derivation_label": "GW_SLOT_DERIVATION_CONFIRMED",
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "normal_failure_cleanup_report.json").write_text(
                json.dumps(
                    {
                        "outcome_label": "CLEANUP_BOUNDARY_PASS",
                        "cleanup_mode": "normal_failure_cleanup",
                        "detector_source": "detect_cleanup_residue",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "normal_path_artifact_contract.json").write_text(
                json.dumps(
                    {
                        "outcome_labels": [
                            "NORMAL_PATH_ARTIFACT_CONTRACT_ADDED",
                            "OWNED_RESOURCE_INDEX_MISSING_BLOCKER",
                        ],
                        "normal_path_owned_scope_only": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = refresh_profile_run_summary_derived_artifacts(run_dir=run_dir)
            written = json.loads(
                (run_dir / "profile_run_summary.json").read_text(encoding="utf-8")
            )

        self.assertEqual(
            summary["runtime_preload_diff_contract_audit"][
                "preactivation_preload_epoch_count"
            ],
            1,
        )
        self.assertIn("RUNTIME_PRELOAD_SCHEDULED", summary["outcome_labels"])
        self.assertIn("GW_SLOT_DERIVATION_CONFIRMED", summary["outcome_labels"])
        self.assertIn("OWNED_RESOURCE_INDEX_MISSING_BLOCKER", summary["outcome_labels"])
        self.assertNotIn("NORMAL_PATH_OWNED_SCOPE_ONLY", summary["outcome_labels"])
        self.assertNotIn("NORMAL_PATH_NO_HIDDEN_REPAIR", summary["outcome_labels"])
        self.assertIn("GLOBAL_SCAN_DIAGNOSTIC_ONLY", summary["outcome_labels"])
        self.assertIn("NORMAL_FAILURE_CLEANUP_MODE", summary["outcome_labels"])
        self.assertTrue(
            summary["normal_failure_cleanup"]["global_scan_diagnostic_only"]
        )
        self.assertEqual(
            written["derived_artifact_refresh"]["artifact_count"],
            4,
        )

    def test_normal_path_artifact_contract_aggregates_explicit_plan_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            (run_dir / "run_manifest.json").write_text(
                json.dumps({"run_id": "run-a", "generated_artifacts": {}}) + "\n",
                encoding="utf-8",
            )
            (run_dir / "prepared_update_bundle.json").write_text(
                json.dumps(
                    {
                        "selected_epoch_queue": [{"epoch_id": "runtimeepoch-1"}],
                        "current_execution_queue": [{"epoch_id": "runtimeepoch-1"}],
                        "lookahead_preload_queue": [{"epoch_id": "parameterepoch-1"}],
                        "continuous_runtime_execution": {
                            "epoch_execution_queue": [{"epoch_id": "runtimeepoch-1"}],
                            "run_window_resource_coverage_plan": {
                                "schema_version": "skyseed-run-window-resource-coverage-plan-v1",
                                "planned_run_horizon_count": 1,
                                "lookahead_horizon_count": 1,
                                "required_resource_union_count": 1,
                                "materialized_resource_count": 1,
                                "unresolved_count": 0,
                                "newly_required_but_not_prematerialized_count": 0,
                            },
                            "prepared_resource_scope_by_slot": {
                                "slot-a": {
                                    "owned_resources": ["netns:a:if0"],
                                },
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "runtime_update_plan_summary.json").write_text(
                json.dumps(
                    [
                        {
                            "slice_id": "slice-1",
                            "runtime_update_action_count": 1,
                        }
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "horizon_execution_reconciled_summary.json").write_text(
                json.dumps(
                    {
                        "horizon_selected_by": "EventTimeline",
                        "selected_event_count": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "horizon_lifecycle_queue_contract.json").write_text(
                json.dumps(
                    {
                        "current_execution_event_count": 1,
                        "lookahead_preload_event_count": 1,
                        "preload_required_event_count": 1,
                        "s4_precondition_low_cost_proof_complete": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "runtime_preload_diff_contract_audit.json").write_text(
                json.dumps(
                    {
                        "preactivation_preload_epoch_count": 1,
                        "map_diff_update_epoch_count": 1,
                        "outcome_labels": ["RUNTIME_PRELOAD_SCHEDULED"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "normal_failure_cleanup_report.json").write_text(
                json.dumps(
                    {
                        "outcome_label": "CLEANUP_BOUNDARY_PASS",
                        "cleanup_mode": "normal_failure_cleanup",
                        "detector_source": "detect_cleanup_residue",
                        "cleanup_scope_source": "prepared_owned_resource_index",
                        "bounded_owned_scope": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            contract = evaluate_normal_path_artifact_contract(run_dir=run_dir)
            written = json.loads(
                (run_dir / "normal_path_artifact_contract.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertTrue(contract["run_manifest_present"])
        self.assertTrue(contract["prepared_owned_resource_index_present"])
        self.assertEqual(
            contract["prepared_owned_resource_index_sources"],
            ["continuous_runtime_execution.prepared_resource_scope_by_slot"],
        )
        self.assertTrue(contract["prepared_execution_queue_present"])
        self.assertEqual(contract["prepared_current_execution_queue_count"], 1)
        self.assertEqual(contract["prepared_lookahead_preload_queue_count"], 1)
        self.assertTrue(contract["executed_epoch_queue_present"])
        self.assertTrue(contract["run_window_resource_coverage_present"])
        self.assertTrue(contract["run_window_resource_coverage_passed"])
        self.assertTrue(contract["explicit_runtime_update_plan_present"])
        self.assertTrue(contract["explicit_event_timeline_plan_present"])
        self.assertTrue(contract["explicit_horizon_lifecycle_queue_present"])
        self.assertEqual(
            contract["horizon_lifecycle_queue_contract"][
                "lookahead_preload_event_count"
            ],
            1,
        )
        self.assertTrue(contract["explicit_bpf_preload_diff_plan_present"])
        self.assertTrue(contract["cleanup_owned_scope_evidence_present"])
        self.assertEqual(contract["cleanup_scope_source"], "prepared_owned_resource_index")
        self.assertFalse(contract["cleanup_full_scope_repair_used"])
        self.assertFalse(contract["cleanup_full_scope_delete_used"])
        self.assertFalse(contract["cleanup_full_scope_reconcile_used"])
        self.assertTrue(contract["normal_path_owned_scope_only"])
        self.assertTrue(
            contract[
                "normal_path_fail_fast_required_when_manifest_or_owned_index_missing"
            ]
        )
        self.assertTrue(contract["normal_path_hidden_full_scope_repair_forbidden"])
        self.assertIn(
            "HORIZON_LIFECYCLE_QUEUE_CONTRACT_PRESENT",
            contract["outcome_labels"],
        )
        self.assertIn("NORMAL_PATH_OWNED_SCOPE_ONLY", contract["outcome_labels"])
        self.assertIn("NORMAL_PATH_NO_HIDDEN_REPAIR", contract["outcome_labels"])
        self.assertIn("GLOBAL_SCAN_DIAGNOSTIC_ONLY", contract["outcome_labels"])
        self.assertEqual(written["runtime_update_plan_entry_count"], 1)

    def test_normal_path_artifact_contract_does_not_treat_queue_as_owned_index(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            (run_dir / "run_manifest.json").write_text(
                json.dumps({"run_id": "run-a", "generated_artifacts": {}}) + "\n",
                encoding="utf-8",
            )
            (run_dir / "prepared_update_bundle.json").write_text(
                json.dumps(
                    {
                        "selected_epoch_queue": [{"epoch_id": "runtimeepoch-1"}],
                        "current_execution_queue": [{"epoch_id": "runtimeepoch-1"}],
                        "continuous_runtime_execution": {
                            "epoch_execution_queue": [{"epoch_id": "runtimeepoch-1"}],
                            "prepared_owned_resource_index": {
                                "schema_version": "skyseed-prepared-owned-resource-index-v1",
                                "source": "pre_epoch_host_prewarm_scope",
                                "slot_count": 0,
                                "resource_count": 0,
                                "resource_scope_by_slot": {},
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "runtime_update_plan_summary.json").write_text(
                json.dumps([{"slice_id": "slice-1"}]) + "\n",
                encoding="utf-8",
            )
            (run_dir / "horizon_execution_reconciled_summary.json").write_text(
                json.dumps(
                    {
                        "horizon_selected_by": "EventTimeline",
                        "selected_event_count": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "runtime_preload_diff_contract_audit.json").write_text(
                json.dumps(
                    {
                        "preactivation_preload_epoch_count": 1,
                        "map_diff_update_epoch_count": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            contract = evaluate_normal_path_artifact_contract(run_dir=run_dir)

        self.assertTrue(contract["prepared_execution_queue_present"])
        self.assertFalse(contract["prepared_owned_resource_index_present"])
        self.assertFalse(contract["normal_path_owned_scope_only"])
        self.assertFalse(contract["normal_path_no_hidden_repair"])
        self.assertEqual(contract["prepared_owned_resource_index_sources"], [])
        self.assertIn("OWNED_RESOURCE_INDEX_MISSING_BLOCKER", contract["outcome_labels"])
        self.assertNotIn("NORMAL_PATH_OWNED_SCOPE_ONLY", contract["outcome_labels"])

    def test_normal_path_artifact_contract_accepts_prepared_queue_when_execution_empty(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            (run_dir / "run_manifest.json").write_text(
                json.dumps({"run_id": "run-a", "generated_artifacts": {}}) + "\n",
                encoding="utf-8",
            )
            (run_dir / "prepared_update_bundle.json").write_text(
                json.dumps(
                    {
                        "selected_epoch_queue": [
                            {"epoch_id": "runtimeepoch-1"},
                            {"epoch_id": "parameterepoch-1"},
                        ],
                        "current_execution_queue": [{"epoch_id": "runtimeepoch-1"}],
                        "lookahead_preload_queue": [{"epoch_id": "parameterepoch-1"}],
                        "continuous_runtime_execution": {
                            "epoch_execution_queue": [],
                            "run_window_resource_coverage_plan": {
                                "schema_version": "skyseed-run-window-resource-coverage-plan-v1",
                                "planned_run_horizon_count": 1,
                                "lookahead_horizon_count": 1,
                                "required_resource_union_count": 1,
                                "materialized_resource_count": 1,
                                "unresolved_count": 0,
                                "newly_required_but_not_prematerialized_count": 0,
                            },
                            "prepared_owned_resource_index": {
                                "schema_version": "skyseed-prepared-owned-resource-index-v1",
                                "source": "pre_epoch_host_prewarm_scope",
                                "slot_count": 1,
                                "resource_count": 1,
                                "resource_scope_by_slot": {"slot-a": ["resource-a"]},
                            },
                            "prepared_resource_scope_by_slot": {
                                "slot-a": ["resource-a"],
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "runtime_update_plan_summary.json").write_text(
                json.dumps([{"slice_id": "slice-1"}]) + "\n",
                encoding="utf-8",
            )
            (run_dir / "horizon_execution_reconciled_summary.json").write_text(
                json.dumps(
                    {
                        "horizon_selected_by": "EventTimeline",
                        "selected_event_count": 2,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "horizon_lifecycle_queue_contract.json").write_text(
                json.dumps(
                    {
                        "current_execution_event_count": 1,
                        "lookahead_preload_event_count": 1,
                        "preload_required_event_count": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "runtime_preload_diff_contract_audit.json").write_text(
                json.dumps(
                    {
                        "preactivation_preload_epoch_count": 0,
                        "preactivation_preload_attempt_count": 1,
                        "preactivation_preload_failed_count": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "normal_failure_cleanup_report.json").write_text(
                json.dumps(
                    {
                        "outcome_label": "CLEANUP_BOUNDARY_PASS",
                        "cleanup_mode": "normal_failure_cleanup",
                        "detector_source": "detect_cleanup_residue",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            contract = evaluate_normal_path_artifact_contract(run_dir=run_dir)

        self.assertTrue(contract["prepared_execution_queue_present"])
        self.assertEqual(contract["prepared_current_execution_queue_count"], 1)
        self.assertEqual(contract["prepared_lookahead_preload_queue_count"], 1)
        self.assertFalse(contract["executed_epoch_queue_present"])
        self.assertTrue(contract["run_window_resource_coverage_passed"])
        self.assertFalse(contract["cleanup_owned_scope_evidence_present"])
        self.assertFalse(contract["normal_path_owned_scope_only"])
        self.assertNotIn("OWNED_RESOURCE_INDEX_MISSING_BLOCKER", contract["outcome_labels"])
        self.assertIn(
            "CLEANUP_OWNED_SCOPE_EVIDENCE_MISSING_BLOCKER",
            contract["outcome_labels"],
        )

    def test_cleanup_report_normalization_labels_legacy_cleanup_artifact(self) -> None:
        normalized = normalize_cleanup_report_contract(
            {
                "schema_version": "skyseed-automated-cleanup-contract-v1",
                "outcome_label": "CLEANUP_BOUNDARY_PASS",
                "cleanup_mode": "normal_failure_cleanup",
                "detector_source": "detect_cleanup_residue",
                "external_cleanup_success": True,
            }
        )

        self.assertTrue(normalized["global_scan_diagnostic_only"])
        self.assertIn("CLEANUP_BOUNDARY_PASS", normalized["outcome_labels"])
        self.assertIn("GLOBAL_SCAN_DIAGNOSTIC_ONLY", normalized["outcome_labels"])
        self.assertIn("NORMAL_FAILURE_CLEANUP_MODE", normalized["outcome_labels"])

    def test_cleanup_evidence_labels_pre_cleanup_and_final_cleanup_separately(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            (run_dir / "post_run_preflight.json").write_text(
                '{"clean_after_run": false}\n',
                encoding="utf-8",
            )
            contract = _cleanup_evidence_contract(
                run_dir=run_dir,
                pre_cleanup_residue={
                    "counts": {"compose_containers": 1, "compose_networks": 0}
                },
                normal_cleanup={"external_cleanup_success": True},
                final_residue={
                    "counts": {
                        "bpf_pins": 0,
                        "compose_containers": 0,
                        "compose_networks": 0,
                        "direct_backend_host_interfaces": 0,
                        "docker_network_pool_overlaps": 0,
                        "runtime_locks": 0,
                    }
                },
            )

        self.assertEqual(
            contract["pre_cleanup"]["phase_label"],
            "pre_normal_failure_cleanup",
        )
        self.assertFalse(
            contract["pre_cleanup"]["post_run_preflight_clean_after_run"]
        )
        self.assertEqual(
            contract["final_cleanup"]["evidence_source"],
            "normal_failure_cleanup_report_and_final_external_residue",
        )
        self.assertTrue(contract["final_cleanup"]["external_cleanup_success"])
        self.assertEqual(
            contract["final_cleanup"]["external_residue"]["counts"],
            {
                "bpf_pins": 0,
                "compose_containers": 0,
                "compose_networks": 0,
                "direct_backend_host_interfaces": 0,
                "docker_network_pool_overlaps": 0,
                "runtime_locks": 0,
            },
        )

    def test_persistent_artifact_contract_uses_non_tmp_run_root(self) -> None:
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            (run_dir / "runtime_progress.json").write_text("{}\n", encoding="utf-8")
            contract = _persistent_artifact_contract(run_dir)

        self.assertFalse(contract["run_root_is_tmp_only"])
        self.assertEqual(contract["owner_module"], "satellite/apps/run_skyseed_validation_profile.py")
        self.assertTrue(
            any(
                artifact["name"] == "runtime_progress.json" and artifact["exists"]
                for artifact in contract["artifacts"]
            )
        )

    def test_per_epoch_timing_summary_is_emitted(self) -> None:
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            epoch = {
                "epoch_id": "runtimeepoch-1",
                "epoch_type": "runtime_epoch",
                "event_timeline_type": "runtime_transaction",
                "outcome": {"success": True, "failure_reason": None},
                "timing": {
                    "event_wait_seconds": 10.0,
                    "host_operation_apply_wall_seconds": 2.0,
                    "node_local_apply_wall_seconds": 3.0,
                    "bpf_prepare_after_incremental_cleanup_wall_seconds": 1.5,
                    "bpf_map_update_count": 4,
                    "bpf_map_write_seconds": 0.7,
                    "active_generation_flip_seconds": 0.2,
                    "parameter_qdisc_required_update_count": 0,
                    "parameter_qdisc_apply_seconds": 0.0,
                    "targeted_validation_seconds": 0.4,
                    "post_transition_connectivity_probe_wall_seconds": 0.1,
                    "post_apply_overhead_seconds": 0.6,
                    "network_update_seconds": 5.0,
                    "unknown_blocking_seconds": 0.0,
                },
                "node_local_execution": {
                    "backend": {
                        "bpf_owned_readiness": {
                            "prepare_steps": {
                                "load": {
                                    "active_generation": 7,
                                    "total_update_count": 4,
                                    "map_write_wall_seconds": 0.7,
                                    "generation_gc_cleanup": {
                                        "maps": {
                                            "ingress_classifier_map": {
                                                "target_generation": 7,
                                                "old_generation_delete_update_count": 2,
                                                "target_generation_delete_update_count": 1,
                                            }
                                        }
                                    },
                                }
                            }
                        }
                    }
                },
            }
            (run_dir / "epoch_execution_details.jsonl").write_text(
                __import__("json").dumps(epoch) + "\n",
                encoding="utf-8",
            )

            summary = write_runtime_update_timing_evidence(
                run_dir=run_dir,
                runner_report={"event_timeline_execution_success": True},
                startup_contract={"compose_up_wall_seconds": 1.0},
            )

            self.assertTrue((run_dir / "per_epoch_update_timing.jsonl").exists())
            self.assertTrue((run_dir / "aggregate_update_timing_summary.json").exists())
            self.assertEqual(summary["record_count"], 1)
            self.assertIn("RUNTIME_UPDATE_TIMING_EVIDENCE_ADDED", summary["outcome_labels"])

    def test_event_wait_is_separated_from_update_work(self) -> None:
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            epoch = {
                "epoch_id": "runtimeepoch-wait",
                "epoch_type": "runtime_epoch",
                "event_timeline_type": "runtime_transaction",
                "outcome": {"success": True},
                "timing": {
                    "event_wait_seconds": 30.0,
                    "host_operation_apply_wall_seconds": 1.0,
                    "node_local_apply_wall_seconds": 1.0,
                    "network_update_seconds": 2.0,
                    "post_apply_overhead_seconds": 0.0,
                    "unknown_blocking_seconds": 0.0,
                },
            }
            (run_dir / "epoch_execution_details.jsonl").write_text(
                __import__("json").dumps(epoch) + "\n",
                encoding="utf-8",
            )

            summary = write_runtime_update_timing_evidence(run_dir=run_dir)

            categories = summary["timing_categories"]
            self.assertEqual(categories["event_wait_seconds"], 30.0)
            self.assertEqual(categories["real_runtime_update_work_seconds"], 2.0)
            self.assertTrue(summary["event_wait_excluded_from_update_work"])

    def test_missing_timing_fields_are_reported_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            epoch = {
                "epoch_id": "runtimeepoch-missing",
                "epoch_type": "runtime_epoch",
                "event_timeline_type": "runtime_transaction",
                "outcome": {"success": True},
                "timing": {
                    "event_wait_seconds": 0.0,
                    "node_local_apply_wall_seconds": 0.0,
                    "network_update_seconds": 1.0,
                    "unknown_blocking_seconds": 0.0,
                },
                "node_local_execution": {
                    "backend": {
                        "bpf_owned_readiness": {
                            "prepare_steps": {
                                "load": {
                                    "active_generation": 1,
                                    "total_update_count": 1,
                                    "map_write_wall_seconds": 0.1,
                                }
                            }
                        }
                    }
                },
            }
            (run_dir / "epoch_execution_details.jsonl").write_text(
                __import__("json").dumps(epoch) + "\n",
                encoding="utf-8",
            )

            summary = write_runtime_update_timing_evidence(run_dir=run_dir)

            self.assertIn("host_operation_apply_wall_seconds", summary["missing_timing_fields"])
            self.assertIn("bpf_generation_gc_seconds", summary["missing_timing_fields"])
            self.assertIn("TIMING_FIELDS_MISSING_IN_EXISTING_ARTIFACTS", summary["outcome_labels"])

    def test_soft_budget_warning_label_does_not_create_first_failure(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            summary = _first_failure_summary(
                profile=profile,
                run_dir=Path(tmp),
                runner_report={
                    "runtime_blocker": {
                        "classified_blocker": "",
                    }
                },
                startup_contract={
                    "outcome_labels": [
                        "STARTUP_SOFT_BUDGET_EXCEEDED_WARNING",
                        "STARTUP_MATERIALIZATION_COST_ANOMALY",
                        "SLOW_BUT_PROGRESSING_CONTINUE",
                    ],
                },
                returncode=0,
            )

        self.assertIsNone(summary["first_failure_label"])

    def test_startup_cost_warning_reason_is_not_first_failure_reason(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            summary = _first_failure_summary(
                profile=profile,
                run_dir=Path(tmp),
                runner_report={
                    "failure_reason": "cost_sanity_failed:STARTUP_COST_ANOMALY",
                    "aggregate_runtime_succeeded": False,
                    "real_apply_success": True,
                    "ping_success": True,
                    "event_timeline_execution_success": True,
                    "rollback_success": True,
                    "runtime_blocker": {
                        "classified_blocker": "STARTUP_COST_ANOMALY",
                    },
                },
                startup_contract={
                    "outcome_labels": [
                        "STARTUP_SOFT_BUDGET_EXCEEDED_WARNING",
                        "STARTUP_MATERIALIZATION_COST_ANOMALY",
                        "SLOW_BUT_PROGRESSING_CONTINUE",
                    ],
                },
                returncode=0,
            )

        self.assertIsNone(summary["first_failure_label"])
        self.assertIsNone(summary["failure_reason"])
        self.assertEqual(
            summary["startup_cost_warning_reason"],
            "cost_sanity_failed:STARTUP_COST_ANOMALY",
        )

    def test_static_inventory_precreated_connection_audit_reports_xp120_visibility(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            xp_slot = {
                "slot_name": "p003p004-stl-940120-stl-940178-cross-slot0",
                "slot_type": "cross_plane_isl",
                "network_name": "p003p004-stl-940120-stl-940178-cross-slot0",
                "docker_network_name": "p003p004-stl-940120-stl-940178-cross-slot0",
                "subnet": "10.92.120.0/29",
                "candidate_pairs": [
                    {
                        "satellite_a_id": "stl-940120",
                        "satellite_b_id": "stl-940178",
                    }
                ],
                "endpoint_a_template": {"interface_name": "xp120a"},
                "endpoint_b_template": {"interface_name": "xp120b"},
            }
            (run_dir / "topology.json").write_text(
                json.dumps(
                    {
                        "slot_inventory": {
                            "ut_access": [{"slot_name": "ut-current"}, {"slot_name": "ut-next"}],
                            "gateway_access": [
                                {"slot_name": "gw-0"},
                                {"slot_name": "gw-1"},
                            ],
                            "same_plane_isl": [{"slot_name": "fixed-shell-0001"}],
                            "cross_plane_isl": [xp_slot],
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "link_state.json").write_text(
                json.dumps(
                    {
                        "slots": [
                            {
                                "slot_name": "fixed-shell-0001",
                                "slot_type": "same_plane_isl",
                                "occupied": False,
                                "dynamic_endpoints": [],
                                "preconnected_endpoints": [],
                            },
                            {
                                "slot_name": xp_slot["slot_name"],
                                "slot_type": "cross_plane_isl",
                                "occupied": False,
                                "dynamic_endpoints": [],
                                "preconnected_endpoints": [],
                            },
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "profile_catalog_gate.json").write_text(
                json.dumps(
                    {
                        "selectors": {
                            "ut": {"selected_ids": ["ut-a"]},
                            "gateway": {"selected_ids": ["gw-a"]},
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "prepared_update_bundle.json").write_text(
                json.dumps(
                    {
                        "continuous_runtime_execution": {
                            "epoch_execution_queue": [
                                {
                                    "node_local_execution": {
                                        "backend": {
                                            "bpf_owned_readiness": {
                                                "prepare_steps": {
                                                    "real_backend_prepare": {
                                                        "attach_plan": {
                                                            "netns:stl-940120:xp120a": ["ingress"],
                                                            "netns:stl-940178:xp120b": ["ingress"],
                                                        },
                                                        "resident_attach_coverage": {
                                                            "compile_capacity_interfaces": [
                                                                "netns:stl-940120:xp120a",
                                                                "netns:stl-940178:xp120b",
                                                            ],
                                                            "active_materialized_interfaces": [],
                                                            "future_materialized_attach_eligible_interfaces": [],
                                                            "future_non_materialized_capacity_only_interfaces": [],
                                                            "future_materialized_attach_eligible_interface_count": 0,
                                                            "future_non_materialized_capacity_only_interface_count": 0,
                                                        },
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            ]
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "bpf_mpls_offline_validation.json").write_text(
                json.dumps(
                    {
                        "aggregate": {"unsafe_conflict_count": 0},
                        "path_cardinality_audit": {
                            "aggregate": {
                                "linux_gz_mpls_lfib_fallback_count": 0,
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "lifecycle_aware_horizon_contract.json").write_text(
                json.dumps(
                    {
                        "horizon_selected_by": "EventTimeline",
                        "whole_horizon_completed": True,
                        "ready_for_next_horizon": True,
                        "classification_counts": {"map_only_update": 1},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "slot_capacity_audit.json").write_text(
                json.dumps(
                    {
                        "calculation_basis": {
                            "handover_modeled": True,
                            "policy_margins": {
                                "gateway_slot_margin": 1,
                                "ut_slot_margin": 1,
                            },
                            "ut_required_by_id": {"ut-a": 2},
                            "gateway_required_by_id": {"gw-a": 2},
                            "active_link_max_concurrent_by_type": {
                                "gw_access": 1,
                            },
                            "active_link_distinct_identity_count_by_type": {
                                "cross_plane_isl": 1,
                            },
                        },
                        "prepared_slots_by_type": {
                            "ut_access": 2,
                            "gw_access": 2,
                            "same_plane_isl": 1,
                            "cross_plane_isl": 1,
                        },
                        "required_max_concurrent_slots_by_type": {
                            "ut_access": 2,
                            "gw_access": 2,
                            "same_plane_isl": 1,
                            "cross_plane_isl": 1,
                        },
                        "notes": {
                            "ut_access": "current+next",
                            "gw_access": "max simultaneous plus margin",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            audit = evaluate_static_inventory_precreated_connection_audit(
                run_dir=run_dir,
            )

        self.assertEqual(audit["categories"]["ut_current_next_slots"]["slot_count"], 2)
        self.assertEqual(audit["categories"]["ut_current_next_slots"]["slots_per_selected_ut"], 2)
        self.assertEqual(audit["categories"]["gw_max_simultaneous_slots"]["slot_count"], 2)
        self.assertEqual(
            audit["categories"]["gw_max_simultaneous_slots"]["slots_per_selected_gateway"],
            2,
        )
        self.assertEqual(audit["categories"]["in_plane_fixed_slots"]["slot_count"], 1)
        self.assertEqual(audit["categories"]["cross_plane_bounded_slot_pool"]["slot_count"], 1)
        self.assertTrue(audit["summary"]["xp120_visibility_passed"])
        xp120a = audit["xp120_exact_inactive_visibility_proof"][0]
        self.assertTrue(xp120a["slot_inventory"]["preallocated_static_inventory_slot"])
        self.assertTrue(xp120a["bpf_scope"]["in_attach_plan"])
        self.assertTrue(xp120a["bpf_scope"]["in_compile_capacity_interfaces"])
        self.assertTrue(xp120a["inactive_visibility_proof"]["occupied_false"])
        self.assertTrue(
            xp120a["inactive_visibility_proof"][
                "no_reachable_peer_or_bridge_forwarding_path"
            ]
        )
        self.assertTrue(
            xp120a["inactive_visibility_proof"]["no_linux_ip_or_mpls_fallback"]
        )
        self.assertTrue(audit["summary"]["ut_current_next_semantics_confirmed"])
        self.assertTrue(audit["summary"]["gw_slot_derivation_complete"])
        self.assertIn("GW_SLOT_DERIVATION_CONFIRMED", audit["outcome_labels"])

    def test_static_inventory_audit_reports_incomplete_gateway_slot_derivation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            (run_dir / "topology.json").write_text(
                json.dumps(
                    {
                        "slot_inventory": {
                            "gateway_access": [
                                {"slot_name": "gw-a-gateway-slot0"},
                                {"slot_name": "gw-a-gateway-slot1"},
                                {"slot_name": "gw-b-gateway-slot0"},
                                {"slot_name": "gw-b-gateway-slot1"},
                            ]
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "link_state.json").write_text(
                json.dumps({"slots": []}) + "\n",
                encoding="utf-8",
            )
            (run_dir / "profile_catalog_gate.json").write_text(
                json.dumps(
                    {
                        "selectors": {
                            "gateway": {"selected_ids": ["gw-a", "gw-b"]},
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "slot_capacity_audit.json").write_text(
                json.dumps(
                    {
                        "calculation_basis": {
                            "gateway_required_by_id": {"gw-a": 2},
                            "active_link_max_concurrent_by_type": {
                                "gw_access": 1,
                            },
                        },
                        "prepared_slots_by_type": {"gw_access": 4},
                        "required_max_concurrent_slots_by_type": {"gw_access": 2},
                        "notes": {
                            "gw_access": "required is max simultaneous plus margin",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            audit = evaluate_static_inventory_precreated_connection_audit(
                run_dir=run_dir,
            )
            written = json.loads(
                (run_dir / "static_inventory_precreated_connection_audit.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                written["slot_derivation_audit"]["gw_max_simultaneous_slots"][
                    "missing_selected_gateway_ids_in_required_by_id"
                ],
                ["gw-b"],
            )

        gw = audit["slot_derivation_audit"]["gw_max_simultaneous_slots"]
        self.assertFalse(audit["summary"]["gw_slot_derivation_complete"])
        self.assertIn("GW_SLOT_DERIVATION_INCOMPLETE", audit["outcome_labels"])
        self.assertEqual(gw["missing_selected_gateway_ids_in_required_by_id"], ["gw-b"])
        self.assertEqual(gw["prepared_slot_count"], 4)
        self.assertEqual(gw["required_slot_count"], 2)

    def test_static_inventory_audit_confirms_gw204_gw212_required_coverage(
        self,
    ) -> None:
        selected_gateways = [f"gw2{i:02d}" for i in range(1, 13)]
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            (run_dir / "topology.json").write_text(
                json.dumps(
                    {
                        "slot_inventory": {
                            "gateway_access": [
                                {"slot_name": f"{gateway_id}-gateway-slot{slot}"}
                                for gateway_id in selected_gateways
                                for slot in range(2)
                            ]
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "link_state.json").write_text(
                json.dumps({"slots": []}) + "\n",
                encoding="utf-8",
            )
            (run_dir / "profile_catalog_gate.json").write_text(
                json.dumps(
                    {
                        "selectors": {
                            "gateway": {"selected_ids": selected_gateways},
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "slot_capacity_audit.json").write_text(
                json.dumps(
                    {
                        "calculation_basis": {
                            "gateway_required_by_id": {
                                gateway_id: 2 for gateway_id in selected_gateways
                            },
                            "active_link_max_concurrent_by_type": {
                                "gw_access": 12,
                            },
                        },
                        "prepared_slots_by_type": {"gw_access": 24},
                        "required_max_concurrent_slots_by_type": {"gw_access": 24},
                        "notes": {
                            "gw_access": "derived from max simultaneous satellite connections plus margin",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            audit = evaluate_static_inventory_precreated_connection_audit(
                run_dir=run_dir,
            )
            written = json.loads(
                (run_dir / "static_inventory_precreated_connection_audit.json").read_text(
                    encoding="utf-8"
                )
            )

        gw = audit["slot_derivation_audit"]["gw_max_simultaneous_slots"]
        self.assertTrue(audit["summary"]["gw_slot_derivation_complete"])
        self.assertIn("GW_SLOT_DERIVATION_CONFIRMED", audit["outcome_labels"])
        self.assertEqual(gw["derivation_label"], "GW_SLOT_DERIVATION_CONFIRMED")
        self.assertEqual(gw["missing_selected_gateway_ids_in_required_by_id"], [])
        self.assertIn("gw204", selected_gateways)
        self.assertIn("gw212", selected_gateways)
        self.assertEqual(gw["required_by_id_count"], 12)
        self.assertEqual(gw["prepared_slot_count"], 24)
        self.assertEqual(gw["required_slot_count"], 24)
        self.assertEqual(
            written["slot_derivation_audit"]["gw_max_simultaneous_slots"][
                "derivation_label"
            ],
            "GW_SLOT_DERIVATION_CONFIRMED",
        )

    def test_startup_cost_and_runtime_semantic_result_remain_separate(self) -> None:
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            epoch = {
                "epoch_id": "runtimeepoch-success",
                "epoch_type": "runtime_epoch",
                "event_timeline_type": "runtime_transaction",
                "outcome": {"success": True},
                "timing": {
                    "event_wait_seconds": 0.0,
                    "host_operation_apply_wall_seconds": 1.0,
                    "node_local_apply_wall_seconds": 1.0,
                    "network_update_seconds": 2.0,
                    "unknown_blocking_seconds": 0.0,
                },
            }
            (run_dir / "epoch_execution_details.jsonl").write_text(
                __import__("json").dumps(epoch) + "\n",
                encoding="utf-8",
            )

            summary = write_runtime_update_timing_evidence(
                run_dir=run_dir,
                runner_report={
                    "event_timeline_execution_success": True,
                    "aggregate_runtime_succeeded": False,
                    "runtime_blocker": {
                        "classified_blocker": "STARTUP_COST_ANOMALY",
                    },
                },
            )

            self.assertTrue(
                summary["runtime_semantic_result"]["event_timeline_execution_success"]
            )
            self.assertFalse(summary["runtime_semantic_result"]["aggregate_runtime_succeeded"])
            self.assertTrue(summary["startup_cost_blocker_reported_separately"])

    def test_non_additive_timing_fields_are_labeled(self) -> None:
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            epoch = {
                "epoch_id": "runtimeepoch-nonadditive",
                "epoch_type": "runtime_epoch",
                "event_timeline_type": "runtime_transaction",
                "outcome": {"success": True},
                "timing": {
                    "event_wait_seconds": 0.0,
                    "host_operation_apply_wall_seconds": 1.0,
                    "node_local_apply_wall_seconds": 1.0,
                    "bpf_prepare_after_incremental_cleanup_wall_seconds": 50.0,
                    "parameter_qdisc_apply_seconds": 25.0,
                    "network_update_seconds": 2.0,
                    "post_apply_overhead_seconds": 50.0,
                    "unknown_blocking_seconds": 0.0,
                },
                "node_local_execution": {
                    "backend": {
                        "bpf_owned_readiness": {
                            "prepare_steps": {
                                "load": {
                                    "active_generation": 2,
                                    "total_update_count": 2,
                                    "map_write_wall_seconds": 10.0,
                                }
                            }
                        }
                    }
                },
            }
            (run_dir / "epoch_execution_details.jsonl").write_text(
                __import__("json").dumps(epoch) + "\n",
                encoding="utf-8",
            )

            summary = write_runtime_update_timing_evidence(run_dir=run_dir)
            rows = [
                __import__("json").loads(line)
                for line in (run_dir / "per_epoch_update_timing.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]

            semantics = summary["timing_field_semantics"]
            self.assertEqual(semantics["bpf_prepare_seconds"]["additivity"], "non_additive")
            self.assertEqual(semantics["qdisc_update_seconds"]["additivity"], "non_additive")
            self.assertEqual(
                summary["consistency_check"]["contradiction_count"],
                0,
            )
            self.assertIn("bpf_prepare_seconds", rows[0]["non_additive_timing_fields"])

    def test_additive_child_timing_contradiction_is_reported(self) -> None:
        with tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs") as tmp:
            run_dir = Path(tmp)
            epoch = {
                "epoch_id": "runtimeepoch-contradiction",
                "epoch_type": "runtime_epoch",
                "event_timeline_type": "runtime_transaction",
                "outcome": {"success": True},
                "timing": {
                    "event_wait_seconds": 0.0,
                    "host_operation_apply_wall_seconds": 10.0,
                    "node_local_apply_wall_seconds": 10.0,
                    "network_update_seconds": 2.0,
                    "unknown_blocking_seconds": 0.0,
                },
            }
            (run_dir / "epoch_execution_details.jsonl").write_text(
                __import__("json").dumps(epoch) + "\n",
                encoding="utf-8",
            )

            summary = write_runtime_update_timing_evidence(run_dir=run_dir)

            self.assertEqual(summary["consistency_check"]["contradiction_count"], 1)
            contradiction = summary["consistency_check"]["contradictions"][0]
            self.assertEqual(contradiction["epoch_id"], "runtimeepoch-contradiction")

    def test_cleanup_residue_detector_catches_compose_network_pool_overlap(self) -> None:
        def fake_runner(command, **_kwargs):
            if command[:3] == ["docker", "ps", "-a"]:
                return _completed("")
            if command[:3] == ["docker", "network", "ls"]:
                return _completed("seed_output_net_1\tseed_output\n")
            if command[:3] == ["docker", "network", "inspect"]:
                return _completed(
                    '[{"Id":"net1","Name":"seed_output_net_1",'
                    '"IPAM":{"Config":[{"Subnet":"10.128.0.0/19"}]}}]'
                )
            if command[:3] == ["ip", "-o", "link"]:
                return _completed("")
            if command[:3] == ["sudo", "-n", "test"]:
                return _completed("", returncode=1)
            if command and command[0] == "find":
                return _completed("")
            return _completed("")

        residue = detect_cleanup_residue(
            docker_dummy_network_pool="10.128.0.0/9",
            runner=fake_runner,
        )

        self.assertEqual(residue["counts"]["compose_networks"], 1)
        self.assertEqual(residue["counts"]["docker_network_pool_overlaps"], 1)
        self.assertFalse(residue["clean"])

    def test_cleanup_residue_detector_catches_direct_backend_interfaces(self) -> None:
        def fake_runner(command, **_kwargs):
            if command[:3] == ["docker", "ps", "-a"]:
                return _completed("")
            if command[:3] == ["docker", "network", "ls"]:
                return _completed("")
            if command[:3] == ["ip", "-o", "link"]:
                return _completed(
                    "9: vhs123@if8: <BROADCAST> mtu 1500 qdisc noop state DOWN mode DEFAULT group default\n"
                )
            if command[:3] == ["sudo", "-n", "test"]:
                return _completed("", returncode=1)
            if command and command[0] == "find":
                return _completed("")
            return _completed("")

        residue = detect_cleanup_residue(runner=fake_runner)

        self.assertEqual(residue["counts"]["direct_backend_host_interfaces"], 1)
        self.assertFalse(residue["clean"])

    def test_cleanup_only_removes_orphan_direct_backend_veth(self) -> None:
        state = {"interfaces": {"veth5e2dc45"}}
        commands: list[list[str]] = []

        def fake_runner(command, **_kwargs):
            commands.append(list(command))
            if command[:3] == ["docker", "ps", "-a"]:
                return _completed("")
            if command[:3] == ["docker", "network", "ls"]:
                return _completed("")
            if command[:3] == ["ip", "-o", "link"]:
                return _completed(
                    "".join(
                        f"9: {name}@if8: <BROADCAST> mtu 1500 qdisc noop state DOWN mode DEFAULT group default\n"
                        for name in sorted(state["interfaces"])
                    )
                )
            if command[:4] == ["sudo", "-n", "ip", "link"] and command[4] == "delete":
                state["interfaces"].discard(str(command[5]))
                return _completed("")
            if command[:3] == ["sudo", "-n", "test"]:
                return _completed("", returncode=1)
            if command and command[0] == "find":
                return _completed("")
            return _completed("")

        with tempfile.TemporaryDirectory() as tmp:
            report = run_cleanup_only(run_dir=Path(tmp), runner=fake_runner)

        self.assertTrue(report["external_cleanup_success"])
        self.assertTrue(report["global_scan_diagnostic_only"])
        self.assertIn("GLOBAL_SCAN_DIAGNOSTIC_ONLY", report["outcome_labels"])
        self.assertIn("CLEANUP_ONLY_REPAIR_MODE", report["outcome_labels"])
        self.assertEqual(report["before"]["counts"]["direct_backend_host_interfaces"], 1)
        self.assertEqual(report["after"]["counts"]["direct_backend_host_interfaces"], 0)
        self.assertIn(
            ["sudo", "-n", "ip", "link", "delete", "veth5e2dc45"],
            commands,
        )

    def test_normal_failure_cleanup_records_bounded_owned_scope_evidence(self) -> None:
        state = {"interfaces": {"veth5e2dc45"}}

        def fake_runner(command, **_kwargs):
            if command[:3] == ["docker", "ps", "-a"]:
                return _completed("")
            if command[:3] == ["docker", "network", "ls"]:
                return _completed("")
            if command[:3] == ["ip", "-o", "link"]:
                return _completed(
                    "".join(
                        f"9: {name}@if8: <BROADCAST> mtu 1500 qdisc noop state DOWN mode DEFAULT group default\n"
                        for name in sorted(state["interfaces"])
                    )
                )
            if command[:4] == ["sudo", "-n", "ip", "link"] and command[4] == "delete":
                state["interfaces"].discard(str(command[5]))
                return _completed("")
            if command[:3] == ["sudo", "-n", "test"]:
                return _completed("", returncode=1)
            if command and command[0] == "find":
                return _completed("")
            return _completed("")

        with tempfile.TemporaryDirectory() as tmp:
            report = run_cleanup_only(
                run_dir=Path(tmp),
                runner=fake_runner,
                report_filename="normal_failure_cleanup_report.json",
                cleanup_mode="normal_failure_cleanup",
                success_outcome_label="CLEANUP_BOUNDARY_PASS",
                cleanup_scope_evidence={
                    "present": True,
                    "cleanup_scope_source": "prepared_owned_resource_index",
                    "run_manifest_id": "run-window-1",
                    "prepared_owned_resource_index_id": "ownedidx-abc",
                    "bounded_owned_resource_count": 12,
                    "prepared_slot_scope_count": 3,
                },
            )

        self.assertEqual(report["cleanup_mode"], "normal_failure_cleanup")
        self.assertEqual(report["cleanup_scope_source"], "prepared_owned_resource_index")
        self.assertEqual(report["run_manifest_id"], "run-window-1")
        self.assertEqual(report["prepared_owned_resource_index_id"], "ownedidx-abc")
        self.assertTrue(report["bounded_owned_scope"])
        self.assertEqual(report["bounded_owned_resource_count"], 12)
        self.assertEqual(report["cleanup_target_count"], 1)
        self.assertEqual(report["direct_backend_host_interface_target_count"], 1)
        self.assertTrue(report["orphan_veth_detector_enabled"])
        self.assertTrue(report["global_scan_used"])
        self.assertTrue(report["global_scan_diagnostic_only"])
        self.assertFalse(report["full_scope_repair_used"])
        self.assertFalse(report["full_scope_delete_used"])
        self.assertFalse(report["full_scope_reconcile_used"])
        self.assertFalse(report["normal_success_uses_global_scan"])
        self.assertEqual(report["final_residue_summary"]["counts"]["direct_backend_host_interfaces"], 0)
        self.assertIn("orphan_veth", report["detector_coverage"])

    def test_automated_cleanup_removes_owned_bpf_pins(self) -> None:
        state = {"pin_exists": True}
        commands: list[list[str]] = []

        def fake_runner(command, **_kwargs):
            commands.append(list(command))
            if command[:3] == ["docker", "ps", "-a"]:
                return _completed("")
            if command[:3] == ["docker", "network", "ls"]:
                return _completed("")
            if command[:3] == ["ip", "-o", "link"]:
                return _completed("")
            if command[:3] == ["sudo", "-n", "test"]:
                return _completed("", returncode=0 if state["pin_exists"] else 1)
            if command[:3] == ["sudo", "-n", "rm"]:
                state["pin_exists"] = False
                return _completed("")
            if command and command[0] == "find":
                return _completed("")
            return _completed("")

        with tempfile.TemporaryDirectory() as tmp:
            report = run_cleanup_only(run_dir=Path(tmp), runner=fake_runner)

        self.assertTrue(report["external_cleanup_success"])
        self.assertTrue(any(cmd[:3] == ["sudo", "-n", "rm"] for cmd in commands))
        self.assertEqual(report["after"]["counts"]["bpf_pins"], 0)

    def test_automated_cleanup_removes_owned_compose_or_reports_failure(self) -> None:
        state = {"containers": {"stl-1"}, "networks": {"seed_output_default"}}

        def fake_runner(command, **_kwargs):
            if command[:3] == ["docker", "ps", "-a"]:
                return _completed(
                    "\n".join(
                        f"{name}\tseed_output\tExited"
                        for name in sorted(state["containers"])
                    )
                    + ("\n" if state["containers"] else "")
                )
            if command[:3] == ["docker", "network", "ls"]:
                return _completed(
                    "\n".join(
                        f"{name}\tseed_output"
                        for name in sorted(state["networks"])
                    )
                    + ("\n" if state["networks"] else "")
                )
            if command[:3] == ["docker", "network", "inspect"]:
                return _completed(
                    '[{"Id":"n","Name":"seed_output_default",'
                    '"IPAM":{"Config":[{"Subnet":"10.128.0.0/19"}]},'
                    '"Containers":{}}]'
                )
            if command[:3] == ["docker", "rm", "-f"]:
                state["containers"].difference_update(command[3:])
                return _completed("")
            if command[:3] == ["docker", "network", "rm"]:
                state["networks"].discard(command[3])
                return _completed("")
            if command[:3] == ["ip", "-o", "link"]:
                return _completed("")
            if command[:3] == ["sudo", "-n", "test"]:
                return _completed("", returncode=1)
            if command and command[0] == "find":
                return _completed("")
            return _completed("")

        with tempfile.TemporaryDirectory() as tmp:
            report = run_cleanup_only(
                run_dir=Path(tmp),
                docker_dummy_network_pool="10.128.0.0/9",
                runner=fake_runner,
            )

        self.assertTrue(report["external_cleanup_success"])
        self.assertEqual(report["before"]["counts"]["compose_containers"], 1)
        self.assertEqual(report["before"]["counts"]["compose_networks"], 1)
        self.assertEqual(report["after"]["counts"]["compose_containers"], 0)
        self.assertEqual(report["after"]["counts"]["compose_networks"], 0)

    def test_cleanup_only_and_normal_failure_cleanup_use_same_detector(self) -> None:
        def fake_runner(command, **_kwargs):
            if command[:3] == ["docker", "ps", "-a"]:
                return _completed("")
            if command[:3] == ["docker", "network", "ls"]:
                return _completed("")
            if command[:3] == ["ip", "-o", "link"]:
                return _completed("")
            if command[:3] == ["sudo", "-n", "test"]:
                return _completed("", returncode=1)
            if command and command[0] == "find":
                return _completed("")
            return _completed("")

        with tempfile.TemporaryDirectory() as tmp:
            cleanup_only = run_cleanup_only(
                run_dir=Path(tmp) / "cleanup-only",
                runner=fake_runner,
            )
            normal_failure = run_cleanup_only(
                run_dir=Path(tmp) / "normal-failure",
                runner=fake_runner,
                report_filename="normal_failure_cleanup_report.json",
                cleanup_mode="normal_failure_cleanup",
                success_outcome_label="CLEANUP_BOUNDARY_PASS",
            )

        self.assertEqual(cleanup_only["detector_source"], "detect_cleanup_residue")
        self.assertEqual(normal_failure["detector_source"], "detect_cleanup_residue")
        self.assertEqual(cleanup_only["before"]["owner_patterns"], normal_failure["before"]["owner_patterns"])
        self.assertIn("GLOBAL_SCAN_DIAGNOSTIC_ONLY", cleanup_only["outcome_labels"])
        self.assertIn("CLEANUP_ONLY_REPAIR_MODE", cleanup_only["outcome_labels"])
        self.assertIn("GLOBAL_SCAN_DIAGNOSTIC_ONLY", normal_failure["outcome_labels"])
        self.assertIn("NORMAL_FAILURE_CLEANUP_MODE", normal_failure["outcome_labels"])
        self.assertEqual(normal_failure["cleanup_mode"], "normal_failure_cleanup")
        self.assertEqual(normal_failure["outcome_label"], "CLEANUP_BOUNDARY_PASS")

    def test_horizon_completion_reconciles_parameter_delta_attach_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "lifecycle_aware_horizon_contract.json").write_text(
                json.dumps(
                    {
                        "horizon_selected_by": "EventTimeline",
                        "whole_horizon_completed": True,
                        "ready_for_next_horizon": True,
                        "event_reports": [
                            {
                                "slice_id": "starlink-shell-slice-0001",
                                "epoch_id": "runtimeepoch-1",
                                "primary_class": "map_only_update",
                            },
                            {
                                "slice_id": "starlink-shell-slice-0011",
                                "epoch_id": "parameterepoch-1",
                                "primary_class": "parameter_update",
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "aggregate_runtime_succeeded": False,
                        "iterations": [{}, {}, {}, {}],
                        "failure_context": {
                            "current_epoch_context": {
                                "slice_id": "starlink-shell-slice-0011",
                                "epoch_id": "parameterepoch-1",
                                "classification": "parameter_epoch",
                                "runtime_epochs_completed": 4,
                                "parameter_epochs_completed": 0,
                            },
                            "exception": {
                                "message": "bpf_owned_runtime_readiness_failed:reason=tc_bpf_mpls_delta_attach_failed",
                                "skyseed_details": {
                                    "bpf_owned_readiness": {
                                        "prepare_steps": {
                                            "real_backend_prepare": {
                                                "delta_attach_count": 42,
                                                "full_attach_count": 0,
                                            }
                                        }
                                    }
                                },
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            reconciled = reconcile_lifecycle_horizon_completion_with_execution(
                run_dir=run_dir
            )

            self.assertFalse(reconciled["whole_horizon_completed"])
            self.assertFalse(reconciled["ready_for_next_horizon"])
            self.assertEqual(reconciled["map_only_runtime_epochs_completed"], 4)
            self.assertEqual(reconciled["parameter_epochs_completed"], 0)
            self.assertIn(
                "HORIZON_COMPLETION_REPORTING_INCONSISTENT",
                reconciled["outcome_labels"],
            )
            self.assertIn(
                "PARAMETER_UPDATE_TRIGGERED_DELTA_ATTACH_BLOCKER",
                reconciled["outcome_labels"],
            )
            parameter_event = reconciled["event_reports"][1]
            self.assertEqual(parameter_event["execution_status"], "failed")
            self.assertEqual(
                parameter_event["first_semantic_cause"],
                "PARAMETER_UPDATE_TRIGGERED_DELTA_ATTACH_BLOCKER",
            )
            self.assertFalse(parameter_event["packet_miss_authoritative"])

    def test_parameter_update_without_delta_attach_does_not_report_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "lifecycle_aware_horizon_contract.json").write_text(
                json.dumps(
                    {
                        "horizon_selected_by": "EventTimeline",
                        "whole_horizon_completed": True,
                        "ready_for_next_horizon": True,
                        "event_reports": [
                            {
                                "slice_id": "starlink-shell-slice-0011",
                                "epoch_id": "parameterepoch-1",
                                "primary_class": "parameter_update",
                                "execution_status": "completed",
                            },
                        ],
                        "outcome_labels": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "aggregate_runtime_succeeded": True,
                        "iterations": [],
                        "failure_context": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            reconciled = reconcile_lifecycle_horizon_completion_with_execution(
                run_dir=run_dir
            )

            self.assertTrue(reconciled["whole_horizon_completed"])
            self.assertTrue(reconciled["ready_for_next_horizon"])
            self.assertFalse(reconciled["selected_event_failed"])
            self.assertNotIn(
                "PARAMETER_UPDATE_TRIGGERED_DELTA_ATTACH_BLOCKER",
                reconciled["outcome_labels"],
            )
            self.assertNotIn(
                "PARAMETER_UPDATE_TRIGGERED_DELTA_ATTACH_BLOCKER",
                reconciled["next_horizon_blockers"],
            )

    def test_runtime_preload_audit_reports_diff_without_preload(self) -> None:
        def iteration(phase: str, *, preloaded: bool, noop: int) -> dict[str, object]:
            return {
                "node_local_execution": {
                    "backend": {
                        "bpf_owned_readiness": {
                            "prepare_steps": {
                                "real_backend_prepare": {
                                    "steps": {
                                        "map_load": {
                                            "runtime_reload_attribution": {
                                                "runtime_update_phase": phase,
                                                "preactivation_preload": False,
                                                "preloaded_generation_used": preloaded,
                                                "map_diff_update_used": True,
                                                "active_generation_flip_only": False,
                                                "full_generation_reload_blocker": False,
                                                "actual_data_write_count": 189,
                                                "noop_skipped_count": noop,
                                                "map_diff_trace": {
                                                    "ingress_classifier_map": {
                                                        "desired_count": 120,
                                                        "existing_count": 0,
                                                        "insert_count": 120,
                                                        "update_count": 0,
                                                        "delete_count": 0,
                                                        "noop_skipped_count": 0,
                                                        "wall_seconds": 0.1,
                                                    },
                                                    "interface_role_map": {
                                                        "desired_count": 68,
                                                        "existing_count": 70,
                                                        "insert_count": 0,
                                                        "update_count": 0,
                                                        "delete_count": 0,
                                                        "noop_skipped_count": noop,
                                                        "wall_seconds": 0.2,
                                                    },
                                                },
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "iterations": [
                            iteration("bootstrap", preloaded=False, noop=0),
                            iteration("steady_map_only", preloaded=False, noop=68),
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            audit = evaluate_runtime_preload_diff_contract(run_dir=run_dir)

            self.assertIn("RUNTIME_MAP_DIFF_UPDATE_USED", audit["outcome_labels"])
            self.assertIn("RUNTIME_NOOP_MAP_WRITES_SKIPPED", audit["outcome_labels"])
            self.assertIn("RUNTIME_PRELOAD_NOT_EXERCISED", audit["outcome_labels"])
            self.assertNotIn(
                "RUNTIME_PRELOADED_GENERATION_USED",
                audit["outcome_labels"],
            )
            self.assertEqual(audit["steady_map_only_epoch_count"], 1)
            self.assertFalse(audit["preload_contract_exercised"])
            steady = audit["epoch_reports"][1]
            self.assertEqual(
                steady["map_diff_trace"]["ingress_classifier_map"]["insert_count"],
                120,
            )
            self.assertEqual(
                steady["map_diff_trace"]["interface_role_map"][
                    "noop_skipped_count"
                ],
                68,
            )

    def test_runtime_preload_audit_does_not_treat_reuse_as_scheduled_preload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "iterations": [
                            {
                                "node_local_execution": {
                                    "backend": {
                                        "bpf_owned_readiness": {
                                            "prepare_steps": {
                                                "real_backend_prepare": {
                                                    "steps": {
                                                        "map_load": {
                                                            "runtime_reload_attribution": {
                                                                "runtime_update_phase": "steady_map_only",
                                                                "preloaded_generation_used": True,
                                                                "active_generation_flip_only": True,
                                                                "map_diff_update_used": False,
                                                                "full_generation_reload_blocker": False,
                                                                "actual_data_write_count": 0,
                                                                "noop_skipped_count": 120,
                                                                "map_diff_trace": {
                                                                    "ingress_classifier_map": {
                                                                        "desired_count": 120,
                                                                        "existing_count": 120,
                                                                        "insert_count": 0,
                                                                        "update_count": 0,
                                                                        "delete_count": 0,
                                                                        "noop_skipped_count": 120,
                                                                        "wall_seconds": 0.1,
                                                                    }
                                                                },
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            audit = evaluate_runtime_preload_diff_contract(run_dir=run_dir)

            self.assertIn("RUNTIME_PRELOADED_GENERATION_USED", audit["outcome_labels"])
            self.assertIn("RUNTIME_ACTIVE_GENERATION_FLIP_ONLY", audit["outcome_labels"])
            self.assertIn("RUNTIME_PRELOAD_NOT_EXERCISED", audit["outcome_labels"])
            self.assertNotIn("RUNTIME_PRELOAD_SCHEDULED", audit["outcome_labels"])
            self.assertEqual(audit["preactivation_preload_epoch_count"], 0)
            self.assertFalse(audit["preload_contract_exercised"])

    def test_runtime_preload_audit_counts_scheduler_timing_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "horizon_lifecycle_queue_contract.json").write_text(
                json.dumps(
                    {
                        "lookahead_preload_event_count": 1,
                        "preload_required_event_count": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "prepared_update_bundle.json").write_text(
                json.dumps(
                    {
                        "lookahead_preload_queue": [
                            {
                                "event_id": "runtimeepoch-lookahead",
                                "preload_required": True,
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "iterations": [
                            {
                                "node_local_execution": {
                                    "timing": {
                                        "bpf_lookahead_preactivation_preload_event_count": 1,
                                        "bpf_lookahead_preactivation_preload_success_count": 1,
                                        "bpf_lookahead_preactivation_preload_failed_count": 0,
                                        "bpf_lookahead_preactivation_preload_delta_attach_count": 0,
                                        "bpf_lookahead_preactivation_preload_full_attach_count": 0,
                                    },
                                    "backend": {
                                        "bpf_owned_readiness": {
                                            "prepare_steps": {
                                                "real_backend_prepare": {
                                                    "delta_attach_count": 0,
                                                    "full_attach_count": 0,
                                                    "steps": {
                                                        "map_load": {
                                                            "runtime_reload_attribution": {
                                                                "runtime_update_phase": "bootstrap",
                                                                "preloaded_generation_used": False,
                                                                "active_generation_flip_only": False,
                                                                "map_diff_update_used": False,
                                                                "actual_data_write_count": 0,
                                                                "noop_skipped_count": 0,
                                                            }
                                                        }
                                                    },
                                                }
                                            }
                                        }
                                    },
                                }
                            },
                            {
                                "node_local_execution": {
                                    "backend": {
                                        "bpf_owned_readiness": {
                                            "prepare_steps": {
                                                "real_backend_prepare": {
                                                    "delta_attach_count": 0,
                                                    "full_attach_count": 0,
                                                    "steps": {
                                                        "map_load": {
                                                            "runtime_reload_attribution": {
                                                                "runtime_update_phase": "steady_map_only",
                                                                "preloaded_generation_used": True,
                                                                "active_generation_flip_only": True,
                                                                "map_diff_update_used": False,
                                                                "actual_data_write_count": 0,
                                                                "noop_skipped_count": 120,
                                                            }
                                                        }
                                                    },
                                                }
                                            }
                                        }
                                    }
                                }
                            },
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            audit = evaluate_runtime_preload_diff_contract(run_dir=run_dir)

            self.assertIn("RUNTIME_PRELOAD_SCHEDULED", audit["outcome_labels"])
            self.assertIn("RUNTIME_PRELOAD_DISPATCHED", audit["outcome_labels"])
            self.assertNotIn("RUNTIME_PRELOAD_NOT_EXERCISED", audit["outcome_labels"])
            self.assertEqual(audit["lookahead_preload_scheduled_count"], 1)
            self.assertEqual(audit["lookahead_preload_dispatched_count"], 1)
            self.assertEqual(audit["preactivation_preload_epoch_count"], 1)
            self.assertEqual(audit["preactivation_preload_attempt_count"], 1)
            self.assertEqual(audit["preactivation_preload_success_count"], 1)
            self.assertEqual(audit["preactivation_preload_fail_count"], 0)
            self.assertEqual(audit["preactivation_preload_failed_count"], 0)
            self.assertEqual(audit["preactivation_preload_noop_reuse_count"], 1)
            self.assertEqual(audit["preactivation_preload_skipped_count"], 0)
            self.assertEqual(audit["inactive_generation_write_count"], 0)
            self.assertEqual(
                audit["preactivation_preload_result_detail_incomplete_count"],
                1,
            )
            self.assertFalse(
                audit["preload_noop_reuse_equivalence_proof_available"]
            )
            self.assertEqual(
                audit["preload_noop_reuse_equivalence_status"],
                "missing_detail",
            )
            self.assertEqual(
                audit["preload_noop_reuse_equivalence_proofs"][0][
                    "missing_reason"
                ],
                "lookahead_preload_report_missing",
            )
            self.assertTrue(audit["preload_contract_exercised"])

    def test_runtime_preload_audit_proves_noop_reuse_from_zero_diff_trace(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "horizon_lifecycle_queue_contract.json").write_text(
                json.dumps(
                    {
                        "lookahead_preload_event_count": 1,
                        "preload_required_event_count": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "prepared_update_bundle.json").write_text(
                json.dumps(
                    {
                        "lookahead_preload_queue": [
                            {
                                "event_id": "runtimeepoch-lookahead",
                                "slice_id": "slice-a",
                                "preload_required": True,
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "iterations": [
                            {
                                "node_local_execution": {
                                    "timing": {
                                        "bpf_lookahead_preactivation_preload_event_count": 1,
                                        "bpf_lookahead_preactivation_preload_success_count": 1,
                                        "bpf_lookahead_preactivation_preload_failed_count": 0,
                                    },
                                    "backend": {
                                        "bpf_owned_readiness": {
                                            "prepare_steps": {
                                                "real_backend_prepare": {
                                                    "delta_attach_count": 0,
                                                    "full_attach_count": 0,
                                                    "steps": {
                                                        "map_load": {
                                                            "runtime_reload_attribution": {
                                                                "runtime_update_phase": "bootstrap",
                                                                "preloaded_generation_used": False,
                                                                "active_generation_flip_only": False,
                                                                "map_diff_update_used": False,
                                                                "actual_data_write_count": 0,
                                                                "noop_skipped_count": 0,
                                                            }
                                                        }
                                                    },
                                                }
                                            }
                                        }
                                    },
                                }
                            },
                            {
                                "node_local_execution": {
                                    "backend": {
                                        "bpf_owned_readiness": {
                                            "prepare_steps": {
                                                "real_backend_prepare": {
                                                    "delta_attach_count": 0,
                                                    "full_attach_count": 0,
                                                    "steps": {
                                                        "map_load": {
                                                            "runtime_reload_attribution": {
                                                                "runtime_update_phase": "steady_map_only",
                                                                "preloaded_generation_used": True,
                                                                "active_generation_flip_only": True,
                                                                "map_diff_update_used": False,
                                                                "runtime_full_generation_reload": False,
                                                                "full_generation_write_used": False,
                                                                "actual_data_write_count": 0,
                                                                "noop_skipped_count": 256,
                                                                "active_generation_before": 0,
                                                                "active_generation_after": 1,
                                                                "map_diff_trace": {
                                                                    "ingress_classifier_map": {
                                                                        "desired_count": 120,
                                                                        "existing_count": 120,
                                                                        "insert_count": 0,
                                                                        "update_count": 0,
                                                                        "delete_count": 0,
                                                                        "noop_skipped_count": 120,
                                                                        "wall_seconds": 0.1,
                                                                    },
                                                                    "interface_role_map": {
                                                                        "desired_count": 136,
                                                                        "existing_count": 138,
                                                                        "insert_count": 0,
                                                                        "update_count": 0,
                                                                        "delete_count": 0,
                                                                        "noop_skipped_count": 136,
                                                                        "wall_seconds": 0.2,
                                                                    },
                                                                },
                                                            }
                                                        }
                                                    },
                                                }
                                            }
                                        }
                                    }
                                }
                            },
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            audit = evaluate_runtime_preload_diff_contract(run_dir=run_dir)

            self.assertTrue(
                audit["preload_noop_reuse_equivalence_proof_available"]
            )
            self.assertEqual(
                audit["preload_noop_reuse_equivalence_status"],
                "proved",
            )
            proof = audit["preload_noop_reuse_equivalence_proofs"][0]
            self.assertFalse(proof["report_present"])
            self.assertEqual(
                proof["proof_source"],
                "runtime_map_load_preloaded_generation_zero_diff_noop_trace",
            )
            self.assertEqual(
                proof["equivalence_proof_kind"],
                "zero_diff_noop_coverage",
            )
            self.assertEqual(proof["generation_equivalence_status"], "proved")
            self.assertEqual(
                proof["inactive_generation_write_explanation"],
                "scheduled_preload_noop_reuse_zero_diff_noop_coverage",
            )
            self.assertEqual(proof["active_generation_id"], 0)
            self.assertEqual(proof["reused_generation_id"], 1)
            self.assertTrue(
                proof["per_map_equivalence"]["interface_role_map"][
                    "noop_covers_expected"
                ]
            )
            self.assertTrue(audit["preload_contract_exercised"])

    def test_runtime_preload_audit_distinguishes_preload_reuse_full_write_and_attach(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "iterations": [
                            {
                                "node_local_execution": {
                                    "backend": {
                                        "bpf_owned_readiness": {
                                            "prepare_steps": {
                                                "real_backend_prepare": {
                                                    "delta_attach_count": 0,
                                                    "full_attach_count": 0,
                                                    "steps": {
                                                        "preactivation_preload": {
                                                            "map_write_update_count": 12,
                                                            "runtime_reload_attribution": {
                                                                "runtime_update_phase": "preactivation_preload",
                                                                "preactivation_preload": True,
                                                                "map_diff_update_used": True,
                                                                "noop_skipped_count": 3,
                                                            },
                                                        },
                                                        "map_load": {
                                                            "runtime_reload_attribution": {
                                                                "runtime_update_phase": "steady_map_only",
                                                                "preloaded_generation_used": True,
                                                                "active_generation_flip_only": True,
                                                                "map_diff_update_used": False,
                                                                "actual_data_write_count": 0,
                                                                "noop_skipped_count": 15,
                                                            }
                                                        },
                                                    },
                                                }
                                            }
                                        }
                                    }
                                }
                            },
                            {
                                "node_local_execution": {
                                    "backend": {
                                        "bpf_owned_readiness": {
                                            "prepare_steps": {
                                                "real_backend_prepare": {
                                                    "delta_attach_count": 2,
                                                    "full_attach_count": 1,
                                                    "map_reload_fallback_to_full_attach": True,
                                                    "steps": {
                                                        "map_load": {
                                                            "full_generation_write": True,
                                                            "runtime_reload_attribution": {
                                                                "runtime_update_phase": "steady_map_only",
                                                                "runtime_full_generation_write": True,
                                                                "runtime_full_generation_reload": True,
                                                                "map_diff_update_used": False,
                                                                "preloaded_generation_used": False,
                                                                "active_generation_flip_only": False,
                                                                "actual_data_write_count": 44,
                                                                "noop_skipped_count": 0,
                                                            }
                                                        }
                                                    },
                                                }
                                            }
                                        }
                                    }
                                }
                            },
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            audit = evaluate_runtime_preload_diff_contract(run_dir=run_dir)

        self.assertEqual(audit["preactivation_preload_epoch_count"], 1)
        self.assertEqual(audit["preloaded_generation_used_epoch_count"], 1)
        self.assertEqual(audit["active_generation_flip_only_epoch_count"], 1)
        self.assertEqual(audit["runtime_full_generation_write_epoch_count"], 1)
        self.assertEqual(audit["runtime_full_generation_reload_epoch_count"], 1)
        self.assertEqual(audit["bpf_delta_attach_count"], 2)
        self.assertEqual(audit["bpf_full_attach_count"], 1)
        self.assertIn("RUNTIME_PRELOAD_SCHEDULED", audit["outcome_labels"])
        self.assertIn("RUNTIME_FULL_GENERATION_WRITE_OBSERVED", audit["outcome_labels"])
        self.assertIn("RUNTIME_FULL_GENERATION_RELOAD_OBSERVED", audit["outcome_labels"])
        self.assertIn("RUNTIME_BPF_DELTA_ATTACH_OBSERVED", audit["outcome_labels"])
        self.assertIn("RUNTIME_BPF_FULL_ATTACH_OBSERVED", audit["outcome_labels"])

    def test_runtime_preload_audit_includes_failure_context_preload(self) -> None:
        failure_readiness = {
            "prepare_steps": {
                "real_backend_prepare": {
                    "steps": {
                        "preactivation_preload": {
                            "map_write_update_count": 180,
                            "interface_role_map_updates_skipped": True,
                            "interface_role_map_skipped_update_count": 98,
                            "interface_role_map_update_skip_reason": (
                                "roles_prepared_by_attach_capability"
                            ),
                            "parallel_map_write_enabled": True,
                            "parallel_map_write_worker_count": 8,
                            "parallel_map_write_scheduled_update_count": 180,
                            "runtime_reload_attribution": {
                                "runtime_update_phase": "preactivation_preload",
                                "preactivation_preload": True,
                                "map_diff_update_used": True,
                                "noop_skipped_count": 0,
                                "interface_role_map_updates_skipped": True,
                                "interface_role_map_skipped_update_count": 98,
                                "interface_role_map_update_skip_reason": (
                                    "roles_prepared_by_attach_capability"
                                ),
                                "parallel_map_write_enabled": True,
                                "parallel_map_write_worker_count": 8,
                                "parallel_map_write_scheduled_update_count": 180,
                                "map_diff_trace": {
                                    "ingress_classifier_map": {
                                        "desired_count": 120,
                                        "existing_count": 0,
                                        "insert_count": 120,
                                        "update_count": 0,
                                        "delete_count": 0,
                                        "noop_skipped_count": 0,
                                        "wall_seconds": 0.1,
                                    },
                                },
                            },
                        },
                        "map_load": {
                            "runtime_reload_attribution": {
                                "runtime_update_phase": "steady_map_only",
                                "preloaded_generation_used": True,
                                "map_diff_update_used": False,
                                "active_generation_flip_only": True,
                                "full_generation_reload_blocker": False,
                                "actual_data_write_count": 0,
                                "noop_skipped_count": 278,
                                "active_generation_before": 0,
                                "active_generation_after": 1,
                                "map_diff_trace": {
                                    "ingress_classifier_map": {
                                        "desired_count": 120,
                                        "existing_count": 120,
                                        "insert_count": 0,
                                        "update_count": 0,
                                        "delete_count": 0,
                                        "noop_skipped_count": 120,
                                        "wall_seconds": 0.1,
                                    },
                                },
                            }
                        },
                    }
                }
            }
        }

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "failure_context": {
                            "exception": {
                                "skyseed_details": {
                                    "bpf_owned_readiness": failure_readiness,
                                }
                            }
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            audit = evaluate_runtime_preload_diff_contract(run_dir=run_dir)

            self.assertIn("RUNTIME_PRELOAD_SCHEDULED", audit["outcome_labels"])
            self.assertIn("RUNTIME_PRELOADED_GENERATION_USED", audit["outcome_labels"])
            self.assertIn("RUNTIME_ACTIVE_GENERATION_FLIP_ONLY", audit["outcome_labels"])
            self.assertIn("RUNTIME_NOOP_MAP_WRITES_SKIPPED", audit["outcome_labels"])
            self.assertEqual(audit["steady_map_only_epoch_count"], 1)
            self.assertEqual(audit["preactivation_preload_epoch_count"], 1)
            self.assertTrue(audit["preload_contract_exercised"])
            self.assertEqual(audit["epoch_reports"][0]["observation_source"], "failure_context")
            self.assertTrue(
                audit["epoch_reports"][0]["preactivation_preload_map_diff_update_used"]
            )
            self.assertEqual(
                audit["epoch_reports"][0]["preactivation_preload_map_diff_trace"][
                    "ingress_classifier_map"
                ]["insert_count"],
                120,
            )
            self.assertEqual(audit["preactivation_preload_attempt_count"], 1)
            self.assertEqual(audit["preactivation_preload_success_count"], 1)
            self.assertEqual(audit["preactivation_preload_failed_count"], 0)
            self.assertEqual(audit["inactive_generation_write_count"], 1)
            self.assertEqual(audit["active_generation_flip_count"], 1)

    def test_runtime_preload_audit_counts_failed_lookahead_attempt_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "failure_reason": (
                            "bpf_lookahead_preactivation_preload_failed:"
                            "ACTIVE_INTERFACE_BINDING_MISSING"
                        )
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            audit = evaluate_runtime_preload_diff_contract(run_dir=run_dir)

        self.assertEqual(audit["preactivation_preload_epoch_count"], 0)
        self.assertEqual(audit["preactivation_preload_attempt_count"], 1)
        self.assertEqual(audit["preactivation_preload_success_count"], 0)
        self.assertEqual(audit["preactivation_preload_failed_count"], 1)
        self.assertEqual(audit["inactive_generation_write_count"], 0)
        self.assertEqual(audit["active_generation_flip_count"], 0)

    def test_runtime_preload_audit_reports_preload_diff_when_activation_fails(self) -> None:
        failure_readiness = {
            "prepare_steps": {
                "real_backend_prepare": {
                    "steps": {
                        "preactivation_preload": {
                            "map_write_update_count": 180,
                            "interface_role_map_updates_skipped": True,
                            "interface_role_map_skipped_update_count": 98,
                            "interface_role_map_update_skip_reason": (
                                "roles_prepared_by_attach_capability"
                            ),
                            "parallel_map_write_enabled": True,
                            "parallel_map_write_worker_count": 8,
                            "parallel_map_write_scheduled_update_count": 180,
                            "runtime_reload_attribution": {
                                "runtime_update_phase": "preactivation_preload",
                                "preactivation_preload": True,
                                "map_diff_update_used": True,
                                "noop_skipped_count": 0,
                                "interface_role_map_updates_skipped": True,
                                "interface_role_map_skipped_update_count": 98,
                                "interface_role_map_update_skip_reason": (
                                    "roles_prepared_by_attach_capability"
                                ),
                                "parallel_map_write_enabled": True,
                                "parallel_map_write_worker_count": 8,
                                "parallel_map_write_scheduled_update_count": 180,
                                "map_diff_trace": {
                                    "ingress_classifier_map": {
                                        "desired_count": 120,
                                        "existing_count": 0,
                                        "insert_count": 120,
                                        "update_count": 0,
                                        "delete_count": 0,
                                        "noop_skipped_count": 0,
                                        "wall_seconds": 0.1,
                                    },
                                },
                            },
                        },
                        "map_load": {
                            "runtime_reload_attribution": {
                                "runtime_update_phase": "steady_map_only",
                                "preloaded_generation_used": False,
                                "map_diff_update_used": False,
                                "active_generation_flip_only": False,
                                "full_generation_reload_blocker": False,
                                "actual_data_write_count": 0,
                                "noop_skipped_count": 0,
                                "active_generation_before": 0,
                                "active_generation_after": 0,
                                "map_diff_trace": {},
                            }
                        },
                    }
                }
            }
        }

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "real_runtime_report.json").write_text(
                json.dumps(
                    {
                        "failure_context": {
                            "exception": {
                                "skyseed_details": {
                                    "bpf_owned_readiness": failure_readiness,
                                }
                            }
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            audit = evaluate_runtime_preload_diff_contract(run_dir=run_dir)

            self.assertIn("RUNTIME_PRELOAD_SCHEDULED", audit["outcome_labels"])
            self.assertIn("RUNTIME_MAP_DIFF_UPDATE_USED", audit["outcome_labels"])
            self.assertIn(
                "RUNTIME_INTERFACE_ROLE_MAP_UPDATES_SKIPPED",
                audit["outcome_labels"],
            )
            self.assertIn(
                "RUNTIME_PREACTIVATION_PRELOAD_PARALLEL_MAP_WRITE_USED",
                audit["outcome_labels"],
            )
            self.assertIn("RUNTIME_PRELOAD_NOT_EXERCISED", audit["outcome_labels"])
            self.assertFalse(audit["preload_contract_exercised"])
            report = audit["epoch_reports"][0]
            self.assertTrue(report["preactivation_preload_map_diff_update_used"])
            self.assertEqual(report["preactivation_preload_noop_skipped_count"], 0)
            self.assertTrue(
                report["preactivation_preload_interface_role_map_updates_skipped"]
            )
            self.assertEqual(
                report[
                    "preactivation_preload_interface_role_map_skipped_update_count"
                ],
                98,
            )
            self.assertNotIn(
                "interface_role_map",
                report["preactivation_preload_map_diff_trace"],
            )
            self.assertEqual(
                audit["preactivation_preload_interface_role_map_skip_count"],
                98,
            )
            self.assertEqual(
                audit["preactivation_preload_parallel_map_write_epoch_count"],
                1,
            )
            self.assertEqual(
                audit[
                    "preactivation_preload_parallel_map_write_scheduled_update_count"
                ],
                180,
            )

    def test_stale_runtime_lock_removed_only_after_pid_not_found_check(self) -> None:
        commands: list[list[str]] = []

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock_path = root / "skyseed_real_runtime_active.lock"
            lock_path.write_text(
                '{"pid": 424242, "run_id": "interrupted-rich-s4"}\n',
                encoding="utf-8",
            )

            def fake_runner(command, **_kwargs):
                commands.append(list(command))
                if command[:3] == ["docker", "ps", "-a"]:
                    return _completed("")
                if command[:3] == ["docker", "network", "ls"]:
                    return _completed("")
                if command[:3] == ["ip", "-o", "link"]:
                    return _completed("")
                if command[:3] == ["sudo", "-n", "test"]:
                    return _completed("", returncode=1)
                if command and command[0] == "find":
                    return _completed(str(lock_path) + "\n" if lock_path.exists() else "")
                if command[:3] == ["sh", "-c", 'kill -0 "$1" 2>/dev/null']:
                    return _completed("", returncode=1)
                if command[:2] == ["rm", "-f"]:
                    lock_path.unlink(missing_ok=True)
                    return _completed("")
                return _completed("")

            report = run_cleanup_only(
                run_dir=root / "cleanup",
                runner=fake_runner,
            )

        pid_check_index = next(
            index
            for index, command in enumerate(commands)
            if command[:3] == ["sh", "-c", 'kill -0 "$1" 2>/dev/null']
        )
        rm_index = next(
            index
            for index, command in enumerate(commands)
            if command[:2] == ["rm", "-f"]
        )
        self.assertLess(pid_check_index, rm_index)
        self.assertTrue(report["external_cleanup_success"])
        self.assertTrue(
            any(
                item.get("lock_pid_verification") == "pid_not_found"
                for item in report["commands"]
            )
        )

    def test_internal_cleanup_success_cannot_be_true_if_external_residue_remains(self) -> None:
        residue = {"counts": {"compose_networks": 1, "bpf_pins": 0}}

        self.assertFalse(external_cleanup_success(residue))

    def test_profile_runner_blocks_rich_s4_when_residue_overlaps_dummy_pool(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")

        gate = docker_dummy_network_capacity_gate(
            profile,
            host_prefixes=[{"source": "test", "prefix": "10.0.2.0/24"}],
            docker_prefixes=[
                {
                    "source": "docker network inspect",
                    "network_name": "seed_output_net_1",
                    "prefix": "10.128.0.0/19",
                }
            ],
        )

        self.assertFalse(gate["ok"])
        self.assertIn(
            "docker_dummy_network_pool_overlaps_existing_docker_network",
            gate["failure_reasons"],
        )
        self.assertEqual(
            gate["outcome_label"],
            "DOCKER_DUMMY_NETWORK_POOL_CAPACITY_BLOCKER",
        )

    def test_rich_s4_catalog_contract_passes_with_actual_catalog_ids(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        gate = resolve_catalog_contract(profile)

        self.assertTrue(gate["ok"])
        self.assertEqual(gate["selectors"]["ut"]["selected_ids"][0], "ut2001")
        self.assertEqual(gate["selectors"]["ut"]["selected_ids"][-1], "ut2032")
        self.assertEqual(gate["selectors"]["gateway"]["selected_ids"][0], "gw201")
        self.assertEqual(gate["selectors"]["gateway"]["selected_ids"][-1], "gw212")
        self.assertIn(
            "zone-north-america-west-wide",
            gate["selectors"]["zone"]["selected_ids"],
        )

    def test_stale_legacy_ut_ids_fail_with_profile_catalog_error(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        profile = copy.deepcopy(profile)
        profile["validation_selector"]["ut"] = {
            "mode": "explicit",
            "ids": ["ut3", "ut4"],
        }

        with self.assertRaisesRegex(
            ProfileContractError,
            "profile catalog id mismatch for ut: missing \\['ut3', 'ut4'\\]",
        ):
            resolve_catalog_contract(profile)

    def test_validation_selectors_are_catalog_aware_and_source_recorded(self) -> None:
        profile = load_profile("rich_s4_preview_catalog_gate")
        gate = resolve_catalog_contract(profile)
        ut_sources = gate["selectors"]["ut"]["selected_sources"]

        self.assertEqual(len(ut_sources), 32)
        self.assertEqual(
            ut_sources[0]["selector_source"],
            "profile.validation_selector.ut.catalog-derived-first-n",
        )
        self.assertTrue(ut_sources[0]["catalog_path"].endswith(
            "s4_gz_rich_validation_ut_catalog_32.json"
        ))
        self.assertEqual(ut_sources[0]["id"], "ut2001")

    def test_profile_runner_expands_to_expected_underlying_runner_args(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        gate = resolve_catalog_contract(profile)
        command = build_underlying_command(
            profile=profile,
            catalog_gate=gate,
            run_root=Path("/tmp/skyseed-test-profile"),
            run_id="profile-test",
        )
        rendered = " ".join(command)

        self.assertIn("--planned-shell-id S4", rendered)
        self.assertIn("--max-satellites 348", rendered)
        self.assertIn("--ut-ids " + ",".join(f"ut20{i:02d}" for i in range(1, 33)), rendered)
        self.assertIn("--gateway-ids " + ",".join(f"gw2{i:02d}" for i in range(1, 13)), rendered)
        self.assertIn("--runtime-mode operational_clean", rendered)
        self.assertIn("--forwarding-backend bpf_owned", rendered)
        self.assertIn("--bpf-backend off", rendered)
        self.assertIn("--compose-up-timeout-seconds 900", rendered)
        self.assertIn("--startup-high-progress-grace-ratio 0.95", rendered)
        self.assertIn("--startup-high-progress-grace-seconds 420", rendered)
        self.assertIn("--startup-slow-progress-grace-seconds 180", rendered)
        self.assertIn("--startup-absolute-max-seconds 1500", rendered)
        self.assertIn("--docker-dummy-network-pool 10.128.0.0/9", rendered)
        self.assertIn("--docker-dummy-network-mask 19", rendered)
        self.assertNotIn("ut3,ut4", rendered)
        self.assertNotIn("gw5,gw6,gw7", rendered)
        self.assertNotIn("--bpf-dataplane-canary", rendered)
        self.assertNotIn("--emit-bpf-shadow", rendered)

    def test_capacity_gate_fails_when_pool_is_too_small(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        profile = copy.deepcopy(profile)
        profile["runner_args"]["docker_dummy_network_pool"] = "10.224.0.0/12"
        profile["runner_args"]["docker_dummy_network_mask"] = 16

        gate = docker_dummy_network_capacity_gate(
            profile,
            host_prefixes=[{"source": "test", "prefix": "10.0.2.0/24"}],
            docker_prefixes=[{"source": "test", "prefix": "172.17.0.0/16"}],
        )

        self.assertFalse(gate["ok"])
        self.assertEqual(
            gate["outcome_label"],
            "DOCKER_DUMMY_NETWORK_POOL_CAPACITY_BLOCKER",
        )
        self.assertEqual(gate["required_dummy_networks"], 772)
        self.assertEqual(gate["available_subnets"], 16)
        self.assertIn(
            "required_dummy_networks_exceeds_available_subnets",
            gate["failure_reasons"],
        )

    def test_capacity_gate_passes_for_rich_s4_smoke_profile(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")

        gate = docker_dummy_network_capacity_gate(
            profile,
            host_prefixes=[{"source": "test", "prefix": "10.0.2.0/24"}],
            docker_prefixes=[{"source": "test", "prefix": "172.17.0.0/16"}],
        )

        self.assertTrue(gate["ok"])
        self.assertEqual(gate["required_dummy_networks"], 772)
        self.assertEqual(gate["available_subnets"], 1024)
        self.assertEqual(gate["pool"], "10.128.0.0/9")
        self.assertEqual(gate["mask"], 19)
        self.assertEqual(gate["addresses_per_subnet"], 8192)
        self.assertEqual(gate["host_route_overlaps"], [])
        self.assertEqual(gate["docker_network_overlaps"], [])

    def test_strict_bpf_owned_profiles_reject_canary_experimental_options(self) -> None:
        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        profile = copy.deepcopy(profile)
        profile["runner_args"]["bpf_backend"] = "experimental"
        gate = resolve_catalog_contract(profile)

        with self.assertRaisesRegex(ProfileContractError, "bpf_backend=off"):
            build_underlying_command(
                profile=profile,
                catalog_gate=gate,
                run_root=Path("/tmp/skyseed-test-profile"),
                run_id="profile-test",
            )

        profile = load_profile("rich_s4_operational_clean_bpf_smoke")
        profile = copy.deepcopy(profile)
        profile["runner_boolean_flags"] = {"bpf_dataplane_canary": True}
        gate = resolve_catalog_contract(profile)
        with self.assertRaisesRegex(ProfileContractError, "bpf-dataplane-canary"):
            build_underlying_command(
                profile=profile,
                catalog_gate=gate,
                run_root=Path("/tmp/skyseed-test-profile"),
                run_id="profile-test",
            )


if __name__ == "__main__":
    unittest.main()
