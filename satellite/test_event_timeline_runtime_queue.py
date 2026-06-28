#!/usr/bin/env python3
# encoding: utf-8

import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import patch

SATELLITE_DIR = Path(__file__).resolve().parent
if str(SATELLITE_DIR) not in sys.path:
    sys.path.insert(0, str(SATELLITE_DIR))
APPS_DIR = SATELLITE_DIR / "apps"
if str(APPS_DIR) not in sys.path:
    sys.path.insert(0, str(APPS_DIR))

from apps.run_starlink_shell_experiment import (  # noqa: E402
    _bpf_backend_opted_in,
    _compact_epoch_execution_record,
    _compose_service_metadata,
    _compose_startup_progress_contract,
    _compose_startup_stages,
    _compose_stage_recovery_timeout_seconds,
    _compose_stage_retry_batches,
    _compose_stage_timeout_seconds,
    _continuous_operation_summary,
    _experiment_summary_payload,
    _finalize_runtime_status_fields,
    _ground_endpoint_startup_summary,
    _live_timing_summary,
    _runtime_failure_context_summary,
    _stage_ready_service_names,
    _temporal_execution_queue,
    _wait_for_compose_containers,
)


def _temporal_plan() -> dict:
    return {
        "orbit_samples": [
            {
                "sample_id": "sample-1",
                "sample_index": 1,
                "slice_id": "slice-1",
                "relative_time_seconds": 0.0,
                "absolute_time_utc": "2026-06-21T00:00:00Z",
            },
            {
                "sample_id": "sample-2",
                "sample_index": 2,
                "slice_id": "slice-2",
                "relative_time_seconds": 10.0,
                "absolute_time_utc": "2026-06-21T00:00:10Z",
            },
            {
                "sample_id": "sample-3",
                "sample_index": 3,
                "slice_id": "slice-3",
                "relative_time_seconds": 20.0,
                "absolute_time_utc": "2026-06-21T00:00:20Z",
            },
        ],
        "topology_snapshot_refs": [
            {"slice_id": "slice-1", "snapshot_id": "snap-1"},
            {"slice_id": "slice-2", "snapshot_id": "snap-2"},
            {"slice_id": "slice-3", "snapshot_id": "snap-3"},
        ],
        "runtime_epochs": [
            {
                "epoch_id": "runtime-1",
                "sample_id": "sample-1",
                "slice_id": "slice-1",
                "snapshot_id": "snap-1",
                "activation_relative_time_seconds": 0.0,
                "activation_time_utc": "2026-06-21T00:00:00Z",
                "activation_group_id": "activation-000",
                "deadline_policy": "execute_not_later_than_sample_boundary",
                "trigger_reasons": ["initial_runtime_epoch"],
                "active_link_count": 3,
                "changed_link_ids": ["link-a"],
            }
        ],
        "parameter_epochs": [
            {
                "epoch_id": "parameter-1",
                "sample_id": "sample-3",
                "slice_id": "slice-3",
                "snapshot_id": "snap-3",
                "activation_relative_time_seconds": 20.0,
                "activation_time_utc": "2026-06-21T00:00:20Z",
                "activation_group_id": "activation-020",
                "update_policy": "threshold_or_interval_refresh_without_topology_mutation",
                "trigger_reasons": ["delay_threshold_exceeded"],
                "active_link_count": 3,
                "changed_link_ids": ["link-a"],
            }
        ],
        "event_queue": [
            {
                "event_id": "event-runtime-1",
                "event_type": "runtime_epoch",
                "source_sample_id": "sample-1",
                "slice_id": "slice-1",
                "snapshot_id": "snap-1",
                "activation_relative_time_seconds": 0.0,
                "activation_time_utc": "2026-06-21T00:00:00Z",
                "activation_group_id": "activation-000",
                "execution_policy": "execute_not_later_than_sample_boundary",
                "trigger_reasons": ["initial_runtime_epoch"],
                "linked_epoch_id": "runtime-1",
                "metadata": {"active_link_count": 3, "changed_link_count": 1},
            },
            {
                "event_id": "event-parameter-1",
                "event_type": "parameter_epoch",
                "source_sample_id": "sample-3",
                "slice_id": "slice-3",
                "snapshot_id": "snap-3",
                "activation_relative_time_seconds": 20.0,
                "activation_time_utc": "2026-06-21T00:00:20Z",
                "activation_group_id": "activation-020",
                "execution_policy": "threshold_or_interval_refresh_without_topology_mutation",
                "trigger_reasons": ["delay_threshold_exceeded"],
                "linked_epoch_id": "parameter-1",
                "metadata": {"active_link_count": 3, "changed_link_count": 1},
            },
        ],
        "no_op_sample_count": 1,
        "preparation_plan": {
            "continuous_operation_prepared": {
                "rolling_horizon_replenishment": "prepared_not_yet_live",
                "runtime_epoch_batching_policy": "same_activation_group_only",
            }
        },
        "event_time_summary": {
            "mode": "event_timeline_queue",
            "event_timeline_projection": {
                "event_time_accuracy": {
                    "sample_aligned_event_count": 2,
                    "refined_event_count": 0,
                }
            },
        },
        "preview_update_counters": {
            "physical_sample_count": 3,
            "runtime_transaction_count": 1,
            "parameter_update_batch_count": 1,
            "skipped_no_op_sample_count": 1,
            "sample_aligned_event_count": 2,
            "refined_event_count": 0,
        },
    }


def _summary_args(**overrides: object) -> SimpleNamespace:
    values = {
        "run_mode": "real-runtime",
        "continuous": False,
        "max_satellites": 3,
        "ut_catalog": None,
        "gateway_catalog": None,
        "zone_catalog": None,
        "ut_ids": "",
        "gateway_ids": "",
        "max_host_workers": 0,
        "runtime_mode": "operational_clean",
        "forwarding_backend": "bpf_owned",
        "bpf_dataplane_canary": False,
        "bpf_backend": "off",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _summary_runtime_report(
    *,
    aggregate_runtime_succeeded: bool = True,
    failure_reason: str | None = None,
    timing_summary: dict | None = None,
) -> dict:
    return {
        "finalized": True,
        "mode": "real-runtime",
        "timing_summary": dict(timing_summary or {}),
        "temporal_audit": {"source": "event_timeline"},
        "iterations": [],
        "prepared_bundle_runtime": {
            "source": "event_timeline",
            "physical_sample_count": 3,
            "runtime_transaction_count": 1,
            "parameter_update_batch_count": 1,
            "skipped_noop_sample_count": 1,
            "sample_aligned_event_count": 2,
            "refined_event_count": 0,
            "epoch_execution_queue": [],
        },
        "outcome": {
            "real_apply_success": True,
            "ping_success": True,
            "rollback_completed": True,
            "event_timeline_execution_success": True,
            "pre_rollback_connectivity_success": True,
            "rollback_success": True,
            "post_run_preflight_clean": True,
            "aggregate_runtime_succeeded": aggregate_runtime_succeeded,
            "failure_reason": failure_reason,
            "linux_gz_fallback_enabled": False,
            "linux_gz_fallback_command_count": 0,
            "eligible_core_fallback_to_linux": 0,
            "bpf_classifier_miss_count": 0,
            "bpf_ping_path_hit_count": 2,
            "bpf_return_handoff_hit_count": 1,
        },
    }


def _summary_payload_for_test(
    *,
    args: SimpleNamespace | None = None,
    runtime_report: dict | None = None,
    runtime_blocker: dict | None = None,
    timing_analysis: dict | None = None,
    bpf_dataplane_canary: dict | None = None,
) -> dict:
    temporal_plan = _temporal_plan()
    return _experiment_summary_payload(
        args=args or _summary_args(),
        run_id="run-1",
        run_dir=Path("/tmp/run-1"),
        preview_summary={},
        temporal_payload=temporal_plan,
        selected_runtime_slice_ids=["slice-1"],
        selected_parameter_slice_ids=["slice-2"],
        preview_gate={},
        runtime_report=runtime_report,
        runtime_blocker=runtime_blocker,
        watcher_summary={},
        phase_timing={},
        timing_analysis=timing_analysis or {},
        compose_audit={},
        startup_strategy=None,
        mini_internet_summary={},
        continuous_operation_summary={},
        slot_capacity_audit={},
        data_provenance={},
        artifacts={"real_runtime_report_json": "/tmp/run-1/real_runtime_report.json"},
        preflight_report=None,
        host_prepare_report=None,
        post_run_preflight_report={"clean_after_run": True},
        bpf_dataplane_canary=bpf_dataplane_canary,
    )


class EventTimelineRuntimeQueueTests(unittest.TestCase):
    def test_helper_compose_stage_timeout_scales_for_network_creation_and_s4_helper_count(self) -> None:
        self.assertEqual(
            _compose_stage_timeout_seconds(
                remaining_timeout=0.0,
                stage_type="helper",
                service_count=2,
            ),
            90.0,
        )
        self.assertEqual(
            _compose_stage_timeout_seconds(
                remaining_timeout=0.0,
                stage_type="helper",
                service_count=34,
            ),
            234.0,
        )
        self.assertEqual(
            _compose_stage_recovery_timeout_seconds(
                stage_type="helper",
                service_count=29,
            ),
            194.0,
        )

    def test_endpoint_compose_stage_timeout_scales_with_endpoint_count(self) -> None:
        self.assertEqual(
            _compose_stage_timeout_seconds(
                remaining_timeout=0.0,
                stage_type="endpoint",
                service_count=8,
            ),
            60.0,
        )
        self.assertEqual(
            _compose_stage_timeout_seconds(
                remaining_timeout=0.0,
                stage_type="endpoint",
                service_count=44,
            ),
            240.0,
        )
        self.assertEqual(
            _compose_stage_recovery_timeout_seconds(
                stage_type="endpoint",
                service_count=19,
            ),
            129.0,
        )

    def test_progress_contract_allows_high_progress_grace(self) -> None:
        contract = _compose_startup_progress_contract(
            base_timeout_seconds=900.0,
            elapsed_seconds=895.0,
            completed_batches=80,
            total_batches=81,
            seconds_since_progress=2.0,
            no_progress_stall_seconds=120.0,
            high_progress_grace_ratio=0.95,
            high_progress_grace_seconds=420.0,
            slow_progress_grace_seconds=180.0,
            absolute_max_seconds=1500.0,
            expected_stage_watchdog_seconds=90.0,
        )

        self.assertTrue(contract["allow_continue"])
        self.assertEqual(contract["classification"], "high_progress_grace")
        self.assertEqual(contract["outcome_label"], "SLOW_BUT_PROGRESSING_CONTINUE")
        self.assertTrue(contract["base_budget_pressure"])
        self.assertGreater(contract["remaining_contract_seconds"], 0.0)

    def test_progress_contract_blocks_no_progress_startup_stall(self) -> None:
        contract = _compose_startup_progress_contract(
            base_timeout_seconds=900.0,
            elapsed_seconds=100.0,
            completed_batches=0,
            total_batches=81,
            seconds_since_progress=130.0,
            no_progress_stall_seconds=120.0,
            high_progress_grace_ratio=0.95,
            high_progress_grace_seconds=420.0,
            slow_progress_grace_seconds=180.0,
            absolute_max_seconds=1500.0,
            expected_stage_watchdog_seconds=90.0,
        )

        self.assertFalse(contract["allow_continue"])
        self.assertEqual(contract["classification"], "no_progress_stall")
        self.assertEqual(contract["outcome_label"], "NO_PROGRESS_STARTUP_STALL")

    def test_progressing_startup_near_end_continues_after_soft_budget(self) -> None:
        contract = _compose_startup_progress_contract(
            base_timeout_seconds=900.0,
            elapsed_seconds=1090.0,
            completed_batches=83,
            total_batches=84,
            seconds_since_progress=1.0,
            no_progress_stall_seconds=120.0,
            high_progress_grace_ratio=0.95,
            high_progress_grace_seconds=0.0,
            slow_progress_grace_seconds=180.0,
            absolute_max_seconds=1500.0,
            expected_stage_watchdog_seconds=90.0,
            container_count_increased=True,
            current_stage_changed=True,
        )

        self.assertTrue(contract["allow_continue"])
        self.assertEqual(contract["classification"], "high_progress_continue")
        self.assertTrue(contract["soft_budget_exceeded"])
        self.assertEqual(contract["outcome_label"], "SLOW_BUT_PROGRESSING_CONTINUE")
        self.assertTrue(contract["high_progress_continue"])
        self.assertTrue(contract["progress_observed"])
        self.assertFalse(contract["no_progress_stall"])
        self.assertFalse(contract["absolute_max_exceeded"])
        self.assertGreaterEqual(contract["remaining_contract_seconds"], 400.0)
        self.assertEqual(
            _compose_stage_timeout_seconds(
                remaining_timeout=0.0,
                stage_type="satellite_batch",
                service_count=12,
            ),
            90.0,
        )

    def test_progress_contract_blocks_absolute_startup_cap(self) -> None:
        contract = _compose_startup_progress_contract(
            base_timeout_seconds=900.0,
            elapsed_seconds=1501.0,
            completed_batches=71,
            total_batches=72,
            seconds_since_progress=1.0,
            no_progress_stall_seconds=120.0,
            high_progress_grace_ratio=0.95,
            high_progress_grace_seconds=420.0,
            slow_progress_grace_seconds=180.0,
            absolute_max_seconds=1500.0,
            expected_stage_watchdog_seconds=90.0,
            container_count_increased=True,
            current_stage_changed=True,
        )

        self.assertFalse(contract["allow_continue"])
        self.assertEqual(contract["classification"], "startup_hard_cap_exceeded")
        self.assertEqual(
            contract["outcome_label"],
            "STARTUP_HARD_CAP_EXCEEDED",
        )
        self.assertTrue(contract["absolute_max_exceeded"])
        self.assertTrue(contract["hard_cap_exceeded"])
        self.assertEqual(contract["kill_reason"], "startup_hard_cap_exceeded")

    def test_soft_budget_exceeded_with_recent_progress_warns_without_kill(self) -> None:
        contract = _compose_startup_progress_contract(
            base_timeout_seconds=900.0,
            elapsed_seconds=930.0,
            completed_batches=12,
            total_batches=84,
            seconds_since_progress=3.0,
            no_progress_stall_seconds=120.0,
            high_progress_grace_ratio=0.95,
            high_progress_grace_seconds=0.0,
            slow_progress_grace_seconds=0.0,
            absolute_max_seconds=1500.0,
            expected_stage_watchdog_seconds=90.0,
            container_count_increased=True,
        )

        self.assertTrue(contract["allow_continue"])
        self.assertTrue(contract["soft_budget_exceeded"])
        self.assertTrue(contract["progress_observed"])
        self.assertEqual(contract["classification"], "high_progress_continue")
        self.assertEqual(contract["outcome_label"], "SLOW_BUT_PROGRESSING_CONTINUE")
        self.assertIsNone(contract["kill_reason"])

    def test_endpoint_stage_retry_uses_single_service_batches(self) -> None:
        self.assertEqual(
            _compose_stage_retry_batches(
                service_names=[f"ut{i}" for i in range(1, 4)],
                stage_type="endpoint",
            ),
            [
                ["ut1"],
                ["ut2"],
                ["ut3"],
            ],
        )

    def test_helper_stage_retry_uses_parallel_batches(self) -> None:
        self.assertEqual(
            _compose_stage_retry_batches(
                service_names=[f"helper{i}" for i in range(1, 7)],
                stage_type="helper",
            ),
            [
                ["helper1", "helper2", "helper3", "helper4"],
                ["helper5", "helper6"],
            ],
        )

    def test_large_compose_splits_helpers_and_ground_endpoints_into_batches(self) -> None:
        compose_audit = {
            "service_names_by_type": {
                "helper": [
                    "0123456789abcdef0123456789abcdef",
                    *[f"ut{i}-placeholder-rtr" for i in range(1, 33)],
                ],
                "ix": [],
                "mini_internet": [],
                "gateway": [f"gw{i}" for i in range(1, 13)],
                "ut": [f"ut{i}" for i in range(1, 33)],
                "satellite": [],
            }
        }
        service_name_by_container = {
            "seed_output_0123456789abcdef0123456789abcdef_1": (
                "0123456789abcdef0123456789abcdef"
            ),
            **{
                f"ut{i}-placeholder-rtr": f"rnode_250_ut{i}-placeholder-rtr"
                for i in range(1, 33)
            },
            **{f"gw{i}": f"gw-service-{i}" for i in range(1, 13)},
            **{f"ut{i}": f"ut-service-{i}" for i in range(1, 33)},
        }

        stages = _compose_startup_stages(
            compose_audit=compose_audit,
            service_name_by_container=service_name_by_container,
            mode="infra-then-sat-batches",
            satellite_batch_size=24,
        )

        self.assertEqual(stages[0]["stage_id"], "helpers-one-shot")
        helper_stages = [
            stage
            for stage in stages
            if str(stage.get("stage_id")).startswith("helpers-placeholder")
        ]
        self.assertEqual(
            [len(stage["service_names"]) for stage in helper_stages],
            [8, 8, 8, 8],
        )
        gateway_stages = [
            stage
            for stage in stages
            if str(stage.get("stage_id")).startswith("ground-gateways")
        ]
        self.assertEqual(len(gateway_stages), 12)
        self.assertTrue(all(stage["endpoint_kind"] == "gateway" for stage in gateway_stages))
        self.assertTrue(all(len(stage["service_names"]) == 1 for stage in gateway_stages))
        ut_stages = [
            stage for stage in stages if str(stage.get("stage_id")).startswith("ground-uts")
        ]
        self.assertEqual(len(ut_stages), 32)
        self.assertTrue(all(len(stage["service_names"]) == 1 for stage in ut_stages))

    def test_satellite_startup_batches_respect_compose_parallel_limit(self) -> None:
        stages = _compose_startup_stages(
            compose_audit={
                "service_names_by_type": {
                    "helper": [],
                    "ix": [],
                    "mini_internet": [],
                    "gateway": [],
                    "ut": [],
                    "satellite": [f"stl-{index:06d}" for index in range(1, 25)],
                }
            },
            service_name_by_container={},
            mode="infra-then-sat-batches",
            satellite_batch_size=24,
            compose_parallel_limit=8,
        )

        self.assertEqual(
            [len(stage["service_names"]) for stage in stages],
            [8, 8, 8],
        )

    def test_helper_stage_accepts_successful_seed_one_shot(self) -> None:
        snapshot = {
            "containers": {
                "seed_output_0123456789abcdef0123456789abcdef_1": {
                    "state": "exited",
                    "exit_code": 0,
                    "error": "",
                },
                "ut2001-placeholder-rtr": {
                    "state": "running",
                    "exit_code": None,
                    "error": "",
                },
                "ut2002-placeholder-rtr": {
                    "state": "exited",
                    "exit_code": 0,
                    "error": "",
                },
                "seed_output_fedcba9876543210fedcba9876543210_1": {
                    "state": "exited",
                    "exit_code": 1,
                    "error": "",
                },
            }
        }

        ready = _stage_ready_service_names(
            compose_service_snapshot=snapshot,
            stage_services=[
                "helper-one-shot",
                "rnode_250_ut2001-placeholder-rtr",
                "rnode_250_ut2002-placeholder-rtr",
                "failed-helper-one-shot",
            ],
            service_name_by_container={
                "seed_output_0123456789abcdef0123456789abcdef_1": "helper-one-shot",
                "ut2001-placeholder-rtr": "rnode_250_ut2001-placeholder-rtr",
                "ut2002-placeholder-rtr": "rnode_250_ut2002-placeholder-rtr",
                "seed_output_fedcba9876543210fedcba9876543210_1": "failed-helper-one-shot",
            },
        )

        self.assertEqual(
            ready,
            ["helper-one-shot", "rnode_250_ut2001-placeholder-rtr"],
        )

    def test_endpoint_stage_rejects_successful_one_shot_container(self) -> None:
        snapshot = {
            "containers": {
                "seed_output_0123456789abcdef0123456789abcdef_1": {
                    "state": "exited",
                    "exit_code": 0,
                    "error": "",
                }
            }
        }

        ready = _stage_ready_service_names(
            compose_service_snapshot=snapshot,
            stage_services=["hnode_250_ut2001"],
            service_name_by_container={
                "seed_output_0123456789abcdef0123456789abcdef_1": (
                    "hnode_250_ut2001"
                ),
            },
            helper_service_names=[],
        )

        self.assertEqual(ready, [])

    def test_ground_endpoint_summary_reports_missing_and_recovery_attempts(self) -> None:
        snapshot = {
            "containers": {
                "ut2001": {
                    "state": "running",
                    "status": "running",
                    "created_at": "2026-06-22T10:00:00.000000000Z",
                    "started_at": "2026-06-22T10:00:04.500000000Z",
                },
                "ut2002": {
                    "state": "missing",
                },
            }
        }

        with patch(
            "apps.run_starlink_shell_experiment._compose_service_logs_tail",
            return_value={"returncode": 1, "stdout": "", "stderr": "missing"},
        ):
            summary = _ground_endpoint_startup_summary(
                output_dir=Path("/tmp/no-such-compose"),
                stage_services=["hnode_250_ut2001", "hnode_250_ut2002"],
                compose_service_snapshot=snapshot,
                service_name_by_container={
                    "ut2001": "hnode_250_ut2001",
                    "ut2002": "hnode_250_ut2002",
                },
                initially_ready_service_names=[],
                retry_results=[
                    {
                        "service_names": ["hnode_250_ut2001", "hnode_250_ut2002"],
                        "timed_out": True,
                        "timeout_seconds": 30.0,
                    }
                ],
            )

        self.assertEqual(summary["ground_endpoint_expected_count"], 2)
        self.assertEqual(summary["ground_endpoint_up_count"], 1)
        self.assertEqual(summary["ground_endpoint_missing_count"], 1)
        self.assertEqual(summary["ground_endpoint_recovered_count"], 1)
        self.assertEqual(summary["readiness_timeout_reason"], "missing_long_running_endpoints")
        self.assertEqual(
            summary["per_service_startup_seconds"]["hnode_250_ut2001"],
            4.5,
        )

    def test_container_readiness_accepts_successful_seed_one_shot(self) -> None:
        one_shot = "seed_output_0123456789abcdef0123456789abcdef_1"

        def fake_run(command, **_kwargs):
            if command[:3] == ["docker", "ps", "-a"]:
                return CompletedProcess(
                    command,
                    0,
                    stdout=(
                        "ut2001\tUp 1 second\n"
                        f"{one_shot}\tExited (0) 1 second ago\n"
                    ),
                    stderr="",
                )
            raise AssertionError(command)

        with patch("apps.run_starlink_shell_experiment.subprocess.run", side_effect=fake_run):
            readiness = _wait_for_compose_containers(
                container_names=["ut2001", one_shot],
                timeout_seconds=0.0,
            )

        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["attempts"][0]["missing_containers"], [])
        self.assertEqual(readiness["attempts"][0]["ready_one_shot_containers"], [one_shot])

    def test_container_readiness_requires_explicit_one_shot_helper_name(self) -> None:
        one_shot = "seed_output_0123456789abcdef0123456789abcdef_1"

        def fake_run(command, **_kwargs):
            if command[:3] == ["docker", "ps", "-a"]:
                return CompletedProcess(
                    command,
                    0,
                    stdout=(
                        "ut2001\tUp 1 second\n"
                        f"{one_shot}\tExited (0) 1 second ago\n"
                    ),
                    stderr="",
                )
            raise AssertionError(command)

        with patch("apps.run_starlink_shell_experiment.subprocess.run", side_effect=fake_run):
            readiness = _wait_for_compose_containers(
                container_names=["ut2001", one_shot],
                timeout_seconds=0.01,
                poll_interval_seconds=0.0,
                helper_container_names=[],
            )

        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["attempts"][-1]["missing_containers"], [one_shot])

    def test_compose_metadata_tracks_generated_container_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "run-specific-output"
            output_dir.mkdir()
            (output_dir / ".env").write_text(
                "COMPOSE_PROJECT_NAME=seed_output\n",
                encoding="utf-8",
            )
            (output_dir / "docker-compose.yml").write_text(
                "\n".join(
                    [
                        "version: '2.4'",
                        "services:",
                        "  0123456789abcdef0123456789abcdef:",
                        "    image: helper-image",
                        "  explicit-service:",
                        "    image: explicit-image",
                        "    container_name: explicit-container",
                        "networks: {}",
                    ]
                ),
                encoding="utf-8",
            )

            metadata = _compose_service_metadata(output_dir)

        self.assertIn(
            "seed_output_0123456789abcdef0123456789abcdef_1",
            metadata["container_names"],
        )
        self.assertIn("explicit-container", metadata["container_names"])
        self.assertEqual(
            metadata["service_name_by_container"][
                "seed_output_0123456789abcdef0123456789abcdef_1"
            ],
            "0123456789abcdef0123456789abcdef",
        )

    def test_real_runtime_queue_is_sourced_from_event_timeline(self) -> None:
        temporal_plan = _temporal_plan()

        frames, queue, runtime_slices, parameter_slices = _temporal_execution_queue(
            temporal_plan,
            max_epochs=None,
            max_runtime_epochs=None,
            max_parameter_epochs=None,
        )

        self.assertEqual(runtime_slices, ["slice-1"])
        self.assertEqual(parameter_slices, ["slice-3"])
        self.assertEqual(len(frames), 3)
        self.assertEqual(len(queue), 2)
        self.assertTrue(all(entry["source"] == "event_timeline" for entry in queue))
        self.assertEqual(queue[0]["event_timeline_type"], "runtime_transaction")
        self.assertEqual(queue[1]["event_timeline_type"], "parameter_update_batch")
        self.assertEqual(queue[0]["physical_sample_count"], 3)
        self.assertEqual(queue[0]["runtime_transaction_count"], 1)
        self.assertEqual(queue[0]["parameter_update_batch_count"], 1)
        self.assertEqual(queue[0]["skipped_noop_sample_count"], 1)
        self.assertEqual(queue[0]["sample_aligned_event_count"], 2)
        self.assertEqual(queue[0]["refined_event_count"], 0)

    def test_continuous_operation_summary_reports_event_timeline_counts(self) -> None:
        temporal_plan = _temporal_plan()
        _, queue, runtime_slices, parameter_slices = _temporal_execution_queue(
            temporal_plan,
            max_epochs=None,
            max_runtime_epochs=None,
            max_parameter_epochs=None,
        )

        summary = _continuous_operation_summary(
            temporal_plan=temporal_plan,
            all_sample_frames=[],
            selected_epoch_queue=queue,
            selected_runtime_slice_ids=runtime_slices,
            selected_parameter_slice_ids=parameter_slices,
        )

        self.assertEqual(summary["source"], "event_timeline")
        self.assertEqual(summary["physical_sample_count"], 3)
        self.assertEqual(summary["runtime_transaction_count"], 1)
        self.assertEqual(summary["parameter_update_batch_count"], 1)
        self.assertEqual(summary["skipped_noop_sample_count"], 1)

    def test_run_manifest_summary_exposes_event_timeline_counts_at_top_level(self) -> None:
        temporal_plan = _temporal_plan()
        _, queue, runtime_slices, parameter_slices = _temporal_execution_queue(
            temporal_plan,
            max_epochs=None,
            max_runtime_epochs=None,
            max_parameter_epochs=None,
        )
        args = _summary_args()
        runtime_report = {
            "finalized": True,
            "mode": "real-runtime",
            "timing_summary": {},
            "temporal_audit": {"source": "event_timeline"},
            "iterations": [],
            "prepared_bundle_runtime": {
                "source": "event_timeline",
                "physical_sample_count": 3,
                "runtime_transaction_count": 1,
                "parameter_update_batch_count": 1,
                "skipped_noop_sample_count": 1,
                "sample_aligned_event_count": 2,
                "refined_event_count": 0,
                "epoch_execution_queue": queue,
            },
            "outcome": {
                "event_timeline_execution_success": True,
                "pre_rollback_connectivity_success": True,
                "rollback_success": True,
                "post_run_preflight_clean": True,
                "aggregate_runtime_succeeded": True,
                "linux_gz_fallback_enabled": False,
                "linux_gz_fallback_command_count": 0,
                "eligible_core_fallback_to_linux": 0,
                "bpf_classifier_miss_count": 0,
                "bpf_ping_path_hit_count": 2,
                "bpf_return_handoff_hit_count": 1,
            },
        }

        manifest = _experiment_summary_payload(
            args=args,
            run_id="run-1",
            run_dir=Path("/tmp/run-1"),
            preview_summary={},
            temporal_payload=temporal_plan,
            selected_runtime_slice_ids=runtime_slices,
            selected_parameter_slice_ids=parameter_slices,
            preview_gate={},
            runtime_report=runtime_report,
            runtime_blocker=None,
            watcher_summary={},
            phase_timing={},
            timing_analysis={},
            compose_audit={},
            startup_strategy=None,
            mini_internet_summary={},
            continuous_operation_summary={},
            slot_capacity_audit={},
            data_provenance={},
            artifacts={"real_runtime_report_json": "/tmp/run-1/real_runtime_report.json"},
            preflight_report=None,
            host_prepare_report=None,
            post_run_preflight_report={"clean_after_run": True},
        )

        self.assertEqual(manifest["source"], "event_timeline")
        self.assertEqual(manifest["physical_sample_count"], 3)
        self.assertEqual(manifest["runtime_transaction_count"], 1)
        self.assertEqual(manifest["parameter_update_batch_count"], 1)
        self.assertEqual(manifest["skipped_noop_sample_count"], 1)
        self.assertEqual(manifest["sample_aligned_event_count"], 2)
        self.assertEqual(manifest["refined_event_count"], 0)
        self.assertEqual(manifest["temporal_summary"]["source"], "event_timeline")

    def test_compact_execution_record_preserves_event_timeline_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            detail_jsonl = Path(temp_dir) / "details.jsonl"
            compact = _compact_epoch_execution_record(
                {
                    "sequence_index": 1,
                    "source": "event_timeline",
                    "event_timeline_type": "runtime_transaction",
                    "source_event_id": "event-runtime-1",
                    "source_event_type": "runtime_epoch",
                    "physical_sample_count": 3,
                    "runtime_transaction_count": 1,
                    "parameter_update_batch_count": 1,
                    "skipped_noop_sample_count": 1,
                    "sample_aligned_event_count": 2,
                    "refined_event_count": 0,
                    "event_timeline_execution_success": True,
                    "pre_rollback_connectivity_success": True,
                    "rollback_success": True,
                    "post_run_preflight_clean": True,
                    "aggregate_runtime_succeeded": True,
                    "linux_gz_fallback_enabled": False,
                    "linux_gz_fallback_command_count": 0,
                    "eligible_core_fallback_to_linux": 0,
                    "bpf_classifier_miss_count": 0,
                    "bpf_ping_path_hit_count": 4,
                    "bpf_return_handoff_hit_count": 2,
                    "epoch_id": "runtime-1",
                    "epoch_type": "runtime_epoch",
                    "timing": {},
                    "outcome": {"success": True},
                },
                detail_jsonl=detail_jsonl,
            )

        self.assertEqual(compact["source"], "event_timeline")
        self.assertEqual(compact["event_timeline_type"], "runtime_transaction")
        self.assertEqual(compact["physical_sample_count"], 3)
        self.assertEqual(compact["runtime_transaction_count"], 1)
        self.assertEqual(compact["parameter_update_batch_count"], 1)
        self.assertIs(compact["event_timeline_execution_success"], True)
        self.assertIs(compact["pre_rollback_connectivity_success"], True)
        self.assertIs(compact["rollback_success"], True)
        self.assertIs(compact["post_run_preflight_clean"], True)
        self.assertIs(compact["aggregate_runtime_succeeded"], True)
        self.assertIs(compact["linux_gz_fallback_enabled"], False)
        self.assertEqual(compact["linux_gz_fallback_command_count"], 0)
        self.assertEqual(compact["eligible_core_fallback_to_linux"], 0)
        self.assertEqual(compact["bpf_classifier_miss_count"], 0)
        self.assertEqual(compact["bpf_ping_path_hit_count"], 4)
        self.assertEqual(compact["bpf_return_handoff_hit_count"], 2)

    def test_finalized_runtime_status_fields_propagate_to_artifacts(self) -> None:
        runtime_report = {
            "finalized": True,
            "final_cleanup": {"all_cleanup_success": True},
            "final_runtime_connectivity_validation": {
                "validation_phase": "pre_rollback",
                "validation_scope": "reapplied_gateway_class_sample",
                "primary_only": True,
                "ping_success": True,
            },
            "iterations": [
                {
                    "sequence_index": 1,
                    "source": "event_timeline",
                    "event_timeline_type": "runtime_transaction",
                    "outcome": {"success": True},
                    "linux_gz_fallback_enabled": False,
                    "linux_gz_fallback_command_count": 0,
                    "eligible_core_fallback_to_linux": 0,
                    "bpf_classifier_miss_count": 0,
                    "bpf_ping_path_hit_count": 2,
                    "bpf_return_handoff_hit_count": 1,
                }
            ],
            "prepared_bundle_runtime": {
                "source": "event_timeline",
                "epoch_execution_queue": [
                    {
                        "sequence_index": 1,
                        "source": "event_timeline",
                        "event_timeline_type": "runtime_transaction",
                        "outcome": {"success": True},
                        "linux_gz_fallback_enabled": False,
                        "linux_gz_fallback_command_count": 0,
                        "eligible_core_fallback_to_linux": 0,
                        "bpf_classifier_miss_count": 0,
                        "bpf_ping_path_hit_count": 2,
                        "bpf_return_handoff_hit_count": 1,
                    }
                ],
            },
            "temporal_audit": {"source": "event_timeline"},
            "outcome": {"failure_reason": None, "rollback_completed": True},
        }

        finalized = _finalize_runtime_status_fields(
            runtime_report,
            {"clean_after_run": True},
        )

        for container in (
            finalized,
            finalized["outcome"],
            finalized["temporal_audit"],
            finalized["iterations"][0],
            finalized["prepared_bundle_runtime"]["epoch_execution_queue"][0],
        ):
            self.assertIs(container["event_timeline_execution_success"], True)
            self.assertIs(container["pre_rollback_connectivity_success"], True)
            self.assertIs(container["rollback_success"], True)
            self.assertIs(container["post_run_preflight_clean"], True)
            self.assertIs(container["aggregate_runtime_succeeded"], True)
            self.assertIs(container["linux_gz_fallback_enabled"], False)
            self.assertEqual(container["linux_gz_fallback_command_count"], 0)
            self.assertEqual(container["eligible_core_fallback_to_linux"], 0)
            self.assertEqual(container["bpf_classifier_miss_count"], 0)
            self.assertEqual(container["bpf_ping_path_hit_count"], 2)
            self.assertEqual(container["bpf_return_handoff_hit_count"], 1)

    def test_finalized_runtime_status_preserves_strict_violation_counters(self) -> None:
        failure_reason = (
            "bpf_owned_strict_contract_violation:"
            "eligible G/Z scope must be BPF-owned with no Linux fallback; "
            "readiness_reason=bpf_mpls_map_key_conflict; "
            "eligible_core_fallback_to_linux=902; "
            "linux_gz_fallback_command_count=902; "
            "backend_apply_fallback_to_linux=902"
        )
        runtime_report = {
            "finalized": True,
            "final_cleanup": {"all_cleanup_success": True},
            "final_runtime_connectivity_validation": {},
            "iterations": [],
            "prepared_bundle_runtime": {
                "source": "event_timeline",
                "epoch_execution_queue": [],
            },
            "temporal_audit": {"source": "event_timeline"},
            "failure_context": {
                "exception": {
                    "message": failure_reason,
                    "skyseed_details": {
                        "backend_apply_contract": {
                            "linux_gz_fallback_command_count": 902,
                            "eligible_core_fallback_to_linux": 902,
                        }
                    },
                }
            },
            "outcome": {"failure_reason": failure_reason, "rollback_completed": True},
        }

        finalized = _finalize_runtime_status_fields(
            runtime_report,
            {"clean_after_run": True},
        )

        for container in (
            finalized,
            finalized["outcome"],
            finalized["temporal_audit"],
        ):
            self.assertIs(container["linux_gz_fallback_enabled"], True)
            self.assertEqual(container["linux_gz_fallback_command_count"], 902)
            self.assertEqual(container["eligible_core_fallback_to_linux"], 902)
            self.assertIs(container["aggregate_runtime_succeeded"], False)

    def test_finalized_runtime_status_preserves_failure_miss_drop_counters(self) -> None:
        failure_reason = (
            "bpf_owned_epoch_validation_miss_drop; "
            "backend_apply_skipped_by_bpf=592; "
            "backend_apply_fallback_to_linux=0; "
            "backend_apply_requested_commands=782"
        )
        runtime_report = {
            "finalized": True,
            "final_cleanup": {"all_cleanup_success": True},
            "final_runtime_connectivity_validation": {},
            "iterations": [],
            "prepared_bundle_runtime": {
                "source": "event_timeline",
                "epoch_execution_queue": [],
            },
            "temporal_audit": {"source": "event_timeline"},
            "failure_context": {
                "exception": {
                    "message": failure_reason,
                    "skyseed_details": {
                        "backend_apply_contract": {
                            "linux_gz_fallback_command_count": 0,
                            "eligible_core_fallback_to_linux": 0,
                        },
                        "bpf_owned_packet_path_diagnostics": {
                            "bpf_classifier_miss_count": 146,
                            "bpf_ping_path_hit_count": 12,
                            "bpf_return_handoff_hit_count": 12,
                            "miss_drop_counters": {
                                "miss": 73,
                                "drop": 73,
                                "drop_missing_label": 0,
                                "drop_missing_generation": 0,
                                "drop_bad_packet": 0,
                            },
                        },
                    },
                }
            },
            "outcome": {"failure_reason": failure_reason, "rollback_completed": True},
        }

        finalized = _finalize_runtime_status_fields(
            runtime_report,
            {"clean_after_run": True},
        )

        for container in (
            finalized,
            finalized["outcome"],
            finalized["temporal_audit"],
        ):
            self.assertIs(container["linux_gz_fallback_enabled"], False)
            self.assertEqual(container["linux_gz_fallback_command_count"], 0)
            self.assertEqual(container["eligible_core_fallback_to_linux"], 0)
            self.assertEqual(container["bpf_classifier_miss_count"], 146)
            self.assertEqual(container["bpf_ping_path_hit_count"], 12)
            self.assertEqual(container["bpf_return_handoff_hit_count"], 12)
            self.assertEqual(
                container["bpf_miss_drop_counters"],
                {
                    "miss": 73,
                    "drop": 73,
                    "drop_missing_label": 0,
                    "drop_missing_generation": 0,
                    "drop_bad_packet": 0,
                },
            )
            self.assertIs(container["aggregate_runtime_succeeded"], False)

    def test_runtime_failure_context_summary_preserves_first_failure_bpf_counters(self) -> None:
        runtime_report = {
            "failure_context": {
                "exception": {
                    "message": "bpf_owned_epoch_validation_miss_drop; cleanup_success=True",
                    "skyseed_details": {
                        "bpf_owned_packet_path_diagnostics": {
                            "classifier_hit": 12,
                            "label_push": 12,
                            "egress_handoff_hit": 12,
                            "label_pop": 12,
                            "core_lfib_hit": 0,
                            "label_swap": 0,
                            "bpf_classifier_miss_count": 272,
                            "miss_drop_counters": {
                                "miss": 136,
                                "drop": 136,
                            },
                        }
                    },
                }
            },
            "outcome": {"failure_reason": None},
        }

        summary = _runtime_failure_context_summary(runtime_report)

        self.assertEqual(
            summary["classified_blocker"],
            "bpf_owned_epoch_validation_miss_drop",
        )
        self.assertEqual(
            summary["bpf_first_failure_counters"]["bpf_classifier_miss_count"],
            272,
        )
        self.assertEqual(
            summary["bpf_first_failure_counters"]["miss_drop_counters"],
            {"miss": 136, "drop": 136},
        )

    def test_operational_clean_cost_sanity_flags_steady_full_attach(self) -> None:
        summary = _live_timing_summary(
            [
                {
                    "epoch_type": "runtime_epoch",
                    "epoch_id": "initial",
                    "timing": {"semantic_breakdown": {"network_update_seconds": 5.0}},
                },
                {
                    "epoch_type": "runtime_epoch",
                    "epoch_id": "steady",
                    "bpf_attach_reuse": {
                        "available": True,
                        "reuse_eligible": False,
                        "reuse_rejected_reason": "map_reload_failed_before_full_prepare",
                        "attach_strategy": "dirty_recovery_reset",
                        "full_attach_count": 12,
                        "delta_attach_count": 0,
                        "reused_attach_count": 0,
                        "interface_set_changed": False,
                        "steady_epoch_map_only": False,
                    },
                    "timing": {
                        "semantic_breakdown": {
                            "network_update_seconds": 5.0,
                            "post_apply_overhead_seconds": 1.0,
                            "unknown_blocking_seconds": 0.0,
                        }
                    },
                },
            ],
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        cost = summary["cost_sanity"]
        self.assertFalse(cost["passed"])
        self.assertEqual(cost["conclusion"], "STEADY_UPDATE_COST_ANOMALY")
        self.assertTrue(cost["broad_rerun_blocked"])

    def test_operational_clean_cost_sanity_flags_cleanup_cost(self) -> None:
        summary = _live_timing_summary(
            [
                {
                    "epoch_type": "runtime_epoch",
                    "epoch_id": "initial",
                    "timing": {
                        "semantic_breakdown": {
                            "network_update_seconds": 5.0,
                            "runtime_incremental_cleanup_seconds": 0.0,
                        }
                    },
                },
                {
                    "epoch_type": "runtime_epoch",
                    "epoch_id": "steady",
                    "bpf_attach_reuse": {
                        "available": True,
                        "reuse_eligible": True,
                        "attach_strategy": "reuse_attached_bpf_map_reload",
                        "full_attach_count": 0,
                        "delta_attach_count": 0,
                        "reused_attach_count": 10,
                        "interface_set_changed": False,
                        "steady_epoch_map_only": True,
                    },
                    "timing": {
                        "semantic_breakdown": {
                            "network_update_seconds": 5.0,
                            "runtime_incremental_cleanup_seconds": 400.0,
                            "post_apply_overhead_seconds": 1.0,
                            "unknown_blocking_seconds": 0.0,
                        }
                    },
                },
            ],
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        self.assertEqual(summary["cost_sanity"]["conclusion"], "CLEANUP_COST_ANOMALY")

    def test_operational_clean_cost_sanity_flags_command_cardinality(self) -> None:
        summary = _live_timing_summary(
            [
                {
                    "epoch_type": "runtime_epoch",
                    "epoch_id": "initial",
                    "timing": {"semantic_breakdown": {"network_update_seconds": 5.0}},
                },
                {
                    "epoch_type": "runtime_epoch",
                    "epoch_id": "steady",
                    "bpf_attach_reuse": {
                        "available": True,
                        "reuse_eligible": True,
                        "attach_strategy": "reuse_attached_bpf_map_reload",
                        "full_attach_count": 0,
                        "delta_attach_count": 0,
                        "reused_attach_count": 10,
                        "interface_set_changed": False,
                        "steady_epoch_map_only": True,
                    },
                    "host_operation_timing_summary": {
                        "operation_counts": {"qdisc_delete": 9},
                    },
                    "timing": {
                        "semantic_breakdown": {
                            "network_update_seconds": 5.0,
                            "post_apply_overhead_seconds": 1.0,
                            "unknown_blocking_seconds": 0.0,
                        }
                    },
                },
            ],
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        self.assertEqual(
            summary["cost_sanity"]["conclusion"],
            "COMMAND_CARDINALITY_ANOMALY",
        )

    def test_experiment_summary_cost_sanity_blocks_runtime_pass(self) -> None:
        runtime_report = _summary_runtime_report(
            timing_summary={
                "cost_sanity": {
                    "schema_version": "skyseed-cost-sanity-v1",
                    "applicable": True,
                    "passed": False,
                    "conclusion": "COMMAND_CARDINALITY_ANOMALY",
                    "anomaly_count": 1,
                    "broad_rerun_blocked": True,
                    "anomaly_table": [
                        {
                            "phase": "command_cardinality",
                            "expected_order": "steady map-only update",
                            "actual": {"steady_qdisc_delete_count": 9},
                            "anomaly": True,
                            "suspected_module": "command planner",
                            "next_action": "inspect command cardinality",
                            "conclusion": "COMMAND_CARDINALITY_ANOMALY",
                        }
                    ],
                }
            },
        )

        manifest = _summary_payload_for_test(runtime_report=runtime_report)

        self.assertIs(manifest["aggregate_runtime_succeeded"], False)
        self.assertEqual(
            manifest["failure_reason"],
            "cost_sanity_failed:COMMAND_CARDINALITY_ANOMALY",
        )
        self.assertEqual(
            manifest["runtime_blocker"]["classified_blocker"],
            "COMMAND_CARDINALITY_ANOMALY",
        )
        self.assertEqual(
            manifest["cost_sanity_blocker"]["stage"],
            "cost_sanity",
        )
        self.assertTrue(manifest["broad_rerun_blocked"])

    def test_experiment_summary_warns_startup_cost_anomaly_without_failing_runtime(
        self,
    ) -> None:
        runtime_report = _summary_runtime_report()
        manifest = _summary_payload_for_test(
            runtime_report=runtime_report,
            timing_analysis={
                "startup_preparation": {
                    "cold_start_before_first_epoch_execution_wall_seconds": 1200.0,
                    "ready_to_execute_epochs_wall_seconds": 1190.0,
                    "compose_up_wall_seconds": 900.0,
                    "container_readiness_wall_seconds": 200.0,
                }
            },
        )

        self.assertIs(manifest["aggregate_runtime_succeeded"], True)
        self.assertEqual(
            manifest["cost_sanity_conclusion"],
            "STARTUP_COST_ANOMALY",
        )
        self.assertIsNone(manifest["runtime_blocker"])
        self.assertIsNone(manifest["cost_sanity_blocker"])
        self.assertTrue(manifest["broad_rerun_blocked"])
        self.assertEqual(manifest["cost_sanity"]["blocking_anomaly_count"], 0)
        self.assertEqual(manifest["cost_sanity"]["warning_only_anomaly_count"], 1)
        self.assertFalse(
            manifest["cost_sanity"]["policy"][
                "startup_cost_anomaly_blocks_runtime_pass"
            ]
        )

    def test_experiment_summary_preserves_semantic_blocker_when_cost_fails(self) -> None:
        runtime_report = _summary_runtime_report(
            aggregate_runtime_succeeded=False,
            failure_reason="operational_clean_bpf_map_reload_failed",
            timing_summary={
                "cost_sanity": {
                    "schema_version": "skyseed-cost-sanity-v1",
                    "applicable": True,
                    "passed": False,
                    "conclusion": "CLEANUP_COST_ANOMALY",
                    "anomaly_count": 1,
                    "broad_rerun_blocked": True,
                    "anomaly_table": [
                        {
                            "phase": "cleanup",
                            "expected_order": "bounded cleanup",
                            "actual": {"total_seconds": 400.0},
                            "anomaly": True,
                            "suspected_module": "cleanup",
                            "next_action": "inspect cleanup",
                            "conclusion": "CLEANUP_COST_ANOMALY",
                        }
                    ],
                }
            },
        )
        semantic_blocker = {
            "stage": "bpf_hotpath_map_reload",
            "classified_blocker": "operational_clean_bpf_map_reload_failed",
        }

        manifest = _summary_payload_for_test(
            runtime_report=runtime_report,
            runtime_blocker=semantic_blocker,
        )

        self.assertEqual(
            manifest["runtime_blocker"]["classified_blocker"],
            "operational_clean_bpf_map_reload_failed",
        )
        self.assertEqual(
            manifest["cost_sanity_blocker"]["classified_blocker"],
            "CLEANUP_COST_ANOMALY",
        )
        self.assertTrue(manifest["broad_rerun_blocked"])

    def test_bpf_experimental_backend_is_explicit_diagnostic_only(self) -> None:
        self.assertFalse(_bpf_backend_opted_in(_summary_args()))
        self.assertTrue(_bpf_backend_opted_in(_summary_args(bpf_dataplane_canary=True)))
        self.assertTrue(_bpf_backend_opted_in(_summary_args(bpf_backend="experimental")))

        manifest = _summary_payload_for_test(
            args=_summary_args(bpf_dataplane_canary=True),
            runtime_report=_summary_runtime_report(),
            bpf_dataplane_canary={"ok": True, "metrics": {}},
        )

        self.assertEqual(
            manifest["bpf_experimental_backend_contract"],
            {
                "diagnostic_only": True,
                "authoritative_for_runtime_pass": False,
                "explicit_opt_in": True,
                "executed": True,
            },
        )
        self.assertIs(manifest["aggregate_runtime_succeeded"], True)

    def test_bpf_experimental_success_cannot_make_strict_runtime_pass(self) -> None:
        manifest = _summary_payload_for_test(
            args=_summary_args(bpf_dataplane_canary=True),
            runtime_report=_summary_runtime_report(
                aggregate_runtime_succeeded=False,
                failure_reason="operational_clean_bpf_map_reload_failed",
            ),
            bpf_dataplane_canary={"ok": True, "metrics": {"loaded_gz_fec_count": 8}},
        )

        self.assertIs(manifest["aggregate_runtime_succeeded"], False)
        self.assertEqual(
            manifest["failure_reason"],
            "operational_clean_bpf_map_reload_failed",
        )
        self.assertFalse(
            manifest["bpf_experimental_backend_contract"][
                "authoritative_for_runtime_pass"
            ]
        )


if __name__ == "__main__":
    unittest.main()
