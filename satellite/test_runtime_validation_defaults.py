#!/usr/bin/env python3
# encoding: utf-8

import inspect
import sys
import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from linux_mpls_runtime import (
    ActiveRuntimeSession,
    DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE,
    MinimalRealLinuxMplsInstaller,
    _bpf_mpls_generation_map_capacities,
    _bpf_owned_attach_reuse_plan,
    _bpf_owned_prepare_mpls_backend,
    _bpf_clsact_lifecycle_metrics,
    _bpf_owned_interface_role_completeness,
    _bpf_owned_merge_resident_attach_coverage,
    _bpf_owned_reused_attach_clsact_guard,
    _bpf_owned_runtime_readiness_failure_message,
    _copy_bpf_prepare_timing_to_phase_timing,
    _prepare_bpf_owned_readiness,
    _select_runtime_ping_validation_contexts,
)
from iteration2_satellite_experiment import _warmup_ut_ids_for_transition
from network_runtime import DirectNetnsVethSlotLinkNamespaceQdiscInstaller
from run_starlink_shell_experiment import parse_args
from run_starlink_shell_experiment import (
    _await_initial_runtime_readiness_for_args,
    _bpf_attach_reuse_epoch_summary,
    _bpf_hotpath_reuse_audit,
    _cleanup_bpf_mpls_pins_after_runtime,
    _compact_bpf_real_backend_prepare,
    _compact_node_local_execution,
    _epoch_execution_detail_jsonl_record,
    _epoch_execution_runtime_report_record,
    _final_cleanup_timing_summary,
    _interface_inventory_activation_contract,
    _live_timing_summary,
    _lifecycle_aware_horizon_event_contract,
    _map_only_horizon_boundary_contract,
    _operational_clean_final_connectivity_validation_summary,
    _operational_clean_node_local_execution_summary,
    _operational_clean_heartbeat_summary,
    _operational_clean_bpf_owned_initial_readiness_attempt,
    _operational_clean_bounded_cleanup_session,
    _operational_clean_pre_start_audit,
    _parameter_batch_reclassification,
    _parameter_update_batch_accounting,
    _parameter_update_category,
    _preflight_profile_for_args,
    _run_invocation_metadata,
    _runtime_failure_context_summary,
    _should_prepare_host_mpls_for_args,
    _should_skip_host_rollback_for_args,
    _should_run_parameter_epoch_validation_for_args,
    _should_run_runtime_epoch_validation_for_args,
    _static_inventory_materialization_contract,
    _static_inventory_materialization_resource_scope_by_slot,
    _merge_resource_scope_by_slot,
    _skipped_packet_observation_summary,
    _slot_capacity_audit,
    _update_host_cleanup_history,
)
from skyseed_preflight import (  # noqa: E402
    PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED,
)


class RuntimeValidationDefaultTests(unittest.TestCase):
    def test_operational_clean_bpf_owned_initial_readiness_skips_legacy_route_gate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            satellite_dir = Path(tmp) / "satellite"
            output_dir = satellite_dir / "output"
            output_dir.mkdir(parents=True)
            (output_dir / "skyseed_real_runtime_active.lock").write_text("{}", encoding="utf-8")

            with (
                patch(
                    "run_starlink_shell_experiment.SATELLITE_DIR",
                    satellite_dir,
                ),
                patch(
                    "run_starlink_shell_experiment._docker_exec_readiness_probe",
                    return_value={
                        "ok": True,
                        "returncode": 0,
                        "stdout": "",
                        "stderr": "",
                    },
                ),
                patch(
                    "run_starlink_shell_experiment.direct_backend_host_artifacts_report",
                    return_value={"ok": True, "interfaces": []},
                ),
            ):
                attempt = _operational_clean_bpf_owned_initial_readiness_attempt(
                    gateway_ids=("gw201",),
                    validation_targets=(
                        {"node_id": "as154-host-new", "ip_address": "10.154.0.129"},
                    ),
                    mini_internet_node_prefix="starlinkexp-",
                )

        status_by_name = {
            str(check["name"]): str(check["status"])
            for check in attempt["subchecks"]
        }
        block_by_name = {
            str(check["name"]): bool(check["blocks_runtime"])
            for check in attempt["subchecks"]
        }

        self.assertTrue(attempt["ready"])
        self.assertEqual(
            status_by_name["legacy_gateway_linux_route_to_validation_targets"],
            "skipped",
        )
        self.assertFalse(block_by_name["legacy_gateway_linux_route_to_validation_targets"])
        self.assertEqual(status_by_name["pinned_maps"], "not_reached")
        self.assertFalse(block_by_name["pinned_maps"])
        self.assertEqual(
            status_by_name["runtime_gateway_container_command_channels"],
            "passed",
        )
        self.assertEqual(
            status_by_name["validation_target_container_command_channels"],
            "passed",
        )

    def test_operational_clean_bpf_owned_initial_readiness_uses_profile_gate(
        self,
    ) -> None:
        args = SimpleNamespace(
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
            mini_internet_node_prefix="starlinkexp-",
            initial_runtime_readiness_timeout_seconds=30.0,
        )

        with (
            patch(
                "run_starlink_shell_experiment._await_operational_clean_bpf_owned_initial_readiness",
                return_value={"ready": True, "readiness_profile": "operational_clean_bpf_owned_pre_epoch"},
            ) as bpf_gate,
            patch(
                "run_starlink_shell_experiment._await_initial_real_runtime_readiness",
            ) as legacy_gate,
        ):
            result = _await_initial_runtime_readiness_for_args(
                args=args,
                gateway_ids=("gw201",),
                validation_targets=(
                    {"node_id": "as154-host-new", "ip_address": "10.154.0.129"},
                ),
            )

        self.assertTrue(result["ready"])
        bpf_gate.assert_called_once()
        legacy_gate.assert_not_called()

    def test_scoped_prewarm_does_not_prepare_scope_external_candidates(self) -> None:
        installer = DirectNetnsVethSlotLinkNamespaceQdiscInstaller()
        slot = {"slot_name": "slot-a", "slot_type": "same_plane_isl"}
        resources = {
            "resource-a": {"resource_key": "resource-a"},
            "resource-b": {"resource_key": "resource-b"},
        }

        with patch.object(installer, "_candidate_resources", return_value=resources):
            self.assertEqual(
                installer._prepared_resources_for_slot(
                    slot=slot,
                    prepared_resource_scope_by_slot=None,
                ),
                resources,
            )
            self.assertEqual(
                installer._prepared_resources_for_slot(
                    slot=slot,
                    prepared_resource_scope_by_slot={"other-slot": {"resource-a"}},
                ),
                {},
            )
            self.assertEqual(
                installer._prepared_resources_for_slot(
                    slot=slot,
                    prepared_resource_scope_by_slot={"slot-a": {"resource-b"}},
                ),
                {"resource-b": resources["resource-b"]},
            )

    def test_cli_and_installer_share_runtime_validation_scope_default(self) -> None:
        with patch.object(sys, "argv", ["run_starlink_shell_experiment.py"]):
            args = parse_args()

        signature = inspect.signature(MinimalRealLinuxMplsInstaller.apply_and_validate)

        self.assertEqual(
            args.runtime_epoch_validation_scope,
            DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE,
        )
        self.assertEqual(
            signature.parameters["ping_validation_scope"].default,
            DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE,
        )

    def test_operational_clean_runtime_mode_is_explicit_cli_switch(self) -> None:
        with patch.object(
            sys,
            "argv",
            ["run_starlink_shell_experiment.py", "--runtime-mode", "operational_clean"],
        ):
            args = parse_args()

        self.assertEqual(args.runtime_mode, "operational_clean")

    def test_bpf_prepare_phase_timing_preserves_update_counts(self) -> None:
        phase_timing = {}

        _copy_bpf_prepare_timing_to_phase_timing(
            phase_timing,
            {
                "bpf_reuse_audit_seconds": "0.001",
                "bpf_map_compile_seconds": 0.002,
                "bpf_map_write_seconds": 0.5,
                "active_generation_flip_seconds": 0.03,
                "bpf_attach_seconds": 0.0,
                "bpf_live_role_read_seconds": 0.0,
                "bpf_map_write_update_count": "9",
                "bpf_map_update_count": 10,
                "active_generation_update_count": 1,
            },
        )

        self.assertEqual(phase_timing["bpf_map_write_seconds"], 0.5)
        self.assertEqual(phase_timing["active_generation_flip_seconds"], 0.03)
        self.assertEqual(phase_timing["bpf_map_write_update_count"], 9)
        self.assertEqual(phase_timing["bpf_map_update_count"], 10)
        self.assertEqual(phase_timing["active_generation_update_count"], 1)

    def test_operational_clean_bpf_owned_uses_clean_preflight_without_mpls_prepare(
        self,
    ) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "run_starlink_shell_experiment.py",
                "--runtime-mode",
                "operational_clean",
                "--forwarding-backend",
                "bpf_owned",
            ],
        ):
            args = parse_args()

        self.assertEqual(
            _preflight_profile_for_args(args),
            PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED,
        )
        self.assertFalse(_should_prepare_host_mpls_for_args(args))

    def test_stop_after_pre_start_gates_is_explicit_audit_mode(self) -> None:
        with patch.object(sys, "argv", ["run_starlink_shell_experiment.py"]):
            default_args = parse_args()
        with patch.object(
            sys,
            "argv",
            [
                "run_starlink_shell_experiment.py",
                "--stop-after-pre-start-gates",
            ],
        ):
            audit_args = parse_args()

        self.assertFalse(default_args.stop_after_pre_start_gates)
        self.assertTrue(audit_args.stop_after_pre_start_gates)

    def test_warmup_ut_selection_ignores_non_mapping_bindings(self) -> None:
        transition = {
            "host_plan": SimpleNamespace(
                operations=[
                    {
                        "slot_type": "ut_access",
                        "operation": "activate_slot_attachment",
                        "source_binding": 0,
                        "target_binding": {"ut_id": "ut101"},
                    },
                    {
                        "slot_type": "ut_access",
                        "operation": "rebind_slot_attachment",
                        "source_binding": {"ut_id": "ut102"},
                        "target_binding": 1,
                    },
                ]
            )
        }

        self.assertEqual(
            _warmup_ut_ids_for_transition(transition),
            ["ut101", "ut102"],
        )

    def test_operational_clean_disables_first_epoch_validation(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "run_starlink_shell_experiment.py",
                "--runtime-mode",
                "operational_clean",
                "--runtime-epoch-validation-interval",
                "0",
                "--parameter-epoch-validation-interval",
                "0",
            ],
        ):
            clean_args = parse_args()
        with patch.object(
            sys,
            "argv",
            [
                "run_starlink_shell_experiment.py",
                "--runtime-mode",
                "correctness",
                "--runtime-epoch-validation-interval",
                "0",
                "--parameter-epoch-validation-interval",
                "0",
            ],
        ):
            correctness_args = parse_args()

        self.assertFalse(
            _should_run_runtime_epoch_validation_for_args(
                args=clean_args,
                runtime_count=0,
            )
        )
        self.assertFalse(
            _should_run_parameter_epoch_validation_for_args(
                args=clean_args,
                parameter_count=0,
            )
        )
        self.assertTrue(
            _should_run_runtime_epoch_validation_for_args(
                args=correctness_args,
                runtime_count=0,
            )
        )
        self.assertTrue(
            _should_run_parameter_epoch_validation_for_args(
                args=correctness_args,
                parameter_count=0,
            )
        )

    def test_operational_clean_final_connectivity_policy_is_skipped(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "run_starlink_shell_experiment.py",
                "--runtime-mode",
                "operational_clean",
            ],
        ):
            args = parse_args()

        summary = _operational_clean_final_connectivity_validation_summary(args)

        self.assertTrue(summary["validation_skipped"])
        self.assertTrue(summary["ping_success"])
        self.assertEqual(summary["wall_seconds"], 0.0)
        self.assertEqual(
            summary["skip_reason"],
            "operational_clean_disables_final_connectivity_validation",
        )

    def test_operational_clean_pre_start_audit_passes_clean_defaults(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "run_starlink_shell_experiment.py",
                "--run-mode",
                "real-runtime",
                "--runtime-mode",
                "operational_clean",
                "--forwarding-backend",
                "bpf_owned",
            ],
        ):
            args = parse_args()

        audit = _operational_clean_pre_start_audit(
            args=args,
            slot_capacity_audit={
                "passed": True,
                "classified_blocker": "",
                "deficit_by_type": {},
                "deficit_types": [],
            },
            preview_gate={
                "all_preview_valid": True,
                "all_real_supported": True,
            },
        )

        self.assertTrue(audit["passed"])
        self.assertEqual(audit["classified_blocker"], "")
        self.assertEqual(audit["blockers"], [])
        self.assertTrue(
            audit["checks"][
                "runtime_first_validation_skipped_when_interval_zero"
            ]["passed"]
        )
        self.assertIn("bpf_attach_seconds", audit["required_timing_fields"])
        self.assertIn(
            "bpf_resident_attach_added_interface_count",
            audit["required_timing_fields"],
        )
        self.assertIn(
            "bpf_extra_previous_interface_count",
            audit["required_timing_fields"],
        )
        self.assertIn(
            "first_failure_diagnostics",
            audit["required_timing_summary_fields"],
        )
        self.assertTrue(
            audit["checks"]["timing_summary_required_fields_present"]["passed"]
        )
        self.assertIn(
            "qdisc_wall_seconds",
            audit["required_parameter_accounting_fields"],
        )

    def test_operational_clean_pre_start_audit_blocks_non_bpf_backend(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "run_starlink_shell_experiment.py",
                "--run-mode",
                "real-runtime",
                "--runtime-mode",
                "operational_clean",
                "--forwarding-backend",
                "linux_mpls",
            ],
        ):
            args = parse_args()

        audit = _operational_clean_pre_start_audit(
            args=args,
            slot_capacity_audit={
                "passed": True,
                "classified_blocker": "",
                "deficit_by_type": {},
                "deficit_types": [],
            },
            preview_gate={
                "all_preview_valid": True,
                "all_real_supported": True,
            },
        )

        self.assertFalse(audit["passed"])
        self.assertEqual(audit["classified_blocker"], "BPF_HOTPATH_STILL_BLOCKED")
        self.assertIn(
            "BPF_HOTPATH_STILL_BLOCKED",
            [blocker["blocker"] for blocker in audit["blockers"]],
        )

    def test_operational_clean_pre_start_audit_classifies_timing_schema_gap(
        self,
    ) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "run_starlink_shell_experiment.py",
                "--run-mode",
                "real-runtime",
                "--runtime-mode",
                "operational_clean",
                "--forwarding-backend",
                "bpf_owned",
            ],
        ):
            args = parse_args()

        audit = _operational_clean_pre_start_audit(
            args=args,
            slot_capacity_audit={
                "passed": True,
                "classified_blocker": "",
                "deficit_by_type": {},
                "deficit_types": [],
            },
            preview_gate={
                "all_preview_valid": True,
                "all_real_supported": True,
            },
            required_timing_fields=[""],
        )

        self.assertFalse(audit["passed"])
        self.assertEqual(audit["classified_blocker"], "TIMING_SCHEMA_STILL_BLOCKED")

        summary_gap_audit = _operational_clean_pre_start_audit(
            args=args,
            slot_capacity_audit={
                "passed": True,
                "classified_blocker": "",
                "deficit_by_type": {},
                "deficit_types": [],
            },
            preview_gate={
                "all_preview_valid": True,
                "all_real_supported": True,
            },
            required_timing_summary_fields=[""],
        )

        self.assertFalse(summary_gap_audit["passed"])
        self.assertEqual(
            summary_gap_audit["classified_blocker"],
            "TIMING_SCHEMA_STILL_BLOCKED",
        )

    def test_map_only_horizon_boundary_detects_unsafe_future_mpls_activation(
        self,
    ) -> None:
        interface_ref = "netns:stl-940120:xp120a"
        contract = _map_only_horizon_boundary_contract(
            interface_horizon_snapshots=[
                {
                    "slice_id": "starlink-shell-slice-0004",
                    "future_non_materialized_capacity_only_interfaces": [
                        interface_ref
                    ],
                    "resident_attach_coverage": {
                        "interface_classifications": [
                            {
                                "interface_ref": interface_ref,
                                "classification": (
                                    "FUTURE_NON_MATERIALIZED_CAPACITY_ONLY"
                                ),
                                "expected_roles": ["g_to_z_core_lfib"],
                            }
                        ]
                    },
                },
                {
                    "slice_id": "starlink-shell-slice-0005",
                    "epoch_id": "runtimeepoch-959af428d54bbb5b",
                    "active_materialized_interfaces": [interface_ref],
                    "attached_interface_details": [
                        {
                            "interface_ref": interface_ref,
                            "expected_roles": ["g_to_z_core_lfib"],
                        }
                    ],
                    "semantic_safety_proof_by_interface": {
                        interface_ref: {
                            "fail_closed_proof": True,
                            "bpf_attached": True,
                            "qdisc_clsact_attached": True,
                        }
                    },
                },
            ],
            selected_epoch_queue=[
                {"slice_id": "starlink-shell-slice-0004"},
                {"slice_id": "starlink-shell-slice-0005"},
            ],
            strict_map_only=True,
        )

        self.assertFalse(contract["passed"])
        self.assertTrue(contract["boundary_reached"])
        self.assertEqual(
            contract["classified_blocker"],
            "ATTACH_HORIZON_BOUNDARY_REACHED",
        )
        self.assertEqual(
            contract["lifecycle_classification"],
            "EXPLICIT_LIFECYCLE_MATERIALIZATION_REQUIRED",
        )
        self.assertIn(
            "tc_bpf_non_gz_ip_pass_allows_unclassified_ipv4",
            contract["first_boundary"]["semantic_safety"]["blockers"],
        )

    def test_operational_clean_pre_start_audit_blocks_before_boundary(
        self,
    ) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "run_starlink_shell_experiment.py",
                "--run-mode",
                "real-runtime",
                "--runtime-mode",
                "operational_clean",
                "--forwarding-backend",
                "bpf_owned",
            ],
        ):
            args = parse_args()
        boundary_contract = {
            "boundary_reached": True,
            "classified_blocker": "ATTACH_HORIZON_BOUNDARY_REACHED",
            "first_boundary": {
                "interface_ref": "netns:stl-940120:xp120a",
                "classification": "EXPLICIT_LIFECYCLE_MATERIALIZATION_REQUIRED",
            },
        }

        audit = _operational_clean_pre_start_audit(
            args=args,
            slot_capacity_audit={
                "passed": True,
                "classified_blocker": "",
                "deficit_by_type": {},
                "deficit_types": [],
            },
            preview_gate={
                "all_preview_valid": True,
                "all_real_supported": True,
            },
            map_only_horizon_boundary_contract=boundary_contract,
        )

        self.assertFalse(audit["passed"])
        self.assertEqual(
            audit["classified_blocker"],
            "ATTACH_HORIZON_BOUNDARY_REACHED",
        )
        self.assertIn(
            "ATTACH_HORIZON_BOUNDARY_REACHED",
            [blocker["blocker"] for blocker in audit["blockers"]],
        )

    def test_packet_miss_is_not_first_cause_when_boundary_is_known(self) -> None:
        boundary_contract = {
            "boundary_reached": True,
            "classified_blocker": "ATTACH_HORIZON_BOUNDARY_REACHED",
            "first_semantic_cause": (
                "ATTACH_HORIZON_BOUNDARY/"
                "EXPLICIT_LIFECYCLE_MATERIALIZATION_REQUIRED"
            ),
            "packet_miss_authoritative": False,
            "delta_attach_failure_authoritative": False,
        }
        with patch.object(
            sys,
            "argv",
            [
                "run_starlink_shell_experiment.py",
                "--run-mode",
                "real-runtime",
                "--runtime-mode",
                "operational_clean",
                "--forwarding-backend",
                "bpf_owned",
            ],
        ):
            args = parse_args()

        audit = _operational_clean_pre_start_audit(
            args=args,
            slot_capacity_audit={
                "passed": True,
                "classified_blocker": "",
                "deficit_by_type": {},
                "deficit_types": [],
            },
            preview_gate={
                "all_preview_valid": True,
                "all_real_supported": True,
            },
            map_only_horizon_boundary_contract=boundary_contract,
        )

        self.assertEqual(
            audit["classified_blocker"],
            "ATTACH_HORIZON_BOUNDARY_REACHED",
        )
        self.assertFalse(
            audit["map_only_horizon_boundary_contract"][
                "packet_miss_authoritative"
            ]
        )
        self.assertFalse(
            audit["map_only_horizon_boundary_contract"][
                "delta_attach_failure_authoritative"
            ]
        )

    def test_safe_classifier_future_interface_can_be_preattach_eligible(
        self,
    ) -> None:
        interface_ref = "netns:stl-940058:ua-u2028s0"
        contract = _map_only_horizon_boundary_contract(
            interface_horizon_snapshots=[
                {
                    "slice_id": "starlink-shell-slice-0004",
                    "future_non_materialized_capacity_only_interfaces": [
                        interface_ref
                    ],
                },
                {
                    "slice_id": "starlink-shell-slice-0005",
                    "active_materialized_interfaces": [interface_ref],
                    "attached_interface_details": [
                        {
                            "interface_ref": interface_ref,
                            "expected_roles": ["z_to_g_classifier"],
                        }
                    ],
                    "semantic_safety_proof_by_interface": {
                        interface_ref: {
                            "fail_closed_proof": True,
                            "bpf_attached": True,
                            "qdisc_clsact_attached": True,
                            "active_generation_entries_present": False,
                            "classifier_key_present_for_active_generation": False,
                            "linux_bridge_forwarding": False,
                            "linux_ip_route": False,
                            "linux_mpls_route": False,
                        }
                    },
                },
            ],
            strict_map_only=True,
        )

        self.assertTrue(contract["passed"])
        self.assertFalse(contract["boundary_reached"])
        self.assertEqual(contract["safe_preattach_eligible_count"], 1)
        self.assertEqual(
            contract["safe_preattach_eligible_interfaces"][0]["classification"],
            "PREATTACH_ELIGIBLE_FAIL_CLOSED",
        )

    def test_lifecycle_required_profile_reports_boundary_without_map_only_pass(
        self,
    ) -> None:
        interface_ref = "netns:stl-940178:xp120b"
        contract = _map_only_horizon_boundary_contract(
            interface_horizon_snapshots=[
                {
                    "slice_id": "starlink-shell-slice-0004",
                    "future_non_materialized_capacity_only_interfaces": [
                        interface_ref
                    ],
                },
                {
                    "slice_id": "starlink-shell-slice-0005",
                    "active_materialized_interfaces": [interface_ref],
                    "attached_interface_details": [
                        {
                            "interface_ref": interface_ref,
                            "expected_roles": ["z_to_g_core_lfib"],
                        }
                    ],
                },
            ],
            strict_map_only=False,
            lifecycle_capable=True,
        )

        self.assertTrue(contract["passed"])
        self.assertTrue(contract["boundary_reached"])
        self.assertEqual(contract["classified_blocker"], "")
        self.assertEqual(
            contract["lifecycle_classification"],
            "EXPLICIT_LIFECYCLE_MATERIALIZATION_REQUIRED",
        )
        self.assertFalse(contract["map_only_resident_reload"])

    def test_lifecycle_aware_horizon_classifies_every_selected_event(self) -> None:
        interface_ref = "netns:stl-940120:xp120a"
        contract = _lifecycle_aware_horizon_event_contract(
            selected_epoch_queue=[
                {
                    "slice_id": "starlink-shell-slice-0004",
                    "classification": "runtime_epoch",
                    "epoch_id": "runtimeepoch-map",
                },
                {
                    "slice_id": "starlink-shell-slice-param",
                    "classification": "parameter_epoch",
                    "epoch_id": "parameterepoch-1",
                },
                {
                    "slice_id": "starlink-shell-slice-0005",
                    "classification": "runtime_epoch",
                    "epoch_id": "runtimeepoch-life",
                },
            ],
            interface_horizon_snapshots=[
                {
                    "slice_id": "starlink-shell-slice-0004",
                    "active_materialized_interfaces": ["netns:gw205:ix204"],
                    "future_non_materialized_capacity_only_interfaces": [
                        interface_ref
                    ],
                },
                {
                    "slice_id": "starlink-shell-slice-0005",
                    "active_materialized_interfaces": [
                        "netns:gw205:ix204",
                        interface_ref,
                    ],
                    "attached_interface_details": [
                        {
                            "interface_ref": interface_ref,
                            "expected_roles": ["g_to_z_core_lfib"],
                        }
                    ],
                },
            ],
            cleanup_phases=[
                {
                    "phase": "final_external_residue_check",
                    "classification": "cleanup_finalizer",
                }
            ],
        )

        self.assertEqual(contract["selected_event_count"], 3)
        self.assertEqual(contract["classified_event_count"], 3)
        self.assertEqual(
            [event["primary_class"] for event in contract["event_reports"]],
            ["map_only_update", "parameter_update", "lifecycle_materialization"],
        )
        self.assertEqual(contract["cleanup_finalizer_phase_count"], 1)
        self.assertEqual(
            contract["event_reports"][1]["transaction_boundaries"][
                "cleanup_finalizer_phase"
            ],
            [],
        )

    def test_lifecycle_horizon_distinguishes_transaction_sub_stages(self) -> None:
        interface_ref = "netns:stl-940178:xp120b"
        contract = _lifecycle_aware_horizon_event_contract(
            selected_epoch_queue=[
                {
                    "slice_id": "starlink-shell-slice-0004",
                    "classification": "runtime_epoch",
                    "epoch_id": "runtimeepoch-before",
                },
                {
                    "slice_id": "starlink-shell-slice-0005",
                    "classification": "runtime_epoch",
                    "epoch_id": "runtimeepoch-boundary",
                },
            ],
            interface_horizon_snapshots=[
                {
                    "slice_id": "starlink-shell-slice-0004",
                    "future_non_materialized_capacity_only_interfaces": [
                        interface_ref
                    ],
                    "resident_attach_coverage": {
                        "interface_classifications": [
                            {
                                "interface_ref": interface_ref,
                                "classification": (
                                    "FUTURE_NON_MATERIALIZED_CAPACITY_ONLY"
                                ),
                                "expected_roles": ["z_to_g_core_lfib"],
                            }
                        ]
                    },
                },
                {
                    "slice_id": "starlink-shell-slice-0005",
                    "active_materialized_interfaces": [interface_ref],
                    "attached_interface_details": [
                        {
                            "interface_ref": interface_ref,
                            "expected_roles": [
                                "z_to_g_core_lfib",
                                "z_to_g_egress_handoff",
                            ],
                        }
                    ],
                    "bpf_attach_interfaces": [interface_ref],
                    "role_update_interfaces": [interface_ref],
                },
            ],
        )

        boundary_event = contract["event_reports"][1]
        self.assertEqual(boundary_event["primary_class"], "lifecycle_materialization")
        self.assertEqual(
            boundary_event["transaction_boundaries"]["sub_stages"],
            ["materialize", "attach", "role_update", "map_load", "flip"],
        )
        self.assertFalse(boundary_event["counted_as_map_only_pass"])
        self.assertEqual(contract["map_only_pass_count"], 1)
        self.assertEqual(contract["lifecycle_epoch_count"], 1)

    def test_lifecycle_boundary_precedes_packet_miss_and_delta_attach_failure(
        self,
    ) -> None:
        interface_ref = "netns:stl-940120:xp120a"
        contract = _lifecycle_aware_horizon_event_contract(
            selected_epoch_queue=[
                {
                    "slice_id": "starlink-shell-slice-0004",
                    "classification": "runtime_epoch",
                },
                {
                    "slice_id": "starlink-shell-slice-0005",
                    "classification": "runtime_epoch",
                },
            ],
            interface_horizon_snapshots=[
                {
                    "slice_id": "starlink-shell-slice-0004",
                    "future_non_materialized_capacity_only_interfaces": [
                        interface_ref
                    ],
                },
                {
                    "slice_id": "starlink-shell-slice-0005",
                    "active_materialized_interfaces": [interface_ref],
                    "attached_interface_details": [
                        {
                            "interface_ref": interface_ref,
                            "expected_roles": ["g_to_z_core_lfib"],
                        }
                    ],
                    "semantic_safety_proof_by_interface": {
                        interface_ref: {
                            "fail_closed_proof": True,
                            "bpf_attached": True,
                            "qdisc_clsact_attached": True,
                        }
                    },
                },
            ],
        )

        self.assertEqual(
            contract["first_semantic_cause"],
            "ATTACH_HORIZON_BOUNDARY/EXPLICIT_LIFECYCLE_MATERIALIZATION_REQUIRED",
        )
        self.assertFalse(contract["packet_miss_authoritative"])
        self.assertFalse(contract["delta_attach_failure_authoritative"])
        self.assertFalse(contract["whole_horizon_completed"])
        self.assertFalse(contract["ready_for_next_horizon"])

    def test_pre_start_audit_can_use_lifecycle_horizon_boundary(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "run_starlink_shell_experiment.py",
                "--run-mode",
                "real-runtime",
                "--runtime-mode",
                "operational_clean",
                "--forwarding-backend",
                "bpf_owned",
            ],
        ):
            args = parse_args()

        audit = _operational_clean_pre_start_audit(
            args=args,
            slot_capacity_audit={
                "passed": True,
                "classified_blocker": "",
                "deficit_by_type": {},
                "deficit_types": [],
            },
            preview_gate={
                "all_preview_valid": True,
                "all_real_supported": True,
            },
            lifecycle_aware_horizon_contract={
                "first_known_lifecycle_boundary": {"slice_id": "slice-5"},
                "first_semantic_cause": (
                    "ATTACH_HORIZON_BOUNDARY/"
                    "EXPLICIT_LIFECYCLE_MATERIALIZATION_REQUIRED"
                ),
            },
        )

        self.assertFalse(audit["passed"])
        self.assertEqual(
            audit["classified_blocker"],
            "ATTACH_HORIZON_BOUNDARY_REACHED",
        )

    def test_operational_clean_skipped_packet_observation_is_auditable(self) -> None:
        summary = _skipped_packet_observation_summary(
            observe_selectors=["ut2001:10.154.0.129"],
            skip_reason="operational_clean_disables_sync_packet_observation",
        )

        self.assertFalse(summary["enabled"])
        self.assertTrue(summary["observation_skipped"])
        self.assertEqual(
            summary["skip_reason"],
            "operational_clean_disables_sync_packet_observation",
        )
        self.assertEqual(summary["observe_selectors"], ["ut2001:10.154.0.129"])

    def test_operational_clean_node_summary_omits_heavy_result_lists(self) -> None:
        session = SimpleNamespace(
            session_id="session-1",
            backend={"forwarding_backend": "bpf_owned"},
            target_slice={
                "slice_id": "slice-1",
                "active_flow_count": 4,
                "reapplied_flow_ids": ["f1"],
                "held_flow_ids": ["f2"],
            },
            preflight={
                "backend_apply_contract": {"linux_gz_fallback_command_count": 0},
                "bpf_owned_packet_counters_after_apply": {
                    "miss_drop_counters": {"miss": 0, "drop": 0}
                },
            },
            timing={"apply_wall_seconds": 1.5, "flow_path_observations": [{"x": 1}]},
            apply_results=[{"large": "payload"}],
            verify_results=[{"large": "payload"}],
            ping_results=[{"success": True}],
        )

        summary = _operational_clean_node_local_execution_summary(session)

        self.assertEqual(summary["session_id"], "session-1")
        self.assertEqual(summary["apply_result_count"], 1)
        self.assertNotIn("apply_results", summary)
        self.assertNotIn("verify_results", summary)
        self.assertNotIn("ping_results", summary)
        self.assertNotIn("flow_path_observations", summary["timing"])

    def test_operational_clean_epoch_jsonl_record_is_compact(self) -> None:
        record = {
            "sequence_index": 7,
            "epoch_type": "runtime_epoch",
            "segment_epoch_key": "seg:slice",
            "timing": {"transition_wall_seconds": 1.2},
            "outcome": {"success": True},
            "parameter_batch_accounting": {
                "planned_update_count": 4,
                "planned_category_counts": {"metadata_only": 4},
                "qdisc_command_count": 0,
            },
            "applied_operations": [{"operation": "activate"}, {"operation": "update"}],
            "skipped_operations": [{"operation": "noop"}],
            "node_local_reapplied_flow_ids": ["flow-a", "flow-b"],
            "node_local_held_flow_ids": ["flow-c"],
            "host_mutation_execution": {
                "outcome": {
                    "operation_count": 1,
                    "apply_success": True,
                    "manager_timing_breakdown": {"operation_apply_wall_seconds": 0.2},
                    "delta_summary": {
                        "managed_slot_count": 2,
                        "executed_operation_count": 2,
                        "slot_outcomes": [
                            {
                                "slot_name": "slot-a",
                                "category": "created",
                                "change_class": "activate_only",
                                "operation": "activate_slot_attachment",
                                "reused": True,
                            },
                            {
                                "slot_name": "slot-b",
                                "category": "created",
                                "change_class": "activate_only",
                                "operation": "activate_slot_attachment",
                                "reused": False,
                            },
                        ],
                    },
                    "applied_operations": [{"operation": "heavy"}],
                },
                "apply_results": [{"operation": "heavy"}],
            },
            "node_local_execution": {
                "backend": {
                    "backend_kind": "nonlegacy_backend_specific_runtime_target",
                    "backend_name": "minimal_real_linux_mpls_installer",
                    "backend_version": "0.1.0",
                    "backend_switching": {
                        "selected_mode": "bpf_owned",
                        "bpf_owned": True,
                        "linux_mpls": False,
                    },
                    "bpf_owned_packet_counters_before_ping_validation": {
                        "command": {"stdout": "large bpftool dump"}
                    },
                    "shared_zone_return_apply_specs": [
                        {"command": ["ip", "route", "replace", "large"]}
                    ],
                },
                "session_id": "session-7",
                "apply_results": [{"large": True}],
                "verify_results": [{"large": True}],
                "ping_results": [{"large": True}],
                "timing": {
                    "flow_path_observations": [{"large": True}],
                    "backend_apply_contract": {
                        "bpf_owned_readiness": {
                            "attached_interface_details": [{"large": True}],
                            "reapplied_shared_apply_specs": [
                                {"command": ["ip", "route", "replace", "large"]}
                            ],
                        }
                    },
                    "zone_return_install_boundary": {
                        "intent_scope": "active_slice",
                        "all_intents": [{"flow_id": "heavy-flow"}],
                        "all_shared_zone_return_install_plans": [
                            {"commands": [["ip", "route", "replace", "heavy"]]}
                        ],
                        "shared_zone_return_intent_count": 8,
                        "scale_summary": {
                            "intent": {
                                "intent_count": 8,
                                "sample_intents": [{"flow_id": "heavy-flow"}],
                            }
                        },
                    },
                },
                "target_slice": {
                    "executed_flow_ids": ["flow-a", "flow-b"],
                    "reapplied_flow_ids": ["flow-a"],
                    "held_flow_ids": ["flow-c"],
                    "incremental_cleanup_flow_ids": ["flow-z"],
                },
            },
        }

        compact = _epoch_execution_detail_jsonl_record(
            record,
            detail_jsonl=Path("/tmp/epoch_execution_details.jsonl"),
            operational_clean_mode=True,
        )
        full = _epoch_execution_detail_jsonl_record(
            record,
            detail_jsonl=Path("/tmp/epoch_execution_details.jsonl"),
            operational_clean_mode=False,
        )

        self.assertEqual(
            compact["detail_reference"]["record_model"],
            "compact_epoch_execution_record_jsonl",
        )
        self.assertTrue(compact["detail_reference"]["full_record_elided"])
        self.assertNotIn(
            "apply_results",
            compact["host_mutation_execution"],
        )
        self.assertNotIn(
            "apply_results",
            compact["node_local_execution"],
        )
        delta_summary = compact["host_mutation_execution"]["outcome"][
            "delta_summary"
        ]
        self.assertNotIn("slot_outcomes", delta_summary)
        self.assertEqual(delta_summary["slot_outcome_count"], 2)
        self.assertEqual(
            delta_summary["slot_outcome_operation_counts"],
            {"activate_slot_attachment": 2},
        )
        backend = compact["node_local_execution"]["backend"]
        self.assertEqual(backend["backend_name"], "minimal_real_linux_mpls_installer")
        self.assertEqual(backend["backend_switching"]["selected_mode"], "bpf_owned")
        compact_json = json.dumps(compact, sort_keys=True)
        self.assertNotIn("large bpftool dump", compact_json)
        self.assertNotIn("shared_zone_return_apply_specs", compact_json)
        self.assertIn(
            "backend_apply_contract",
            compact["node_local_execution"]["timing"],
        )
        self.assertNotIn("attached_interface_details", compact_json)
        self.assertNotIn("all_intents", compact_json)
        self.assertNotIn("all_shared_zone_return_install_plans", compact_json)
        self.assertNotIn("heavy-flow", compact_json)
        self.assertNotIn("applied_operations", compact)
        self.assertNotIn("skipped_operations", compact)
        self.assertNotIn("node_local_reapplied_flow_ids", compact)
        self.assertNotIn("node_local_held_flow_ids", compact)
        self.assertEqual(compact["applied_operation_count"], 2)
        self.assertEqual(compact["skipped_operation_count"], 1)
        self.assertEqual(compact["node_local_reapplied_flow_count"], 2)
        self.assertEqual(compact["node_local_held_flow_count"], 1)
        target_slice = compact["node_local_execution"]["target_slice"]
        self.assertNotIn("executed_flow_ids", target_slice)
        self.assertNotIn("reapplied_flow_ids", target_slice)
        self.assertNotIn("held_flow_ids", target_slice)
        self.assertNotIn("incremental_cleanup_flow_ids", target_slice)
        self.assertEqual(target_slice["executed_flow_id_count"], 2)
        self.assertEqual(target_slice["reapplied_flow_id_count"], 1)
        self.assertEqual(target_slice["held_flow_id_count"], 1)
        self.assertEqual(target_slice["incremental_cleanup_flow_id_count"], 1)
        self.assertIn("apply_results", full["host_mutation_execution"])
        compact_again = _epoch_execution_detail_jsonl_record(
            compact,
            detail_jsonl=Path("/tmp/epoch_execution_details.jsonl"),
            operational_clean_mode=True,
        )
        self.assertEqual(compact_again["applied_operation_count"], 2)
        self.assertEqual(compact_again["skipped_operation_count"], 1)
        self.assertEqual(compact_again["node_local_reapplied_flow_count"], 2)
        self.assertEqual(compact_again["node_local_held_flow_count"], 1)
        self.assertEqual(
            compact_again["host_mutation_execution"]["apply_result_count"],
            1,
        )
        self.assertEqual(
            compact_again["host_mutation_execution"]["outcome"]["delta_summary"][
                "slot_outcome_count"
            ],
            2,
        )
        self.assertEqual(
            compact_again["node_local_execution"]["target_slice"][
                "executed_flow_id_count"
            ],
            2,
        )
        self.assertEqual(
            compact_again["parameter_batch_accounting"]["planned_update_count"],
            4,
        )

    def test_bpf_readiness_compaction_ignores_non_mapping_step_values(self) -> None:
        node_local_execution = {
            "timing": {
                "backend_apply_contract": {
                    "bpf_owned_readiness": {
                        "ready": True,
                        "prepare_steps": {
                            "real_backend_prepare": {
                                "ok": True,
                                "steps": {
                                    "compile": "already-compiled",
                                    "attach": ["not", "a", "mapping"],
                                    "map_load": {"ok": True},
                                },
                            },
                            "compile": "already-compiled",
                        },
                    }
                }
            },
            "backend": {},
        }

        compact = _compact_node_local_execution(node_local_execution)

        readiness = compact["timing"]["backend_apply_contract"][
            "bpf_owned_readiness"
        ]
        real_prepare = readiness["prepare_steps"]["real_backend_prepare"]
        self.assertEqual(real_prepare["steps"]["compile"], {})
        self.assertEqual(real_prepare["steps"]["attach"], {})
        self.assertEqual(real_prepare["steps"]["map_load"], {"ok": True})

    def test_bpf_prepare_compaction_preserves_compile_plan_first_cause(self) -> None:
        compact = _compact_bpf_real_backend_prepare(
            {
                "ok": False,
                "reason": "bpf_mpls_compile_plan_failed",
                "error": (
                    'missing canary interface netns:stl-940080:xp22b: '
                    'Device "xp22b" does not exist.'
                ),
                "attached_interfaces": [],
                "timing_breakdown": {
                    "bpf_map_compile_seconds": 0.0,
                    "bpf_attach_seconds": 0.0,
                    "bpf_map_write_seconds": 0.0,
                },
            }
        )

        self.assertEqual(compact["reason"], "bpf_mpls_compile_plan_failed")
        self.assertEqual(
            compact["error"],
            'missing canary interface netns:stl-940080:xp22b: '
            'Device "xp22b" does not exist.',
        )
        self.assertEqual(compact["attached_interfaces"], [])

    def test_operational_clean_compact_epoch_accepts_scalar_operation_counts(
        self,
    ) -> None:
        record = {
            "sequence_index": 9,
            "epoch_type": "runtime_epoch",
            "segment_epoch_key": "seg:slice",
            "applied_operations": 34,
            "skipped_operations": 3,
            "node_local_reapplied_flow_ids": 2,
            "node_local_held_flow_ids": 1,
            "timing": {"transition_wall_seconds": 0.4},
            "outcome": {"success": True},
            "host_mutation_execution": {
                "outcome": {
                    "operation_count": 34,
                    "apply_success": True,
                    "applied_operations": 34,
                }
            },
            "node_local_execution": {
                "target_slice": {
                    "executed_flow_ids": 4,
                    "reapplied_flow_ids": 2,
                    "held_flow_ids": 1,
                    "incremental_cleanup_flow_ids": 0,
                },
            },
        }

        compact = _epoch_execution_detail_jsonl_record(
            record,
            detail_jsonl=Path("/tmp/epoch_execution_details.jsonl"),
            operational_clean_mode=True,
        )

        self.assertEqual(compact["applied_operation_count"], 34)
        self.assertEqual(compact["skipped_operation_count"], 3)
        self.assertEqual(compact["node_local_reapplied_flow_count"], 2)
        self.assertEqual(compact["node_local_held_flow_count"], 1)
        target_slice = compact["node_local_execution"]["target_slice"]
        self.assertEqual(target_slice["executed_flow_id_count"], 4)
        self.assertEqual(target_slice["reapplied_flow_id_count"], 2)
        self.assertEqual(target_slice["held_flow_id_count"], 1)
        self.assertEqual(target_slice["incremental_cleanup_flow_id_count"], 0)

    def test_operational_clean_runtime_report_record_matches_compact_jsonl_model(
        self,
    ) -> None:
        record = {
            "sequence_index": 8,
            "epoch_type": "runtime_epoch",
            "segment_epoch_key": "seg:slice",
            "timing": {"transition_wall_seconds": 1.2},
            "outcome": {"success": True},
            "host_mutation_execution": {
                "outcome": {
                    "operation_count": 1,
                    "apply_success": True,
                    "applied_operations": [{"operation": "heavy"}],
                },
                "apply_results": [{"operation": "heavy"}],
            },
            "node_local_execution": {
                "apply_results": [{"large": True}],
                "verify_results": [{"large": True}],
                "ping_results": [{"large": True}],
            },
        }

        clean_record = _epoch_execution_runtime_report_record(
            record,
            detail_jsonl=Path("/tmp/epoch_execution_details.jsonl"),
            operational_clean_mode=True,
        )
        non_clean_record = _epoch_execution_runtime_report_record(
            record,
            detail_jsonl=Path("/tmp/epoch_execution_details.jsonl"),
            operational_clean_mode=False,
        )

        self.assertEqual(
            clean_record["detail_reference"]["record_model"],
            "compact_epoch_execution_record_jsonl",
        )
        self.assertTrue(clean_record["detail_reference"]["full_record_elided"])
        self.assertEqual(
            non_clean_record["detail_reference"]["record_model"],
            "full_epoch_execution_record_jsonl",
        )

    def test_operational_clean_host_cleanup_history_tracks_active_state_only(
        self,
    ) -> None:
        host_history = []
        source_by_slot = {}
        update_count = 0

        first = _update_host_cleanup_history(
            host_history,
            host_entry={
                "epoch_type": "runtime_epoch",
                "segment_epoch_key": "seg:slice-1",
                "host_plan": object(),
                "applied_operations": [
                    {
                        "slot_name": "ut1-access-slot0",
                        "source_binding": {"satellite_id": "sat-a"},
                    }
                ],
            },
            operational_clean_active_only=True,
            source_operations_by_slot=source_by_slot,
            update_count=update_count,
        )
        update_count = int(first["update_count"])
        second = _update_host_cleanup_history(
            host_history,
            host_entry={
                "epoch_type": "runtime_epoch",
                "segment_epoch_key": "seg:slice-2",
                "host_plan": object(),
                "applied_operations": [
                    {
                        "slot_name": "ut1-access-slot0",
                        "source_binding": {"satellite_id": "sat-b"},
                    },
                    {
                        "slot_name": "ut2-access-slot0",
                        "source_binding": {"satellite_id": "sat-c"},
                    },
                ],
            },
            operational_clean_active_only=True,
            source_operations_by_slot=source_by_slot,
            update_count=update_count,
        )

        self.assertEqual(len(host_history), 1)
        self.assertEqual(second["mode"], "operational_clean_active_host_state")
        self.assertEqual(second["update_count"], 2)
        self.assertEqual(second["elided_history_entry_count"], 1)
        self.assertEqual(second["source_operation_slot_count"], 2)
        cleanup_operations = {
            operation["slot_name"]: operation
            for operation in host_history[0]["applied_operations"]
        }
        self.assertEqual(
            cleanup_operations["ut1-access-slot0"]["source_binding"],
            {"satellite_id": "sat-a"},
        )
        self.assertEqual(
            cleanup_operations["ut2-access-slot0"]["source_binding"],
            {"satellite_id": "sat-c"},
        )

    def test_non_clean_host_cleanup_history_keeps_epoch_history(self) -> None:
        host_history = []
        source_by_slot = {}
        update_count = 0

        for index in range(2):
            summary = _update_host_cleanup_history(
                host_history,
                host_entry={
                    "epoch_type": "runtime_epoch",
                    "segment_epoch_key": f"seg:slice-{index}",
                    "host_plan": object(),
                    "applied_operations": [{"slot_name": f"slot-{index}"}],
                },
                operational_clean_active_only=False,
                source_operations_by_slot=source_by_slot,
                update_count=update_count,
            )
            update_count = int(summary["update_count"])

        self.assertEqual(len(host_history), 2)
        self.assertEqual(update_count, 2)
        self.assertEqual(source_by_slot, {})

    def test_operational_clean_bounded_cleanup_uses_touched_flow_subset(self) -> None:
        contexts = (
            SimpleNamespace(
                flow_id="flow-held",
                ut_id="ut1",
                is_direct_gateway_path=True,
            ),
            SimpleNamespace(
                flow_id="flow-reapplied",
                ut_id="ut2",
                is_direct_gateway_path=True,
            ),
            SimpleNamespace(
                flow_id="flow-incremental-cleanup",
                ut_id="ut3",
                is_direct_gateway_path=False,
            ),
        )
        session = ActiveRuntimeSession(
            session_id="session-a",
            backend={},
            target_slice={
                "reapplied_flow_ids": ["flow-reapplied"],
                "incremental_cleanup_flow_ids": ["flow-incremental-cleanup"],
                "held_flow_ids": ["flow-held"],
            },
            host_runtime_boundary={},
            preflight={},
            apply_results=[],
            verify_results=[],
            ping_results=[
                {"flow_id": "flow-held"},
                {"flow_id": "flow-reapplied"},
                {"flow_id": "flow-incremental-cleanup"},
            ],
            timing={},
            contexts=contexts,
            context_preflights=({}, {}, {}),
        )

        cleanup_session, touched = _operational_clean_bounded_cleanup_session(
            session
        )

        self.assertEqual(
            touched,
            ["flow-incremental-cleanup", "flow-reapplied"],
        )
        self.assertEqual(
            [context.flow_id for context in cleanup_session.contexts],
            ["flow-reapplied", "flow-incremental-cleanup"],
        )
        self.assertEqual(
            [result["flow_id"] for result in cleanup_session.ping_results],
            ["flow-reapplied", "flow-incremental-cleanup"],
        )

    def test_operational_clean_bounded_cleanup_skips_empty_touched_subset(
        self,
    ) -> None:
        session = ActiveRuntimeSession(
            session_id="session-empty",
            backend={},
            target_slice={
                "reapplied_flow_ids": [],
                "incremental_cleanup_flow_ids": [],
                "held_flow_ids": ["flow-held"],
            },
            host_runtime_boundary={},
            preflight={},
            apply_results=[],
            verify_results=[],
            ping_results=[{"flow_id": "flow-held"}],
            timing={},
            contexts=(SimpleNamespace(flow_id="flow-held"),),
            context_preflights=({},),
        )

        cleanup_session, touched = _operational_clean_bounded_cleanup_session(
            session
        )

        self.assertIsNone(cleanup_session)
        self.assertEqual(touched, [])

    def test_operational_clean_skips_host_rollback_cleanup(self) -> None:
        self.assertTrue(
            _should_skip_host_rollback_for_args(
                SimpleNamespace(runtime_mode="operational_clean")
            )
        )
        self.assertFalse(
            _should_skip_host_rollback_for_args(
                SimpleNamespace(runtime_mode="correctness")
            )
        )

    def test_bpf_attach_reuse_epoch_summary_exposes_counts_and_reason(self) -> None:
        summary = _bpf_attach_reuse_epoch_summary(
            {
                "timing": {
                    "backend_apply_contract": {
                        "bpf_owned_readiness": {
                            "prepare_steps": {
                                "real_backend_prepare": {
                                    "attach_reuse": {
                                        "reuse_eligible": False,
                                        "reuse_rejected_reason": (
                                            "ifindex_binding_mismatch"
                                        ),
                                        "attach_strategy": "full_attach_required",
                                        "reused_attach_count": 0,
                                        "delta_attach_count": 0,
                                        "full_attach_count": 3,
                                        "interface_set_changed": False,
                                        "steady_epoch_map_only": False,
                                        "extra_previous_interfaces": [
                                            "netns:sat-a:if0",
                                            "netns:sat-b:if1",
                                        ],
                                    },
                                    "clsact_lifecycle_metrics": {
                                        "clsact_missing_on_expected_reuse_count": 1,
                                        "delta_attach_failure_count": 2,
                                    },
                                    "resident_attach_coverage": {
                                        "enabled": True,
                                        "current_interface_count": 3,
                                        "resident_interface_count": 5,
                                        "merged_interface_count": 5,
                                        "added_interface_count": 2,
                                        "attached_interface_count": 5,
                                        "future_materialized_attach_eligible_interface_count": 2,
                                        "future_non_materialized_capacity_only_interface_count": 1,
                                        "attach_cardinality_budget": {
                                            "max_attach_interfaces": 8,
                                            "budget_exceeded": False,
                                        },
                                    },
                                }
                            }
                        }
                    }
                }
            }
        )

        self.assertTrue(summary["available"])
        self.assertFalse(summary["reuse_eligible"])
        self.assertEqual(summary["reuse_rejected_reason"], "ifindex_binding_mismatch")
        self.assertEqual(summary["attach_strategy"], "full_attach_required")
        self.assertEqual(summary["full_attach_count"], 3)
        self.assertEqual(summary["clsact_reuse_warning_count"], 3)
        self.assertTrue(summary["resident_attach_coverage_enabled"])
        self.assertEqual(summary["resident_attach_current_interface_count"], 3)
        self.assertEqual(summary["resident_attach_resident_interface_count"], 5)
        self.assertEqual(summary["resident_attach_merged_interface_count"], 5)
        self.assertEqual(summary["resident_attach_added_interface_count"], 2)
        self.assertEqual(summary["resident_attach_attached_interface_count"], 5)
        self.assertEqual(
            summary["resident_attach_future_materialized_interface_count"],
            2,
        )
        self.assertEqual(
            summary["resident_attach_future_non_materialized_interface_count"],
            1,
        )
        self.assertEqual(summary["resident_attach_budget_max_interface_count"], 8)
        self.assertFalse(summary["resident_attach_budget_exceeded"])
        self.assertEqual(summary["extra_previous_interface_count"], 2)

    def test_bpf_attach_reuse_epoch_summary_prefers_final_attach_result(
        self,
    ) -> None:
        summary = _bpf_attach_reuse_epoch_summary(
            {
                "timing": {
                    "backend_apply_contract": {
                        "bpf_owned_readiness": {
                            "ready": True,
                            "attached_interfaces": ["netns:sat-a:if0"],
                            "prepare_steps": {
                                "real_backend_prepare": {
                                    "prepare_mode": "full_attach_and_map_load",
                                    "attach_lifecycle_mode": "dirty_recovery_reset",
                                    "reused_attach_count": 0,
                                    "delta_attach_count": 0,
                                    "full_attach_count": 76,
                                    "attach_reuse": {
                                        "reuse_eligible": False,
                                        "reason": "map_reload_failed_before_full_prepare",
                                        "attach_strategy": "full_reuse",
                                        "reused_attach_count": 76,
                                        "delta_attach_count": 0,
                                        "full_attach_count": 0,
                                        "interface_set_changed": False,
                                        "steady_epoch_map_only": True,
                                    },
                                }
                            },
                        }
                    }
                }
            }
        )

        self.assertEqual(summary["attach_strategy"], "dirty_recovery_reset")
        self.assertEqual(summary["full_attach_count"], 76)
        self.assertEqual(summary["reused_attach_count"], 0)
        self.assertFalse(summary["steady_epoch_map_only"])
        self.assertTrue(summary["map_reload_fallback_to_full_attach"])
        self.assertEqual(
            summary["reuse_rejected_reason"],
            "map_reload_failed_before_full_prepare",
        )

    def test_bpf_attach_reuse_summary_survives_operational_clean_compaction(
        self,
    ) -> None:
        readiness = {
            "ready": True,
            "ok": True,
            "fallback_to_linux": False,
            "attached_interfaces": ["netns:gw1:if0", "netns:sat1:if1"],
            "resolved_packet_route_entries": [
                {
                    "direction": "g_to_z_access_ingress",
                    "flow_id": "flow-a",
                    "ut_id": "ut1",
                    "target_ip": "10.0.0.1",
                    "selected_gateway_id": "gw1",
                    "ingress_interface": "netns:sat1:if0",
                    "egress_interface": "netns:sat1:if1",
                }
            ],
            "prepare_steps": {
                "real_backend_prepare": {
                    "prepare_mode": "existing_readiness_reused_no_prepare",
                    "evidence_source": "proved_existing_bpf_owned_readiness",
                    "attached_interfaces": ["netns:gw1:if0", "netns:sat1:if1"],
                    "attach_reuse": {
                        "reuse_eligible": True,
                        "attach_strategy": "existing_readiness_reused_no_prepare",
                        "reused_attach_count": 2,
                        "delta_attach_count": 0,
                        "full_attach_count": 0,
                        "interface_set_changed": False,
                        "steady_epoch_map_only": True,
                    },
                    "timing_breakdown": {
                        "bpf_map_write_update_count": 0,
                        "bpf_map_update_count": 0,
                        "active_generation_update_count": 0,
                    },
                }
            },
        }
        compact = _compact_node_local_execution(
            {
                "backend": {"bpf_owned_readiness": readiness},
                "preflight": {
                    "backend_apply_contract": {
                        "backend_mode": "bpf_owned",
                        "bpf_owned_readiness": readiness,
                    }
                },
                "timing": {
                    "backend_apply_contract": {
                        "backend_mode": "bpf_owned",
                        "bpf_owned_readiness": readiness,
                    }
                },
                "target_slice": {},
            }
        )

        summary = _bpf_attach_reuse_epoch_summary(compact)

        self.assertTrue(summary["available"])
        self.assertTrue(summary["reuse_eligible"])
        self.assertEqual(
            summary["attach_strategy"],
            "existing_readiness_reused_no_prepare",
        )
        self.assertEqual(summary["reused_attach_count"], 2)
        self.assertEqual(summary["full_attach_count"], 0)
        self.assertTrue(summary["steady_epoch_map_only"])
        self.assertEqual(summary["resolved_packet_route_entry_count"], 1)
        self.assertEqual(summary["prepared_attached_interface_count"], 2)

    def test_prepare_bpf_owned_readiness_reuse_emits_hotpath_evidence(self) -> None:
        previous_readiness = {
            "ready": True,
            "ok": True,
            "fallback_to_linux": False,
            "attached_interfaces": ["netns:gw1:if0", "netns:sat1:if1"],
            "resolved_packet_route_entries": [{"flow_id": "flow-a"}],
            "mpls_map_entry_counts": {"core_lfib_map": 3},
        }

        with patch(
            "linux_mpls_runtime._bpf_owned_readiness_mismatch_reason",
            return_value="",
        ):
            prepared = _prepare_bpf_owned_readiness(
                runtime_update_plan=SimpleNamespace(),
                topology_path=Path("topology.json"),
                contexts=(),
                bpf_owned_readiness=previous_readiness,
            )

        real_prepare = prepared["prepare_steps"]["real_backend_prepare"]
        attach_reuse = real_prepare["attach_reuse"]
        self.assertTrue(prepared["ready"])
        self.assertEqual(
            real_prepare["prepare_mode"],
            "existing_readiness_reused_no_prepare",
        )
        self.assertEqual(
            attach_reuse["attach_strategy"],
            "existing_readiness_reused_no_prepare",
        )
        self.assertTrue(attach_reuse["steady_epoch_map_only"])
        self.assertEqual(attach_reuse["full_attach_count"], 0)
        self.assertEqual(attach_reuse["reused_attach_count"], 2)

    def test_bpf_hotpath_audit_allows_initial_attach_and_map_only_steady(self) -> None:
        records = [
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 1,
                "timing": {
                    "bpf_full_attach_count": 8,
                    "bpf_interface_set_changed": True,
                    "bpf_attach_seconds": 1.0,
                },
            },
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 2,
                "timing": {
                    "bpf_attach_reuse_eligible": True,
                    "bpf_attach_strategy": "full_reuse",
                    "bpf_reused_attach_count": 8,
                    "bpf_delta_attach_count": 0,
                    "bpf_full_attach_count": 0,
                    "bpf_interface_set_changed": False,
                    "bpf_steady_epoch_map_only": True,
                    "bpf_clsact_reuse_warning_count": 0,
                    "bpf_attach_seconds": 0.0,
                },
            },
        ]

        audit = _bpf_hotpath_reuse_audit(
            records,
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        self.assertTrue(audit["passed"])
        self.assertEqual(audit["steady_runtime_epoch_count"], 1)
        self.assertEqual(audit["steady_map_only_epoch_count"], 1)
        self.assertEqual(audit["steady_map_only_evidence_status"], "observed_passed")
        self.assertTrue(audit["resident_map_only_path_proven"])
        self.assertEqual(audit["total_full_attach_count"], 0)

    def test_bpf_hotpath_audit_distinguishes_delta_attach_from_map_only_evidence(
        self,
    ) -> None:
        records = [
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 1,
                "timing": {
                    "bpf_full_attach_count": 8,
                    "bpf_interface_set_changed": True,
                },
            },
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 2,
                "timing": {
                    "bpf_attach_reuse_eligible": True,
                    "bpf_attach_strategy": "delta_attach",
                    "bpf_reused_attach_count": 5,
                    "bpf_delta_attach_count": 3,
                    "bpf_full_attach_count": 0,
                    "bpf_interface_set_changed": True,
                    "bpf_steady_epoch_map_only": False,
                    "bpf_clsact_reuse_warning_count": 0,
                    "bpf_attach_seconds": 0.4,
                },
            },
        ]

        audit = _bpf_hotpath_reuse_audit(
            records,
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        self.assertFalse(audit["passed"])
        self.assertEqual(audit["steady_map_only_epoch_count"], 0)
        self.assertEqual(
            audit["steady_map_only_evidence_status"],
            "violations_present",
        )
        self.assertFalse(audit["resident_map_only_path_proven"])
        self.assertEqual(audit["total_full_attach_count"], 0)
        self.assertEqual(audit["total_delta_attach_count"], 3)
        self.assertEqual(
            audit["violations"][0]["reason"],
            "steady_delta_attach_in_strict_operational_clean",
        )

    def test_runtime_failure_summary_includes_delta_attach_gap_details(self) -> None:
        runtime_report = {
            "outcome": {
                "failure_reason": (
                    "bpf_owned_runtime_readiness_failed:"
                    "reason=tc_bpf_mpls_delta_attach_failed"
                )
            },
            "failure_context": {
                "exception": {
                    "message": "bpf_owned_runtime_readiness_failed",
                    "skyseed_details": {
                        "bpf_owned_readiness": {
                            "reason": "tc_bpf_mpls_delta_attach_failed",
                            "generation_id": 4,
                            "runtime_generation_loaded": False,
                            "map_ready": False,
                            "active_generation_set": False,
                            "runtime_epoch_intent": {
                                "slice_id": "starlink-shell-slice-0005"
                            },
                            "active_materialized_interfaces": [
                                "netns:stl-940120:xp120a"
                            ],
                            "compile_capacity_interfaces": [
                                "netns:stl-940120:xp120a"
                            ],
                            "future_materialized_attach_eligible_interfaces": [],
                            "future_non_materialized_capacity_only_interfaces": [],
                            "attached_interface_details": [
                                {
                                    "interface_ref": "netns:stl-940120:xp120a",
                                    "netns": "stl-940120",
                                    "container_interface_name": "xp120a",
                                    "ifindex": 16516,
                                    "expected_roles": ["g_to_z_core_lfib"],
                                }
                            ],
                            "resident_attach_coverage": {
                                "interface_classifications": [
                                    {
                                        "interface_ref": "netns:stl-940120:xp120a",
                                        "expected_roles": ["g_to_z_core_lfib"],
                                    }
                                ]
                            },
                            "resolved_mpls_flow_entries": [
                                {
                                    "map_type": "core_lfib_map",
                                    "generation": 4,
                                    "ingress_ifindex": 16516,
                                    "ingress_interface": (
                                        "netns:stl-940120:xp120a"
                                    ),
                                    "label": 524320,
                                    "direction": "g_to_z",
                                }
                            ],
                            "prepare_steps": {
                                "real_backend_prepare": {
                                    "attach_reuse": {
                                        "attach_strategy": "delta_attach",
                                        "delta_attach_count": 1,
                                        "missing_interfaces": [
                                            "netns:stl-940120:xp120a"
                                        ],
                                        "missing_directions": [
                                            {
                                                "interface_ref": (
                                                    "netns:stl-940120:xp120a"
                                                ),
                                                "missing_directions": ["ingress"],
                                            }
                                        ],
                                        "required_attach_plan": {
                                            "netns:stl-940120:xp120a": ["ingress"]
                                        },
                                        "previous_attach_plan": {},
                                    },
                                    "steps": {"map_load": {}},
                                }
                            },
                        }
                    },
                }
            },
        }

        summary = _runtime_failure_context_summary(runtime_report)
        delta = summary["bpf_delta_attach_failure"]
        detail = delta["interface_details"][0]

        self.assertEqual(delta["delta_attach_count"], 1)
        self.assertEqual(detail["interface_ref"], "netns:stl-940120:xp120a")
        self.assertEqual(detail["expected_roles"], ["g_to_z_core_lfib"])
        self.assertTrue(detail["active_materialized"])
        self.assertFalse(detail["loaded_interface_role_map_entry"])
        self.assertEqual(
            detail["classifier_core_egress_generated_keys"][
                "generated_entry_count_by_map"
            ],
            {"core_lfib_map": 1},
        )

    def test_bpf_hotpath_audit_blocks_steady_full_attach_without_interface_change(
        self,
    ) -> None:
        records = [
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 1,
                "timing": {
                    "bpf_full_attach_count": 4,
                    "bpf_interface_set_changed": True,
                },
            },
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 2,
                "epoch_id": "runtime-2",
                "timing": {
                    "bpf_attach_reuse_eligible": False,
                    "bpf_attach_strategy": "full_attach_required",
                    "bpf_reuse_rejected_reason": "ifindex_binding_mismatch",
                    "bpf_reused_attach_count": 0,
                    "bpf_delta_attach_count": 0,
                    "bpf_full_attach_count": 4,
                    "bpf_interface_set_changed": False,
                    "bpf_steady_epoch_map_only": False,
                    "bpf_clsact_reuse_warning_count": 0,
                },
            },
        ]

        audit = _bpf_hotpath_reuse_audit(
            records,
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        self.assertFalse(audit["passed"])
        self.assertEqual(audit["classified_blocker"], "BPF_HOTPATH_STILL_BLOCKED")
        self.assertEqual(
            audit["violations"][0]["reason"],
            "steady_full_attach_without_interface_set_change",
        )

    def test_bpf_hotpath_audit_classifies_steady_dirty_recovery_reset(self) -> None:
        records = [
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 1,
                "timing": {
                    "bpf_full_attach_count": 8,
                    "bpf_interface_set_changed": True,
                },
            },
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 2,
                "epoch_id": "runtime-2",
                "bpf_attach_reuse": {
                    "available": True,
                    "reuse_eligible": False,
                    "reuse_rejected_reason": "dirty_state_recovery",
                    "attach_strategy": "dirty_recovery_reset",
                    "attach_lifecycle_mode": "dirty_recovery_reset",
                    "reused_attach_count": 0,
                    "delta_attach_count": 0,
                    "full_attach_count": 4,
                    "interface_set_changed": True,
                    "steady_epoch_map_only": False,
                    "clsact_reuse_warning_count": 0,
                },
            },
        ]

        audit = _bpf_hotpath_reuse_audit(
            records,
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        self.assertFalse(audit["passed"])
        self.assertEqual(
            audit["classified_blocker"],
            "DIRTY_RECOVERY_RESET_STILL_PRESENT",
        )
        self.assertIn(
            "dirty_recovery_reset_in_steady_epoch",
            {violation["reason"] for violation in audit["violations"]},
        )

    def test_bpf_mpls_generation_map_capacities_cover_active_and_target(self) -> None:
        capacities = _bpf_mpls_generation_map_capacities(
            active_counts={
                "ingress_classifier_map": 120,
                "core_lfib_map": 3,
                "egress_handoff_map": 60,
            },
            current_counts={
                "ingress_classifier_map": 120,
                "core_lfib_map": 3,
                "egress_handoff_map": 60,
            },
        )

        self.assertEqual(capacities["classifiers"], 256)
        self.assertEqual(capacities["lfib"], 256)
        self.assertEqual(capacities["handoffs"], 136)

    def test_bpf_mpls_generation_map_capacities_reserve_bootstrap_target_sized_inactive_generation(
        self,
    ) -> None:
        capacities = _bpf_mpls_generation_map_capacities(
            current_counts={
                "ingress_classifier_map": 120,
                "core_lfib_map": 3,
                "egress_handoff_map": 60,
            },
        )

        self.assertEqual(capacities["classifiers"], 256)
        self.assertEqual(capacities["lfib"], 256)
        self.assertEqual(capacities["handoffs"], 136)

    def test_bpf_hotpath_audit_blocks_missing_reuse_reason_and_clsact_warning(
        self,
    ) -> None:
        records = [
            {"epoch_type": "runtime_epoch", "sequence_index": 1},
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 2,
                "timing": {
                    "bpf_attach_reuse_eligible": False,
                    "bpf_attach_strategy": "full_attach_required",
                    "bpf_full_attach_count": 0,
                    "bpf_interface_set_changed": False,
                    "bpf_clsact_reuse_warning_count": 1,
                },
            },
        ]

        audit = _bpf_hotpath_reuse_audit(
            records,
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        self.assertFalse(audit["passed"])
        self.assertEqual(audit["violation_count"], 2)
        self.assertEqual(
            {violation["reason"] for violation in audit["violations"]},
            {
                "reuse_ineligible_without_concrete_reason",
                "clsact_reuse_warning_in_steady_epoch",
            },
        )

    def test_bpf_hotpath_audit_blocks_map_only_attach_seconds(self) -> None:
        records = [
            {"epoch_type": "runtime_epoch", "sequence_index": 1},
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 2,
                "timing": {
                    "bpf_attach_reuse_eligible": True,
                    "bpf_attach_strategy": "full_reuse",
                    "bpf_full_attach_count": 0,
                    "bpf_interface_set_changed": False,
                    "bpf_steady_epoch_map_only": True,
                    "bpf_clsact_reuse_warning_count": 0,
                    "bpf_attach_seconds": 0.5,
                },
            },
        ]

        audit = _bpf_hotpath_reuse_audit(
            records,
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        self.assertFalse(audit["passed"])
        self.assertEqual(audit["steady_map_only_evidence_status"], "observed_failed")
        self.assertFalse(audit["resident_map_only_path_proven"])
        self.assertEqual(audit["steady_map_only_violation_count"], 1)
        self.assertEqual(
            audit["violations"][0]["reason"],
            "map_only_epoch_has_nonzero_attach_seconds",
        )

    def test_live_timing_summary_exposes_bpf_hotpath_audit(self) -> None:
        records = [
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 1,
                "activation_relative_time_seconds": 0.0,
                "timing": {
                    "transition_wall_seconds": 10.0,
                    "host_operation_apply_wall_seconds": 1.0,
                    "node_local_apply_wall_seconds": 2.0,
                    "scheduler_blocking_after_data_plane_ready_seconds": 3.0,
                    "bpf_attach_strategy": "full_attach_required",
                    "bpf_full_attach_count": 8,
                    "bpf_attach_seconds": 1.0,
                },
                "node_local_execution": {
                    "preflight": {
                        "bpf_owned_packet_counters_after_apply": {
                            "bpf_owned_packet_counters_before_ping_validation": {
                                "counters": {"classifier_hit": 4, "miss": 0}
                            },
                            "bpf_owned_packet_counters_cumulative": {
                                "counters": {"classifier_hit": 5, "miss": 0}
                            },
                            "bpf_owned_packet_counter_delta_during_validation": {
                                "classifier_hit": 1,
                                "miss": 0,
                            },
                            "miss_drop_counters": {"miss": 0, "drop": 0},
                        }
                    }
                },
            },
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 2,
                "activation_relative_time_seconds": 45.0,
                "timing": {
                    "transition_wall_seconds": 2.0,
                    "host_operation_apply_wall_seconds": 0.1,
                    "node_local_apply_wall_seconds": 0.2,
                    "scheduler_blocking_after_data_plane_ready_seconds": 0.3,
                    "runtime_rebind_operation_count": 1,
                    "runtime_rebind_wall_seconds": 0.123,
                    "bpf_attach_reuse_eligible": True,
                    "bpf_attach_strategy": "full_reuse",
                    "bpf_reused_attach_count": 8,
                    "bpf_full_attach_count": 0,
                    "bpf_interface_set_changed": False,
                    "bpf_steady_epoch_map_only": True,
                    "bpf_resident_attach_coverage_enabled": True,
                    "bpf_resident_attach_current_interface_count": 8,
                    "bpf_resident_attach_resident_interface_count": 8,
                    "bpf_resident_attach_merged_interface_count": 8,
                    "bpf_resident_attach_added_interface_count": 0,
                    "bpf_extra_previous_interface_count": 2,
                    "bpf_map_write_seconds": 0.004,
                    "bpf_map_write_update_count": 5,
                    "bpf_map_update_count": 6,
                    "active_generation_flip_seconds": 0.001,
                    "active_generation_update_count": 1,
                    "bpf_attach_seconds": 0.0,
                },
                "node_local_execution": {
                    "preflight": {
                        "bpf_owned_packet_counters_after_apply": {
                            "bpf_owned_packet_counters_before_ping_validation": {
                                "counters": {"classifier_hit": 5, "miss": 0}
                            },
                            "bpf_owned_packet_counters_cumulative": {
                                "counters": {"classifier_hit": 9, "miss": 0}
                            },
                            "bpf_owned_packet_counter_delta_during_validation": {
                                "classifier_hit": 4,
                                "miss": 0,
                            },
                            "miss_drop_counters": {"miss": 0, "drop": 0},
                        }
                    }
                },
            },
            {
                "epoch_type": "parameter_epoch",
                "sequence_index": 3,
                "activation_relative_time_seconds": 10.0,
                "timing": {
                    "transition_wall_seconds": 0.25,
                    "host_operation_apply_wall_seconds": 0.005,
                    "scheduler_blocking_after_data_plane_ready_seconds": 0.1,
                    "parameter_qdisc_wall_seconds": 0.0,
                    "parameter_qdisc_cumulative_seconds": 0.0,
                    "parameter_metadata_wall_seconds": 0.0,
                    "parameter_metadata_cumulative_seconds": 0.0,
                },
                "parameter_batch_accounting": {
                    "planned_update_count": 4,
                    "applied_result_count": 4,
                    "planned_category_counts": {"metadata_only": 4},
                    "applied_category_counts": {"metadata_only": 4},
                    "planned_slot_type_category_counts": {
                        "cross_plane_isl": {"metadata_only": 3},
                        "gw_access": {"metadata_only": 1},
                    },
                    "applied_slot_type_category_counts": {
                        "cross_plane_isl": {"metadata_only": 3},
                        "gw_access": {"metadata_only": 1},
                    },
                    "metadata_only_count": 4,
                    "bpf_parameter_map_update_count": 0,
                    "planned_qdisc_required_count": 0,
                    "applied_qdisc_required_count": 0,
                    "topology_rebind_required_count": 0,
                    "qdisc_command_count": 0,
                    "qdisc_target_group_count": 0,
                    "qdisc_timed_command_count": 0,
                    "qdisc_cumulative_seconds": 0.0,
                    "qdisc_wall_seconds": 0.0,
                    "metadata_cumulative_seconds": 0.0,
                    "metadata_wall_seconds": 0.0,
                },
            },
        ]

        summary = _live_timing_summary(
            records,
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        self.assertTrue(summary["bpf_hotpath_reuse_audit"]["passed"])
        self.assertEqual(
            summary["bpf_hotpath_reuse_audit"]["steady_map_only_epoch_count"],
            1,
        )
        self.assertEqual(
            summary["bpf_hotpath_reuse_audit"][
                "resident_attach_coverage_enabled_steady_epoch_count"
            ],
            1,
        )
        self.assertEqual(
            summary["bpf_hotpath_reuse_audit"][
                "total_resident_attach_added_interface_count"
            ],
            0,
        )
        self.assertEqual(
            summary["bpf_hotpath_reuse_audit"][
                "total_extra_previous_interface_count"
            ],
            2,
        )
        runtime_transactions = summary["runtime_transactions"]
        self.assertEqual(runtime_transactions["count"], 2)
        self.assertEqual(runtime_transactions["steady_count"], 1)
        self.assertEqual(
            runtime_transactions["event_time_progress"][
                "last_activation_relative_time_seconds"
            ],
            45.0,
        )
        self.assertEqual(
            runtime_transactions["steady_bpf_prepare_breakdown"][
                "full_attach_count"
            ],
            0,
        )
        self.assertEqual(
            runtime_transactions["steady_bpf_prepare_breakdown"][
                "reused_attach_count"
            ],
            8,
        )
        self.assertEqual(
            runtime_transactions["steady_bpf_prepare_breakdown"][
                "total_bpf_map_write_seconds"
            ],
            0.004,
        )
        self.assertEqual(
            runtime_transactions["steady_bpf_prepare_breakdown"][
                "bpf_map_write_update_count"
            ],
            5,
        )
        self.assertEqual(
            runtime_transactions["steady_bpf_prepare_breakdown"][
                "bpf_map_update_count"
            ],
            6,
        )
        self.assertEqual(
            runtime_transactions["steady_bpf_prepare_breakdown"][
                "active_generation_update_count"
            ],
            1,
        )
        self.assertEqual(
            runtime_transactions["steady_bpf_prepare_breakdown"][
                "resident_attach_coverage_enabled_count"
            ],
            1,
        )
        self.assertEqual(
            runtime_transactions["steady_bpf_prepare_breakdown"][
                "resident_attach_resident_interface_count"
            ]["worst_seconds"],
            8.0,
        )
        self.assertEqual(
            runtime_transactions["steady_bpf_prepare_breakdown"][
                "resident_attach_added_interface_count"
            ]["worst_seconds"],
            0.0,
        )
        self.assertEqual(
            runtime_transactions["steady_bpf_prepare_breakdown"][
                "extra_previous_interface_count"
            ]["worst_seconds"],
            2.0,
        )
        self.assertEqual(
            runtime_transactions["runtime_rebind_operation_count"],
            1,
        )
        self.assertEqual(
            runtime_transactions["runtime_rebind_wall_seconds"]["worst_seconds"],
            0.123,
        )

        parameter_batches = summary["parameter_update_batches"]
        self.assertEqual(parameter_batches["count"], 1)
        accounting_totals = parameter_batches["accounting_totals"]
        self.assertEqual(accounting_totals["planned_update_count"], 4)
        self.assertEqual(
            accounting_totals["planned_category_counts"],
            {"metadata_only": 4},
        )
        self.assertEqual(accounting_totals["qdisc_command_count"], 0)
        self.assertEqual(accounting_totals["topology_rebind_required_count"], 0)
        bpf_counter_accounting = summary["bpf_counter_accounting"]
        self.assertTrue(
            bpf_counter_accounting["bpf_runtime_start_counter_baseline"]["available"]
        )
        self.assertEqual(
            bpf_counter_accounting[
                "bpf_counter_delta_since_runtime_start_baseline"
            ]["classifier_hit"],
            5,
        )
        self.assertEqual(
            bpf_counter_accounting["bpf_miss_drop_delta_since_runtime_start"][
                "miss"
            ],
            0,
        )
        self.assertEqual(
            bpf_counter_accounting["negative_counter_delta_warning_count"],
            0,
        )

    def test_live_timing_summary_reports_negative_bpf_counter_delta_warnings(
        self,
    ) -> None:
        records = [
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 1,
                "node_local_execution": {
                    "preflight": {
                        "bpf_owned_packet_counters_after_apply": {
                            "bpf_owned_packet_counters_before_ping_validation": {
                                "counters": {"classifier_hit": 10, "miss": 0}
                            },
                            "bpf_owned_packet_counters_cumulative": {
                                "counters": {"classifier_hit": 8, "miss": 0}
                            },
                            "bpf_owned_packet_counter_delta_during_validation": {
                                "classifier_hit": -2,
                                "miss": 0,
                            },
                            "miss_drop_counters": {"miss": 0, "drop": 0},
                        }
                    }
                },
            }
        ]

        summary = _live_timing_summary(
            records,
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        accounting = summary["bpf_counter_accounting"]
        self.assertEqual(accounting["negative_counter_delta_warning_count"], 2)
        self.assertEqual(
            {
                warning["source"]
                for warning in accounting["negative_counter_delta_warnings"]
            },
            {"runtime_start_baseline", "epoch_validation_delta"},
        )
        self.assertEqual(accounting["fallback_count"], 0)

    def test_live_timing_summary_reports_first_failure_diagnostics(self) -> None:
        records = [
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 1,
                "epoch_id": "slice-0001",
                "outcome": {"success": True},
            },
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 2,
                "epoch_id": "slice-0002",
                "segment_epoch_key": "seg-2",
                "activation_relative_time_seconds": 45.0,
                "outcome": {
                    "success": False,
                    "failure_reason": "bpf_owned_epoch_validation_miss_drop; miss=2",
                },
                "linux_gz_fallback_command_count": 1,
                "eligible_core_fallback_to_linux": 0,
                "bpf_classifier_miss_count": 2,
                "bpf_ping_path_hit_count": 5,
                "bpf_return_handoff_hit_count": 4,
                "timing": {
                    "bpf_attach_strategy": "reuse_with_map_load",
                    "bpf_reuse_rejected_reason": "",
                    "bpf_attach_seconds": 0.0,
                    "bpf_map_write_seconds": 0.012,
                    "active_generation_flip_seconds": 0.001,
                    "bpf_clsact_reuse_warning_count": 1,
                },
                "node_local_execution": {
                    "preflight": {
                        "bpf_owned_packet_counters_after_apply": {
                            "bpf_owned_packet_counters_before_ping_validation": {
                                "counters": {"classifier_hit": 10, "miss": 0}
                            },
                            "bpf_owned_packet_counters_cumulative": {
                                "counters": {"classifier_hit": 12, "miss": 2}
                            },
                            "bpf_owned_packet_counter_delta_during_validation": {
                                "classifier_hit": 2,
                                "miss": 2,
                            },
                            "miss_drop_counters": {"miss": 2, "drop": 0},
                        }
                    }
                },
            },
        ]

        summary = _live_timing_summary(
            records,
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        diagnostics = summary["first_failure_diagnostics"]
        self.assertTrue(diagnostics["failure_observed"])
        self.assertEqual(
            diagnostics["classified_blocker"],
            "bpf_owned_epoch_validation_miss_drop",
        )
        self.assertEqual(diagnostics["first_failed_epoch"]["epoch_id"], "slice-0002")
        self.assertEqual(
            diagnostics["first_failed_epoch"]["activation_relative_time_seconds"],
            45.0,
        )
        self.assertEqual(
            diagnostics["bpf_first_failure_counters"]["miss_drop_counters"]["miss"],
            2,
        )
        self.assertEqual(
            diagnostics["fallback_at_first_failure"]["fallback_count"],
            1,
        )
        self.assertEqual(diagnostics["bpf_attach_strategy"], "reuse_with_map_load")
        self.assertEqual(diagnostics["bpf_attach_seconds"], 0.0)
        self.assertEqual(diagnostics["bpf_map_write_seconds"], 0.012)
        self.assertEqual(diagnostics["active_generation_flip_seconds"], 0.001)
        self.assertEqual(diagnostics["clsact_reuse_warning_count"], 1)

    def test_live_timing_summary_reads_runtime_rebind_from_host_operation_summary(
        self,
    ) -> None:
        records = [
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 1,
                "timing": {"transition_wall_seconds": 1.0},
                "host_operation_timing_summary": {
                    "operation_counts": {"activate_slot_attachment": 1},
                    "per_operation_wall_seconds": {"activate_slot_attachment": 0.1},
                },
            },
            {
                "epoch_type": "runtime_epoch",
                "sequence_index": 2,
                "timing": {"transition_wall_seconds": 2.0},
                "host_operation_timing_summary": {
                    "operation_counts": {"rebind_slot_attachment": 1},
                    "per_operation_wall_seconds": {"rebind_slot_attachment": 0.28},
                },
            },
        ]

        summary = _live_timing_summary(
            records,
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        runtime_transactions = summary["runtime_transactions"]
        self.assertEqual(runtime_transactions["runtime_rebind_operation_count"], 1)
        self.assertEqual(
            runtime_transactions["runtime_rebind_wall_seconds"]["worst_seconds"],
            0.28,
        )
        self.assertEqual(
            runtime_transactions["total_runtime_rebind_wall_seconds"],
            0.28,
        )

    def test_bpf_hotpath_audit_not_applicable_without_clean_bpf_owned(self) -> None:
        audit = _bpf_hotpath_reuse_audit(
            [
                {"epoch_type": "runtime_epoch", "sequence_index": 1},
                {"epoch_type": "runtime_epoch", "sequence_index": 2},
            ],
            runtime_mode="fast-demo",
            forwarding_backend="linux_mpls",
        )

        self.assertFalse(audit["applicable"])
        self.assertIsNone(audit["passed"])
        self.assertEqual(audit["evidence_status"], "not_applicable")
        self.assertEqual(audit["classified_blocker"], "")

    def test_bpf_hotpath_audit_requires_steady_runtime_evidence(self) -> None:
        audit = _bpf_hotpath_reuse_audit(
            [{"epoch_type": "runtime_epoch", "sequence_index": 1}],
            runtime_mode="operational_clean",
            forwarding_backend="bpf_owned",
        )

        self.assertTrue(audit["applicable"])
        self.assertIsNone(audit["passed"])
        self.assertEqual(
            audit["evidence_status"],
            "insufficient_steady_runtime_epochs",
        )
        self.assertEqual(audit["classified_blocker"], "")

    def test_run_invocation_metadata_records_operational_clean_provenance(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "run_starlink_shell_experiment.py",
                "--runtime-mode",
                "operational_clean",
                "--forwarding-backend",
                "bpf_owned",
                "--planned-shell-id",
                "S4",
                "--max-satellites",
                "348",
            ],
        ):
            args = parse_args()

        with patch(
            "run_starlink_shell_experiment._git_provenance",
            return_value={"head": "abc", "dirty": True, "dirty_status": [" M file"]},
        ):
            metadata = _run_invocation_metadata(
                args=args,
                run_wall_start_utc="2026-06-23T00:00:00Z",
                run_wall_end_utc="2026-06-23T02:00:00Z",
            )

        self.assertEqual(metadata["runtime_mode"], "operational_clean")
        self.assertEqual(metadata["forwarding_backend"], "bpf_owned")
        self.assertEqual(metadata["planned_shell_id"], "S4")
        self.assertEqual(metadata["requested_satellite_count"], 348)
        self.assertEqual(metadata["git"]["head"], "abc")
        self.assertTrue(metadata["git"]["dirty"])

    def test_operational_clean_heartbeat_aggregates_runtime_evidence(self) -> None:
        runtime_record = {
            "epoch_type": "runtime_epoch",
            "linux_gz_fallback_command_count": 0,
            "eligible_core_fallback_to_linux": 0,
            "bpf_classifier_miss_count": 0,
            "bpf_ping_path_hit_count": 3,
            "bpf_return_handoff_hit_count": 2,
            "outcome": {"success": True},
            "node_local_execution": {
                "preflight": {
                    "bpf_owned_packet_counters_after_apply": {
                        "bpf_owned_packet_counters_before_ping_validation": {
                            "counters": {"miss": 0, "drop": 0, "classifier_hit": 4}
                        },
                        "bpf_owned_packet_counters_cumulative": {
                            "counters": {"miss": 0, "drop": 0, "classifier_hit": 7}
                        },
                        "bpf_owned_packet_counter_delta_during_validation": {
                            "miss": 0,
                            "drop": 0,
                            "classifier_hit": 3,
                        },
                        "miss_drop_counters": {"miss": 0, "drop": 0},
                    },
                    "backend": {
                        "bpf_owned_readiness": {
                            "prepare_mode": "full_attach_and_map_load",
                            "reused_attach_count": 0,
                            "delta_attach_count": 0,
                            "full_attach_count": 2,
                            "active_generation": 11,
                            "active_generation_switch_count": 1,
                        }
                    },
                }
            },
        }
        parameter_record = {
            "epoch_type": "parameter_epoch",
            "noop_skipped": False,
            "outcome": {"success": True},
            "host_mutation_execution": {
                "apply_results": [
                    {"operation": "update_slot_parameters", "qdisc_required": True}
                ]
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "epoch_execution_details.jsonl"
            path.write_text(
                json.dumps(runtime_record) + "\n" + json.dumps(parameter_record) + "\n",
                encoding="utf-8",
            )
            heartbeat = _operational_clean_heartbeat_summary(
                run_dir=Path(tmp),
                progress_data={
                    "run_wall_start_monotonic": 10.0,
                    "current_phase": "epoch:runtime_epoch:completed",
                    "current_epoch_context": {"activation_time_utc": "t0"},
                },
                now_monotonic=15.0,
            )

        self.assertEqual(heartbeat["runtime_transaction_success_count"], 1)
        self.assertEqual(heartbeat["parameter_update_batch_success_count"], 1)
        self.assertEqual(heartbeat["fallback_count"], 0)
        self.assertEqual(heartbeat["linux_gz_fallback_command_count"], 0)
        self.assertEqual(heartbeat["eligible_core_fallback_to_linux"], 0)
        self.assertEqual(heartbeat["negative_counter_delta_warning_count"], 0)
        self.assertEqual(heartbeat["negative_counter_delta_warnings"], [])
        self.assertEqual(heartbeat["qdisc_update_count"], 1)
        self.assertEqual(heartbeat["bpf_miss_drop_delta_since_runtime_start"]["miss"], 0)
        self.assertEqual(
            heartbeat["bpf_counter_delta_since_runtime_start_baseline"][
                "classifier_hit"
            ],
            3,
        )
        self.assertEqual(heartbeat["active_generation"], 11)
        self.assertEqual(heartbeat["attach_summary"]["full_attach_count"], 2)

    def test_final_cleanup_timing_summary_exposes_cleanup_wall_time(self) -> None:
        summary = _final_cleanup_timing_summary(
            runtime_report={
                "final_cleanup": {
                    "node_local": {
                        "timing": {
                            "rollback_wall_seconds": 0.2,
                            "cleanup_wall_seconds": 0.3,
                        }
                    },
                    "host_rollbacks": [
                        {
                            "timing": {
                                "rollback_wall_seconds": 0.4,
                                "cleanup_wall_seconds": 0.1,
                            }
                        }
                    ],
                    "all_cleanup_success": True,
                }
            },
            phase_timing={
                "compose_down_after_wall_seconds": 0.5,
                "post_run_preflight_wall_seconds": 0.6,
            },
        )

        self.assertEqual(summary["runtime_rollback_wall_seconds"], 0.6)
        self.assertEqual(summary["runtime_cleanup_wall_seconds"], 0.4)
        self.assertEqual(summary["compose_down_after_wall_seconds"], 0.5)
        self.assertEqual(summary["post_run_preflight_wall_seconds"], 0.6)
        self.assertEqual(summary["total_final_cleanup_wall_seconds"], 2.1)
        self.assertTrue(summary["all_cleanup_success"])
        self.assertTrue(summary["runtime_cleanup_available"])
        self.assertTrue(summary["compose_down_after_included"])
        self.assertTrue(summary["post_run_preflight_included"])

    def test_runtime_final_cleanup_removes_bpf_pins_after_evidence_capture(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_rollback_mpls(*, attached_interfaces, remove_maps):
            calls.append(
                {
                    "attached_interfaces": list(attached_interfaces),
                    "remove_maps": remove_maps,
                }
            )
            return {
                "rollback_completed": True,
                "residue": {
                    "clean": True,
                    "pinned_maps": {"ingress_classifier_map": False},
                },
            }

        with patch.dict(
            "sys.modules",
            {
                "bpf_mpls_live": SimpleNamespace(
                    rollback_mpls=fake_rollback_mpls,
                )
            },
        ):
            cleanup = _cleanup_bpf_mpls_pins_after_runtime()

        self.assertTrue(cleanup["attempted"])
        self.assertTrue(cleanup["rollback_completed"])
        self.assertEqual(
            calls,
            [
                {
                    "attached_interfaces": [],
                    "remove_maps": True,
                }
            ],
        )

    def test_empty_validation_scope_falls_back_to_gateway_class_sample(self) -> None:
        selection = _select_runtime_ping_validation_contexts(
            all_contexts=(),
            reapplied_contexts=(),
            validation_scope="",
        )

        self.assertEqual(
            selection["validation_scope"],
            DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE,
        )
        self.assertEqual(selection["active_flow_count"], 0)
        self.assertEqual(selection["reapplied_flow_count"], 0)
        self.assertEqual(selection["selected_flow_count"], 0)
        self.assertEqual(selection["selected_to_sample_source_flow_ratio"], 0.0)

    def test_gateway_class_sample_keeps_required_probe_coverage(self) -> None:
        direct = SimpleNamespace(
            flow_id="flow-direct",
            ut_id="ut101",
            selected_gateway_id="gw1",
            gateway_class_id="zone-a:default-gateway:gw1",
            is_direct_gateway_path=True,
            zone_id="zone-a",
            zone_return_prefixes=("10.97.0.0/24",),
            return_path_intent={},
            traffic_scope="",
        )
        core = SimpleNamespace(
            flow_id="flow-core",
            ut_id="ut102",
            selected_gateway_id="gw1",
            gateway_class_id="zone-a:default-gateway:gw1",
            is_direct_gateway_path=False,
            zone_id="zone-a",
            zone_return_prefixes=("10.97.0.0/24",),
            return_path_intent={},
            traffic_scope="",
        )
        override = SimpleNamespace(
            flow_id="flow-override:override:asn160",
            ut_id="ut103",
            selected_gateway_id="gw2",
            gateway_class_id="zone-a:override-gateway:gw2",
            is_direct_gateway_path=True,
            zone_id="zone-a",
            zone_return_prefixes=("10.97.0.0/24",),
            return_path_intent={},
            traffic_scope="special_prefix_override",
        )

        selection = _select_runtime_ping_validation_contexts(
            all_contexts=(direct, core, override),
            reapplied_contexts=(direct, core, override),
            validation_scope="reapplied_gateway_class_sample",
        )

        self.assertIn("flow-core", selection["target_flow_ids"])
        self.assertEqual(
            selection["coverage_flow_ids"]["core_mpls_like"],
            "flow-core",
        )
        self.assertTrue(selection["coverage"]["direct_probe_selected"])
        self.assertTrue(selection["coverage"]["core_probe_selected"])
        self.assertTrue(selection["coverage"]["return_z_probe_selected"])
        self.assertTrue(selection["coverage"]["override_probe_selected"])
        self.assertTrue(selection["coverage"]["same_zone_multi_ut_probe_selected"])

    def test_connectivity_probe_warmup_is_opt_in(self) -> None:
        context = SimpleNamespace(
            flow_id="flow-a",
            serving_satellite_id="stl-950001",
            selected_gateway_id="gw1",
            target_host_node_id="as154-host-new",
            ping_target_ip="10.154.0.129",
            validation_targets=(
                {"node_id": "as154-host-new", "ip_address": "10.154.0.129"},
            ),
            ut_access_ip="10.10.0.101",
            ut_access_peer_ip="10.10.0.1",
            ut_id="ut101",
        )
        session = SimpleNamespace(
            contexts=(context,),
            target_slice={"slice_id": "slice-1"},
            backend={},
        )

        with (
            patch("linux_mpls_runtime._warm_direct_access_link") as warm,
            patch(
                "linux_mpls_runtime._ping_validation_targets",
                return_value=(
                    [
                        {
                            "flow_id": "flow-a",
                            "success": True,
                        }
                    ],
                    0.0,
                ),
            ),
            patch(
                "linux_mpls_runtime._ground_gateway_sanity_probe",
                return_value={"ok": True, "blockers": [], "flow_results": []},
            ),
        ):
            result = MinimalRealLinuxMplsInstaller().probe_connectivity(session)

        self.assertTrue(result["ping_success"])
        warm.assert_not_called()

    def test_bpf_owned_interface_role_completeness_requires_attached_roles(
        self,
    ) -> None:
        readiness = {
            "attached_interface_details": [
                {
                    "interface_ref": "netns:gw211:ix210",
                    "namespace": "gw211",
                    "interface": "ix210",
                    "ifindex": 323,
                    "expected_roles": ["z_to_g_classifier"],
                }
            ],
            "prepare_steps": {
                "real_backend_prepare": {
                    "steps": {
                        "map_load": {
                            "loaded_interface_roles": [],
                        }
                    }
                }
            },
        }

        incomplete = _bpf_owned_interface_role_completeness(readiness)

        self.assertFalse(incomplete["ok"])
        self.assertEqual(incomplete["incomplete_interface_count"], 1)
        self.assertEqual(
            incomplete["incomplete_interfaces"][0]["missing_role_names"],
            ["classifier"],
        )

        readiness["prepare_steps"]["real_backend_prepare"]["steps"]["map_load"][
            "loaded_interface_roles"
        ] = [
            {
                "ifindex": 323,
                "role": 1,
                "classifier": True,
                "mpls": False,
            }
        ]

        complete = _bpf_owned_interface_role_completeness(readiness)

        self.assertTrue(complete["ok"])
        self.assertEqual(complete["incomplete_interface_count"], 0)

    def test_bpf_owned_attach_reuse_requires_existing_interface_directions(self) -> None:
        compiled = {
            "attach_plan": {
                "netns:gw205:ix204": ["ingress"],
                "netns:stl-940001:ua-u2015s0": ["ingress", "egress"],
            }
        }
        previous_readiness = {
            "prepare_steps": {
                "real_backend_prepare": {
                    "attach_plan": {
                        "netns:gw205:ix204": ["ingress"],
                        "netns:stl-940001:ua-u2015s0": ["egress", "ingress"],
                    }
                }
            }
        }

        reuse = _bpf_owned_attach_reuse_plan(
            compiled=compiled,
            previous_readiness=previous_readiness,
        )

        self.assertTrue(reuse["reuse_eligible"])
        self.assertEqual(reuse["attach_strategy"], "full_reuse")
        self.assertTrue(reuse["steady_epoch_map_only"])
        self.assertFalse(reuse["interface_set_changed"])
        self.assertEqual(reuse["missing_interfaces"], [])
        self.assertEqual(reuse["missing_directions"], [])
        self.assertEqual(reuse["reused_attach_count"], 2)
        self.assertEqual(reuse["delta_attach_count"], 0)

    def test_bpf_owned_attach_reuse_allows_previous_resident_superset(self) -> None:
        reuse = _bpf_owned_attach_reuse_plan(
            compiled={
                "attach_plan": {
                    "netns:gw205:ix204": ["ingress"],
                }
            },
            previous_readiness={
                "prepare_steps": {
                    "real_backend_prepare": {
                        "attach_plan": {
                            "netns:gw205:ix204": ["ingress"],
                            "netns:stl-940058:ua-u2028s0": ["ingress"],
                        },
                        "map_capacities": {
                            "classifiers": 128,
                            "lfib": 256,
                            "handoffs": 128,
                        },
                    }
                }
            },
        )

        self.assertTrue(reuse["reuse_eligible"])
        self.assertEqual(reuse["attach_strategy"], "full_reuse")
        self.assertTrue(reuse["steady_epoch_map_only"])
        self.assertFalse(reuse["interface_set_changed"])
        self.assertEqual(reuse["delta_attach_count"], 0)
        self.assertEqual(
            reuse["extra_previous_interfaces"],
            ["netns:stl-940058:ua-u2028s0"],
        )

    def test_bpf_owned_attach_reuse_allows_previous_resident_role_superset(
        self,
    ) -> None:
        reuse = _bpf_owned_attach_reuse_plan(
            compiled={
                "attach_plan": {"netns:gw205:ix204": ["ingress"]},
                "attached_interface_details": [
                    {
                        "interface_ref": "netns:gw205:ix204",
                        "attach_directions": ["ingress"],
                        "expected_roles": ["g_to_z_classifier"],
                    }
                ],
            },
            previous_readiness={
                "attached_interface_details": [
                    {
                        "interface_ref": "netns:gw205:ix204",
                        "attach_directions": ["ingress"],
                        "expected_roles": [
                            "g_to_z_classifier",
                            "z_to_g_classifier",
                        ],
                    }
                ],
                "prepare_steps": {
                    "real_backend_prepare": {
                        "attach_plan": {"netns:gw205:ix204": ["ingress"]},
                        "map_capacities": {
                            "classifiers": 128,
                            "lfib": 256,
                            "handoffs": 128,
                        },
                    }
                },
            },
        )

        self.assertTrue(reuse["reuse_eligible"])
        self.assertTrue(reuse["steady_epoch_map_only"])
        self.assertEqual(reuse["interface_role_set_mismatches"], [])

    def test_bpf_owned_attach_reuse_role_mismatch_reports_exact_sets(
        self,
    ) -> None:
        reuse = _bpf_owned_attach_reuse_plan(
            compiled={
                "attach_plan": {"netns:stl-940001:ga-g207s0": ["ingress"]},
                "attached_interface_details": [
                    {
                        "interface_ref": "netns:stl-940001:ga-g207s0",
                        "attach_directions": ["ingress"],
                        "expected_roles": [
                            "g_to_z_egress_handoff",
                            "z_to_g_core_lfib",
                        ],
                    }
                ],
            },
            previous_readiness={
                "attached_interface_details": [
                    {
                        "interface_ref": "netns:stl-940001:ga-g207s0",
                        "attach_directions": ["ingress"],
                        "expected_roles": [
                            "g_to_z_egress_handoff",
                            "legacy_previous_role",
                        ],
                    }
                ],
                "prepare_steps": {
                    "real_backend_prepare": {
                        "attach_plan": {
                            "netns:stl-940001:ga-g207s0": ["ingress"],
                        },
                        "map_capacities": {
                            "classifiers": 128,
                            "lfib": 256,
                            "handoffs": 128,
                        },
                    }
                },
            },
        )

        self.assertFalse(reuse["reuse_eligible"])
        self.assertEqual(
            reuse["reuse_rejected_reason"],
            "interface_role_set_mismatch",
        )
        self.assertEqual(
            reuse["outcome_labels"],
            [
                "STEADY_ATTACH_REUSE_CONTRACT_STILL_BLOCKED",
                "INTERFACE_ROLE_SET_MISMATCH_ROOT_CAUSE_IDENTIFIED",
            ],
        )
        self.assertEqual(
            reuse["interface_role_set_mismatches"],
            [
                {
                    "interface_ref": "netns:stl-940001:ga-g207s0",
                    "previous_roles": [
                        "g_to_z_egress_handoff",
                        "legacy_previous_role",
                    ],
                    "current_roles": [
                        "g_to_z_egress_handoff",
                        "z_to_g_core_lfib",
                    ],
                    "missing_current_roles": ["z_to_g_core_lfib"],
                    "extra_previous_roles": ["legacy_previous_role"],
                    "role_sets_match": False,
                }
            ],
        )

    def test_bpf_owned_resident_attach_coverage_does_not_merge_future_maps(
        self,
    ) -> None:
        merged = _bpf_owned_merge_resident_attach_coverage(
            compiled={
                "attach_plan": {"netns:gw205:ix204": ["ingress"]},
                "attached_interfaces": ["netns:gw205:ix204"],
                "attached_interface_details": [
                    {
                        "interface_ref": "netns:gw205:ix204",
                        "attach_directions": ["ingress"],
                        "expected_roles": ["g_to_z_classifier"],
                    }
                ],
                "compiled_mpls_map_counts": {
                    "ingress_classifier_map": 1,
                    "core_lfib_map": 1,
                    "egress_handoff_map": 1,
                },
                "resolved_mpls_flow_entries": [{"flow_id": "current"}],
            },
            resident_compiled={
                "attach_plan": {
                    "netns:gw205:ix204": ["ingress"],
                    "netns:stl-940058:ua-u2028s0": ["ingress", "egress"],
                },
                "attached_interface_details": [
                    {
                        "interface_ref": "netns:stl-940058:ua-u2028s0",
                        "attach_directions": ["ingress", "egress"],
                        "expected_roles": ["z_to_g_classifier"],
                    }
                ],
                "compiled_mpls_map_counts": {
                    "ingress_classifier_map": 99,
                    "core_lfib_map": 99,
                    "egress_handoff_map": 99,
                },
                "resolved_mpls_flow_entries": [{"flow_id": "future"}],
            },
        )

        self.assertEqual(
            merged["attach_plan"],
            {"netns:gw205:ix204": ["ingress"]},
        )
        self.assertEqual(
            merged["attached_interfaces"],
            ["netns:gw205:ix204"],
        )
        self.assertEqual(
            merged["compiled_mpls_map_counts"]["ingress_classifier_map"],
            1,
        )
        self.assertEqual(
            merged["compile_capacity_mpls_map_counts"]["ingress_classifier_map"],
            99,
        )
        self.assertEqual(
            merged["resolved_mpls_flow_entries"],
            [{"flow_id": "current"}],
        )
        self.assertEqual(
            merged["resident_attach_coverage"]["added_interfaces"],
            ["netns:stl-940058:ua-u2028s0"],
        )
        self.assertEqual(
            merged["future_materialized_attach_eligible_interfaces"],
            [],
        )
        self.assertEqual(
            merged["future_non_materialized_capacity_only_interfaces"],
            ["netns:stl-940058:ua-u2028s0"],
        )
        self.assertEqual(
            merged["future_horizon_interfaces"],
            ["netns:stl-940058:ua-u2028s0"],
        )
        self.assertEqual(
            merged["compile_capacity_interfaces"],
            ["netns:gw205:ix204", "netns:stl-940058:ua-u2028s0"],
        )
        self.assertFalse(
            merged["interface_set_contract"]["future_interfaces_readiness_blocking"]
        )

    def test_bpf_owned_materialized_future_resident_interface_is_preattached(
        self,
    ) -> None:
        active_contexts = (SimpleNamespace(flow_id="active-flow"),)
        resident_contexts = (SimpleNamespace(flow_id="future-flow"),)
        active_interface = "netns:gw205:ix204"
        future_interface = "netns:stl-940006:ua-u2003s0"

        def compile_maps(*, contexts, generation_id, offline=False):
            if contexts is resident_contexts:
                self.assertTrue(offline)
                return {
                    "attach_plan": {future_interface: ["ingress", "egress"]},
                    "attached_interfaces": [future_interface],
                    "attached_interface_details": [
                        {
                            "interface_ref": future_interface,
                            "attach_directions": ["ingress", "egress"],
                            "expected_roles": ["z_to_g_zone_handoff"],
                            "ifindex": 9006,
                        }
                    ],
                    "compiled_mpls_map_counts": {
                        "ingress_classifier_map": 7,
                        "core_lfib_map": 8,
                        "egress_handoff_map": 9,
                    },
                    "map_conflicts": [],
                    "map_conflict_summary": {"conflict_count": 0},
                    "label_allocation": {},
                    "resolved_mpls_flow_entries": [{"flow_id": "future"}],
                }
            self.assertIs(contexts, active_contexts)
            return {
                "attach_plan": {active_interface: ["ingress"]},
                "attached_interfaces": [active_interface],
                "attached_interface_details": [
                    {
                        "interface_ref": active_interface,
                        "attach_directions": ["ingress"],
                        "expected_roles": ["active_materialized"],
                        "ifindex": 204,
                    }
                ],
                "compiled_mpls_map_counts": {
                    "ingress_classifier_map": 1,
                    "core_lfib_map": 2,
                    "egress_handoff_map": 3,
                },
                "map_conflicts": [],
                "map_conflict_summary": {"conflict_count": 0},
                "label_allocation": {},
                "resolved_mpls_flow_entries": [{"flow_id": "active"}],
            }

        attach_calls: list[str] = []

        def attach(interface_ref, *args, **kwargs):
            attach_calls.append(interface_ref)
            return [{"returncode": 0, "args": ["tc", "filter", "add"]}]

        with (
            patch(
                "linux_mpls_runtime._bpf_owned_compile_mpls_maps_for_contexts",
                side_effect=compile_maps,
            ),
            patch(
                "linux_mpls_runtime._bpf_owned_future_interface_materialization_evidence",
                return_value={
                    "materialized_interfaces": [
                        {
                            "interface_ref": future_interface,
                            "ifindex": 6003,
                            "reason": "future_horizon_live_ifindex_resolved",
                        }
                    ],
                    "non_materialized_interfaces": [],
                },
            ),
            patch(
                "satellite.bpf_dataplane_canary.collect_environment",
                return_value={},
            ),
            patch("satellite.bpf_dataplane_canary.attach_blockers", return_value=[]),
            patch(
                "satellite.bpf_mpls_live.compile_mpls_bpf",
                return_value={"returncode": 0},
            ),
            patch("satellite.bpf_mpls_live.rollback_mpls", return_value={"ok": True}),
            patch("satellite.bpf_mpls_live.attach_mpls", side_effect=attach),
            patch(
                "satellite.bpf_mpls_live.load_mpls_maps",
                return_value={
                    "ok": True,
                    "active_generation": 42,
                    "loaded_classifier_count": 1,
                    "loaded_core_lfib_count": 2,
                    "loaded_egress_handoff_count": 3,
                    "loaded_interface_role_count": 2,
                    "active_generation_switch_count": 1,
                },
            ),
            patch(
                "linux_mpls_runtime._read_bpf_owned_interface_roles",
                return_value={"ok": True, "command": {"returncode": 0}},
            ),
            patch(
                "linux_mpls_runtime._bpf_owned_interface_role_completeness",
                return_value={"ok": True},
            ),
        ):
            report = _bpf_owned_prepare_mpls_backend(
                proof={"generation_id": 42, "map_entry_count": 1},
                contexts=active_contexts,
                resident_attach_contexts=resident_contexts,
            )

        self.assertTrue(report["ok"])
        self.assertEqual(sorted(attach_calls), [active_interface, future_interface])
        self.assertEqual(
            report["future_materialized_attach_eligible_interfaces"],
            [future_interface],
        )
        self.assertEqual(
            report["future_non_materialized_capacity_only_interfaces"],
            [],
        )
        self.assertEqual(report["full_attach_count"], 2)
        future_detail = {
            detail["interface_ref"]: detail
            for detail in report["attached_interface_details"]
        }[future_interface]
        self.assertEqual(future_detail["ifindex"], 6003)
        self.assertEqual(
            report["resident_attach_coverage"][
                "future_materialized_attach_eligible_interface_count"
            ],
            1,
        )
        self.assertEqual(
            report["resident_attach_coverage"]["attach_cardinality_budget"][
                "attach_eligible_count"
            ],
            2,
        )

    def test_bpf_owned_active_interface_merges_resident_role_superset(self) -> None:
        interface_ref = "netns:stl-940001:ga-g207s0"
        merged = _bpf_owned_merge_resident_attach_coverage(
            compiled={
                "attach_plan": {interface_ref: ["ingress"]},
                "attached_interfaces": [interface_ref],
                "attached_interface_details": [
                    {
                        "interface_ref": interface_ref,
                        "attach_directions": ["ingress"],
                        "expected_roles": ["g_to_z_egress_handoff"],
                        "ifindex": 207,
                    }
                ],
                "compiled_mpls_map_counts": {"ingress_classifier_map": 1},
            },
            resident_compiled={
                "attach_plan": {interface_ref: ["ingress"]},
                "attached_interface_details": [
                    {
                        "interface_ref": interface_ref,
                        "attach_directions": ["ingress"],
                        "expected_roles": [
                            "g_to_z_egress_handoff",
                            "z_to_g_core_lfib",
                            "z_to_g_zone_handoff",
                        ],
                        "ifindex": 999,
                    }
                ],
                "compiled_mpls_map_counts": {"ingress_classifier_map": 2},
            },
        )

        details = {
            detail["interface_ref"]: detail
            for detail in merged["attached_interface_details"]
        }
        self.assertEqual(details[interface_ref]["ifindex"], 207)
        self.assertEqual(
            details[interface_ref]["expected_roles"],
            [
                "g_to_z_egress_handoff",
                "z_to_g_core_lfib",
                "z_to_g_zone_handoff",
            ],
        )
        classifications = {
            item["interface_ref"]: item["classification"]
            for item in merged["resident_attach_coverage"][
                "interface_classifications"
            ]
        }
        self.assertEqual(
            classifications[interface_ref],
            "ACTIVE_MATERIALIZED_CURRENT_AND_HORIZON",
        )

    def test_bpf_owned_future_resident_interface_not_live_bound(self) -> None:
        active_contexts = (SimpleNamespace(flow_id="active-flow"),)
        resident_contexts = (SimpleNamespace(flow_id="future-flow"),)
        active_interface = "netns:gw205:ix204"
        future_interface = "netns:stl-940080:xp22b"
        compile_calls = []

        def compile_maps(*, contexts, generation_id, offline=False):
            compile_calls.append((contexts, generation_id, offline))
            if contexts is resident_contexts:
                self.assertTrue(offline)
                return {
                    "attach_plan": {future_interface: ["ingress", "egress"]},
                    "attached_interfaces": [future_interface],
                    "attached_interface_details": [
                        {
                            "interface_ref": future_interface,
                            "attach_directions": ["ingress", "egress"],
                            "expected_roles": ["future_capacity_only"],
                        }
                    ],
                    "compiled_mpls_map_counts": {
                        "ingress_classifier_map": 7,
                        "core_lfib_map": 8,
                        "egress_handoff_map": 9,
                    },
                    "map_conflicts": [],
                    "map_conflict_summary": {"conflict_count": 0},
                    "label_allocation": {},
                    "resolved_mpls_flow_entries": [{"flow_id": "future"}],
                }
            self.assertIs(contexts, active_contexts)
            self.assertFalse(offline)
            return {
                "attach_plan": {active_interface: ["ingress"]},
                "attached_interfaces": [active_interface],
                "attached_interface_details": [
                    {
                        "interface_ref": active_interface,
                        "attach_directions": ["ingress"],
                        "expected_roles": ["active_materialized"],
                    }
                ],
                "compiled_mpls_map_counts": {
                    "ingress_classifier_map": 1,
                    "core_lfib_map": 2,
                    "egress_handoff_map": 3,
                },
                "map_conflicts": [],
                "map_conflict_summary": {"conflict_count": 0},
                "label_allocation": {},
                "resolved_mpls_flow_entries": [{"flow_id": "active"}],
            }

        with (
            patch(
                "linux_mpls_runtime._bpf_owned_compile_mpls_maps_for_contexts",
                side_effect=compile_maps,
            ),
            patch(
                "linux_mpls_runtime._bpf_owned_future_interface_materialization_evidence",
                return_value={
                    "materialized_interfaces": [],
                    "non_materialized_interfaces": [
                        {
                            "interface_ref": future_interface,
                            "reason": "future_horizon_ifindex_missing",
                            "error": "Device does not exist",
                        }
                    ],
                },
            ),
            patch(
                "satellite.bpf_dataplane_canary.collect_environment",
                return_value={},
            ),
            patch("satellite.bpf_dataplane_canary.attach_blockers", return_value=[]),
            patch(
                "satellite.bpf_mpls_live.compile_mpls_bpf",
                return_value={"returncode": 0},
            ),
            patch("satellite.bpf_mpls_live.rollback_mpls", return_value={"ok": True}),
            patch(
                "satellite.bpf_mpls_live.attach_mpls",
                return_value=[{"returncode": 0, "args": ["tc", "filter", "add"]}],
            ) as attach_mpls,
            patch(
                "satellite.bpf_mpls_live.load_mpls_maps",
                return_value={
                    "ok": True,
                    "active_generation": 42,
                    "loaded_classifier_count": 1,
                    "loaded_core_lfib_count": 2,
                    "loaded_egress_handoff_count": 3,
                    "loaded_interface_role_count": 1,
                    "active_generation_switch_count": 1,
                },
            ),
            patch(
                "linux_mpls_runtime._read_bpf_owned_interface_roles",
                return_value={"ok": True, "command": {"returncode": 0}},
            ),
            patch(
                "linux_mpls_runtime._bpf_owned_interface_role_completeness",
                return_value={"ok": True},
            ),
        ):
            report = _bpf_owned_prepare_mpls_backend(
                proof={"generation_id": 42, "map_entry_count": 1},
                contexts=active_contexts,
                resident_attach_contexts=resident_contexts,
            )

        self.assertTrue(report["ok"])
        self.assertEqual(
            compile_calls,
            [(active_contexts, 42, False), (resident_contexts, 42, True)],
        )
        self.assertEqual(report["attached_interfaces"], [active_interface])
        self.assertEqual(report["attach_plan"], {active_interface: ["ingress"]})
        self.assertEqual(report["active_materialized_interfaces"], [active_interface])
        self.assertEqual(report["future_horizon_interfaces"], [future_interface])
        self.assertEqual(
            report["compile_capacity_interfaces"],
            [active_interface, future_interface],
        )
        self.assertEqual(
            report["resident_attach_coverage"]["future_interface_classification"],
            "FUTURE_INTERFACE_NOT_MATERIALIZED",
        )
        self.assertEqual(report["future_materialized_attach_eligible_interfaces"], [])
        self.assertEqual(
            report["future_non_materialized_capacity_only_interfaces"],
            [future_interface],
        )
        self.assertFalse(
            report["interface_set_contract"]["future_interfaces_readiness_blocking"]
        )
        attach_mpls.assert_called_once()
        self.assertEqual(attach_mpls.call_args.args[0], active_interface)

    def test_bpf_owned_missing_active_interface_fails_hard(self) -> None:
        def compile_maps(*, contexts, generation_id, offline=False):
            self.assertFalse(offline)
            raise RuntimeError(
                'missing canary interface netns:gw205:ix204: '
                'Device "ix204" does not exist.'
            )

        with (
            patch(
                "linux_mpls_runtime._bpf_owned_compile_mpls_maps_for_contexts",
                side_effect=compile_maps,
            ),
            patch(
                "satellite.bpf_dataplane_canary.collect_environment",
            ) as collect_environment,
        ):
            report = _bpf_owned_prepare_mpls_backend(
                proof={"generation_id": 42, "map_entry_count": 1},
                contexts=(SimpleNamespace(flow_id="active-flow"),),
            )

        collect_environment.assert_not_called()
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "ACTIVE_INTERFACE_BINDING_MISSING")
        self.assertEqual(
            report["classification"],
            "ACTIVE_INTERFACE_BINDING_MISSING",
        )
        self.assertIn("Device \"ix204\" does not exist", report["error"])

    def test_bpf_owned_attach_cardinality_budget_fails_before_attach(self) -> None:
        interfaces = [
            "netns:gw205:ix204",
            "netns:stl-940001:ga-g207s0",
            "netns:stl-940002:fs0001",
        ]

        def compile_maps(*, contexts, generation_id, offline=False):
            self.assertFalse(offline)
            return {
                "attach_plan": {interface: ["ingress"] for interface in interfaces},
                "attached_interfaces": list(interfaces),
                "attached_interface_details": [
                    {
                        "interface_ref": interface,
                        "attach_directions": ["ingress"],
                        "expected_roles": ["active_materialized"],
                    }
                    for interface in interfaces
                ],
                "compiled_mpls_map_counts": {
                    "ingress_classifier_map": 1,
                    "core_lfib_map": 1,
                    "egress_handoff_map": 1,
                },
                "map_conflicts": [],
                "map_conflict_summary": {"conflict_count": 0},
                "label_allocation": {},
                "resolved_mpls_flow_entries": [],
            }

        with (
            patch(
                "linux_mpls_runtime._bpf_owned_compile_mpls_maps_for_contexts",
                side_effect=compile_maps,
            ),
            patch(
                "satellite.bpf_dataplane_canary.collect_environment",
            ) as collect_environment,
            patch("satellite.bpf_mpls_live.compile_mpls_bpf") as compile_bpf,
            patch("satellite.bpf_mpls_live.attach_mpls") as attach_mpls,
        ):
            report = _bpf_owned_prepare_mpls_backend(
                proof={
                    "generation_id": 42,
                    "map_entry_count": 1,
                    "resident_attach_coverage_budget": {
                        "max_attach_interfaces": 2,
                    },
                },
                contexts=(SimpleNamespace(flow_id="active-flow"),),
            )

        collect_environment.assert_not_called()
        compile_bpf.assert_not_called()
        attach_mpls.assert_not_called()
        self.assertFalse(report["ok"])
        self.assertEqual(
            report["reason"],
            "bpf_resident_attach_coverage_budget_exceeded",
        )
        self.assertEqual(
            report["attach_cardinality_budget"]["attach_eligible_count"],
            3,
        )
        self.assertTrue(report["attach_cardinality_budget"]["budget_exceeded"])

    def test_bpf_owned_active_logical_interfaces_bind_successfully(self) -> None:
        active_interface = "netns:gw205:ix204"
        compiled = {
            "attach_plan": {active_interface: ["ingress"]},
            "attached_interfaces": [active_interface],
            "attached_interface_details": [
                {
                    "interface_ref": active_interface,
                    "attach_directions": ["ingress"],
                    "expected_roles": ["active_materialized"],
                }
            ],
            "compiled_mpls_map_counts": {
                "ingress_classifier_map": 1,
                "core_lfib_map": 1,
                "egress_handoff_map": 1,
            },
            "map_conflicts": [],
            "map_conflict_summary": {"conflict_count": 0},
            "label_allocation": {},
            "resolved_mpls_flow_entries": [],
        }

        with (
            patch(
                "linux_mpls_runtime._bpf_owned_compile_mpls_maps_for_contexts",
                return_value=compiled,
            ) as compile_maps,
            patch(
                "satellite.bpf_dataplane_canary.collect_environment",
                return_value={},
            ),
            patch("satellite.bpf_dataplane_canary.attach_blockers", return_value=[]),
            patch(
                "satellite.bpf_mpls_live.compile_mpls_bpf",
                return_value={"returncode": 0},
            ),
            patch("satellite.bpf_mpls_live.rollback_mpls", return_value={"ok": True}),
            patch(
                "satellite.bpf_mpls_live.attach_mpls",
                return_value=[{"returncode": 0, "args": ["tc", "filter", "add"]}],
            ),
            patch(
                "satellite.bpf_mpls_live.load_mpls_maps",
                return_value={
                    "ok": True,
                    "active_generation": 42,
                    "loaded_classifier_count": 1,
                    "loaded_core_lfib_count": 1,
                    "loaded_egress_handoff_count": 1,
                    "loaded_interface_role_count": 1,
                    "active_generation_switch_count": 1,
                },
            ),
            patch(
                "linux_mpls_runtime._read_bpf_owned_interface_roles",
                return_value={"ok": True, "command": {"returncode": 0}},
            ),
            patch(
                "linux_mpls_runtime._bpf_owned_interface_role_completeness",
                return_value={"ok": True},
            ),
        ):
            report = _bpf_owned_prepare_mpls_backend(
                proof={"generation_id": 42, "map_entry_count": 1},
                contexts=(SimpleNamespace(flow_id="active-flow"),),
            )

        compile_maps.assert_called_once()
        self.assertFalse(compile_maps.call_args.kwargs.get("offline", False))
        self.assertTrue(report["ok"])
        self.assertEqual(report["attached_interfaces"], [active_interface])
        self.assertEqual(report["active_materialized_interfaces"], [active_interface])
        self.assertEqual(report["future_horizon_interfaces"], [])

    def test_bpf_owned_fail_closed_reuse_mismatch_forbids_full_attach(
        self,
    ) -> None:
        active_interface = "netns:gw205:ix204"
        compiled = {
            "attach_plan": {active_interface: ["ingress"]},
            "attached_interfaces": [active_interface],
            "attached_interface_details": [
                {
                    "interface_ref": active_interface,
                    "attach_directions": ["ingress"],
                    "expected_roles": ["z_to_g_classifier"],
                    "ifindex": 8,
                }
            ],
            "compiled_mpls_map_counts": {
                "ingress_classifier_map": 1,
                "core_lfib_map": 1,
                "egress_handoff_map": 1,
            },
            "map_conflicts": [],
            "map_conflict_summary": {"conflict_count": 0},
            "label_allocation": {},
            "resolved_mpls_flow_entries": [],
        }
        previous_readiness = {
            "attached_interface_details": [
                {
                    "interface_ref": active_interface,
                    "attach_directions": ["ingress"],
                    "expected_roles": ["g_to_z_classifier"],
                    "ifindex": 8,
                }
            ],
            "prepare_steps": {
                "real_backend_prepare": {
                    "attach_plan": {active_interface: ["ingress"]},
                    "map_capacities": {
                        "classifiers": 128,
                        "lfib": 256,
                        "handoffs": 128,
                    },
                }
            },
        }

        with (
            patch(
                "linux_mpls_runtime._bpf_owned_compile_mpls_maps_for_contexts",
                return_value=compiled,
            ),
            patch(
                "satellite.bpf_dataplane_canary.collect_environment",
                return_value={},
            ),
            patch("satellite.bpf_dataplane_canary.attach_blockers", return_value=[]),
            patch("satellite.bpf_mpls_live.compile_mpls_bpf") as compile_bpf,
            patch("satellite.bpf_mpls_live.attach_mpls") as attach_mpls,
        ):
            report = _bpf_owned_prepare_mpls_backend(
                proof={"generation_id": 42, "map_entry_count": 1},
                contexts=(SimpleNamespace(flow_id="active-flow"),),
                reuse_attached_readiness=previous_readiness,
                fail_closed_on_reuse_miss=True,
            )

        compile_bpf.assert_not_called()
        attach_mpls.assert_not_called()
        self.assertFalse(report["ok"])
        self.assertTrue(report["fail_closed"])
        self.assertEqual(report["blocked_stage"], "bpf_hotpath_attach_reuse")
        self.assertEqual(
            report["reason"],
            "operational_clean_bpf_attach_reuse_not_eligible:"
            "interface_role_set_mismatch",
        )
        self.assertEqual(report["attach_reuse"]["full_attach_count"], 1)

    def test_bpf_owned_attach_reuse_delta_attaches_missing_direction(self) -> None:
        reuse = _bpf_owned_attach_reuse_plan(
            compiled={
                "attach_plan": {
                    "netns:stl-940001:ua-u2015s0": ["ingress", "egress"],
                }
            },
            previous_readiness={
                "attached_interface_details": [
                    {
                        "interface_ref": "netns:stl-940001:ua-u2015s0",
                        "attach_directions": ["ingress"],
                    }
                ]
            },
        )

        self.assertTrue(reuse["reuse_eligible"])
        self.assertEqual(reuse["attach_strategy"], "delta_attach")
        self.assertEqual(
            reuse["outcome_labels"],
            ["EXPLICIT_DELTA_ATTACH_TRANSACTION_REQUIRED"],
        )
        self.assertFalse(reuse["steady_epoch_map_only"])
        self.assertTrue(reuse["interface_set_changed"])
        self.assertEqual(reuse["delta_attach_count"], 1)
        self.assertEqual(
            reuse["delta_attach_plan"],
            {"netns:stl-940001:ua-u2015s0": ["egress"]},
        )
        self.assertEqual(
            reuse["missing_directions"],
            [
                {
                    "interface_ref": "netns:stl-940001:ua-u2015s0",
                    "missing_directions": ["egress"],
                    "previous_directions": ["ingress"],
                    "required_directions": ["egress", "ingress"],
                }
            ],
        )

    def test_bpf_owned_attach_reuse_delta_attaches_missing_interface(self) -> None:
        reuse = _bpf_owned_attach_reuse_plan(
            compiled={
                "attach_plan": {
                    "netns:gw205:ix204": ["ingress"],
                    "netns:stl-940058:ua-u2028s0": ["ingress"],
                }
            },
            previous_readiness={
                "prepare_steps": {
                    "real_backend_prepare": {
                        "attach_plan": {"netns:gw205:ix204": ["ingress"]}
                    }
                }
            },
        )

        self.assertTrue(reuse["reuse_eligible"])
        self.assertEqual(reuse["attach_strategy"], "delta_attach")
        self.assertTrue(reuse["interface_set_changed"])
        self.assertEqual(reuse["reused_attach_count"], 1)
        self.assertEqual(reuse["delta_attach_count"], 1)
        self.assertEqual(
            reuse["delta_attach_interfaces"],
            ["netns:stl-940058:ua-u2028s0"],
        )

    def test_bpf_clsact_lifecycle_metrics_count_delta_idempotency(self) -> None:
        metrics = _bpf_clsact_lifecycle_metrics(
            [
                {"returncode": 0, "clsact_lifecycle_event": "show_before_new_delta"},
                {
                    "returncode": 0,
                    "clsact_lifecycle_event": "delete_skipped_missing_on_new_delta",
                },
                {"returncode": 0, "clsact_lifecycle_event": "add"},
            ],
            delta_attach_interfaces=["netns:stl-940058:ua-u2028s0"],
        )

        self.assertEqual(metrics["clsact_delete_skipped_missing_on_new_delta"], 1)
        self.assertEqual(metrics["clsact_add_count"], 1)
        self.assertEqual(metrics["delta_attach_success_count"], 1)
        self.assertEqual(metrics["delta_attach_failure_count"], 0)

    def test_bpf_clsact_lifecycle_metrics_flags_expected_reuse_loss(self) -> None:
        metrics = _bpf_clsact_lifecycle_metrics(
            [
                {
                    "returncode": 2,
                    "clsact_lifecycle_event": "missing_on_expected_reuse",
                    "interface_ref": {
                        "interface_ref": "netns:stl-940058:fs0058",
                    },
                }
            ],
            delta_attach_interfaces=["netns:stl-940058:fs0058"],
        )

        self.assertEqual(metrics["clsact_missing_on_expected_reuse_count"], 1)
        self.assertEqual(metrics["delta_attach_success_count"], 0)
        self.assertEqual(metrics["delta_attach_failure_count"], 1)

    def test_bpf_owned_reused_attach_clsact_guard_forces_full_attach(self) -> None:
        checked: list[str] = []

        def fake_verify(interface_ref: str):
            checked.append(interface_ref)
            return [
                {
                    "returncode": 0,
                    "clsact_lifecycle_event": "show_before_expected_reuse",
                    "interface_ref": {"interface_ref": interface_ref},
                },
                {
                    "returncode": 2,
                    "clsact_lifecycle_event": "missing_on_expected_reuse",
                    "interface_ref": {"interface_ref": interface_ref},
                },
            ]

        guard = _bpf_owned_reused_attach_clsact_guard(
            attach_reuse={
                "reuse_eligible": True,
                "required_attach_plan": {
                    "netns:gw205:ix204": ["ingress"],
                    "netns:stl-940058:ua-u2028s0": ["ingress"],
                },
                "delta_attach_plan": {
                    "netns:stl-940058:ua-u2028s0": ["ingress"],
                },
                "attach_strategy": "delta_attach",
                "reused_attach_count": 1,
                "delta_attach_count": 1,
                "full_attach_count": 0,
            },
            verify_clsact=fake_verify,
        )

        self.assertEqual(checked, ["netns:gw205:ix204"])
        self.assertFalse(guard["ok"])
        self.assertEqual(guard["reason"], "clsact_missing_on_expected_reuse")
        guarded_reuse = guard["attach_reuse"]
        self.assertFalse(guarded_reuse["reuse_eligible"])
        self.assertEqual(
            guarded_reuse["reuse_rejected_reason"],
            "clsact_missing_on_expected_reuse",
        )
        self.assertEqual(guarded_reuse["attach_strategy"], "full_attach_required")
        self.assertEqual(guarded_reuse["full_attach_count"], 2)
        self.assertEqual(
            guarded_reuse["clsact_missing_on_expected_reuse_interfaces"],
            ["netns:gw205:ix204"],
        )

    def test_bpf_owned_attach_reuse_rejects_ifindex_or_role_mismatch(self) -> None:
        compiled = {
            "attach_plan": {"netns:gw205:ix204": ["ingress"]},
            "attached_interface_details": [
                {
                    "interface_ref": "netns:gw205:ix204",
                    "ifindex": 8,
                    "expected_roles": ["z_to_g_classifier"],
                }
            ],
        }
        previous_readiness = {
            "attached_interface_details": [
                {
                    "interface_ref": "netns:gw205:ix204",
                    "attach_directions": ["ingress"],
                    "ifindex": 7,
                    "expected_roles": ["g_to_z_classifier"],
                }
            ]
        }

        reuse = _bpf_owned_attach_reuse_plan(
            compiled=compiled,
            previous_readiness=previous_readiness,
        )

        self.assertFalse(reuse["reuse_eligible"])
        self.assertEqual(reuse["attach_strategy"], "full_attach_required")
        self.assertEqual(reuse["reuse_rejected_reason"], "ifindex_binding_mismatch")
        self.assertFalse(reuse["steady_epoch_map_only"])
        self.assertEqual(reuse["full_attach_count"], 1)

    def test_bpf_owned_steady_map_reload_skips_live_role_read(self) -> None:
        compiled = {
            "attach_plan": {"netns:gw205:ix204": ["ingress"]},
            "attached_interfaces": ["netns:gw205:ix204"],
            "attached_interface_details": [
                {
                    "interface_ref": "netns:gw205:ix204",
                    "attach_directions": ["ingress"],
                    "expected_roles": ["z_to_g_classifier"],
                }
            ],
            "compiled_mpls_map_counts": {
                "ingress_classifier_map": 1,
                "core_lfib_map": 1,
                "egress_handoff_map": 1,
            },
            "map_conflicts": [],
            "map_conflict_summary": {"conflict_count": 0},
            "label_allocation": {},
            "resolved_mpls_flow_entries": [],
        }
        previous_readiness = {
            "prepare_steps": {
                "real_backend_prepare": {
                    "attach_plan": {"netns:gw205:ix204": ["ingress"]},
                    "map_capacities": {
                        "classifiers": 128,
                        "lfib": 256,
                        "handoffs": 128,
                    },
                }
            },
            "attached_interface_details": [
                {
                    "interface_ref": "netns:gw205:ix204",
                    "attach_directions": ["ingress"],
                    "expected_roles": ["z_to_g_classifier"],
                }
            ],
        }

        with (
            patch(
                "linux_mpls_runtime._bpf_owned_compile_mpls_maps_for_contexts",
                return_value=compiled,
            ),
            patch(
                "satellite.bpf_dataplane_canary.collect_environment",
                return_value={},
            ),
            patch("satellite.bpf_dataplane_canary.attach_blockers", return_value=[]),
            patch(
                "satellite.bpf_mpls_live.verify_mpls_clsact",
                return_value=[
                    {
                        "returncode": 0,
                        "clsact_lifecycle_event": "show_before_expected_reuse",
                        "interface_ref": {"interface_ref": "netns:gw205:ix204"},
                    }
                ],
            ),
            patch(
                "satellite.bpf_mpls_live.load_mpls_maps",
                side_effect=[
                    {
                        "ok": True,
                        "active_generation": 41,
                        "map_write_wall_seconds": 0.03,
                        "map_write_update_count": 3,
                        "active_generation_flip_wall_seconds": 0.0,
                        "active_generation_update_count": 0,
                        "active_generation_switch_count": 0,
                        "total_update_count": 3,
                        "loaded_classifier_count": 1,
                        "loaded_core_lfib_count": 1,
                        "loaded_egress_handoff_count": 1,
                        "loaded_interface_role_count": 1,
                        "outcome_labels": ["RUNTIME_MAP_DIFF_UPDATE_USED"],
                    },
                    {
                        "ok": True,
                        "active_generation": 42,
                        "map_write_wall_seconds": 0.02,
                        "map_write_update_count": 0,
                        "active_generation_flip_wall_seconds": 0.01,
                        "active_generation_update_count": 1,
                        "active_generation_switch_count": 1,
                        "total_update_count": 1,
                        "loaded_classifier_count": 1,
                        "loaded_core_lfib_count": 1,
                        "loaded_egress_handoff_count": 1,
                        "loaded_interface_role_count": 1,
                        "outcome_labels": [
                            "RUNTIME_PRELOADED_GENERATION_USED",
                            "RUNTIME_ACTIVE_GENERATION_FLIP_ONLY",
                            "RUNTIME_NOOP_MAP_WRITES_SKIPPED",
                        ],
                    },
                ],
            ) as load_maps,
            patch("linux_mpls_runtime._read_bpf_owned_interface_roles") as live_roles,
        ):
            report = _bpf_owned_prepare_mpls_backend(
                proof={"generation_id": 42, "map_entry_count": 1},
                contexts=(),
                reuse_attached_readiness=previous_readiness,
                fail_closed_on_reuse_miss=True,
            )

        live_roles.assert_not_called()
        self.assertEqual(load_maps.call_count, 2)
        self.assertEqual(
            load_maps.call_args_list[0].args[0]["bpf_runtime_update_phase"],
            "preactivation_preload",
        )
        self.assertTrue(load_maps.call_args_list[0].args[0]["defer_active_generation_flip"])
        self.assertEqual(
            load_maps.call_args_list[1].args[0]["bpf_runtime_update_phase"],
            "steady_map_only",
        )
        self.assertTrue(report["ok"])
        self.assertEqual(report["prepare_mode"], "reuse_attached_bpf_map_reload")
        self.assertTrue(report["preactivation_preload_scheduled"])
        self.assertIn("RUNTIME_PRELOAD_SCHEDULED", report["outcome_labels"])
        self.assertEqual(report["full_attach_count"], 0)
        self.assertEqual(report["timing_breakdown"]["bpf_attach_seconds"], 0.0)
        self.assertEqual(report["timing_breakdown"]["bpf_live_role_read_seconds"], 0.0)
        self.assertEqual(report["timing_breakdown"]["bpf_map_write_seconds"], 0.02)
        self.assertEqual(report["timing_breakdown"]["bpf_map_write_update_count"], 0)
        self.assertEqual(report["timing_breakdown"]["bpf_map_update_count"], 1)
        self.assertEqual(
            report["timing_breakdown"]["active_generation_flip_seconds"],
            0.01,
        )
        self.assertEqual(report["timing_breakdown"]["active_generation_update_count"], 1)
        self.assertEqual(report["active_generation_switch_count"], 1)
        self.assertTrue(
            report["steps"]["live_interface_roles_after_map_load"]["skipped"]
        )

    def test_bpf_owned_steady_map_reload_failure_fail_closes_without_full_attach(
        self,
    ) -> None:
        compiled = {
            "attach_plan": {"netns:gw205:ix204": ["ingress"]},
            "attached_interfaces": ["netns:gw205:ix204"],
            "attached_interface_details": [
                {
                    "interface_ref": "netns:gw205:ix204",
                    "attach_directions": ["ingress"],
                    "expected_roles": ["z_to_g_classifier"],
                }
            ],
            "compiled_mpls_map_counts": {
                "ingress_classifier_map": 1,
                "core_lfib_map": 1,
                "egress_handoff_map": 1,
            },
            "map_conflicts": [],
            "map_conflict_summary": {"conflict_count": 0},
            "label_allocation": {},
            "resolved_mpls_flow_entries": [],
        }
        previous_readiness = {
            "prepare_steps": {
                "real_backend_prepare": {
                    "attach_plan": {"netns:gw205:ix204": ["ingress"]},
                    "map_capacities": {
                        "classifiers": 128,
                        "lfib": 256,
                        "handoffs": 128,
                    },
                }
            },
            "attached_interface_details": [
                {
                    "interface_ref": "netns:gw205:ix204",
                    "attach_directions": ["ingress"],
                    "expected_roles": ["z_to_g_classifier"],
                }
            ],
        }

        with (
            patch(
                "linux_mpls_runtime._bpf_owned_compile_mpls_maps_for_contexts",
                return_value=compiled,
            ),
            patch(
                "satellite.bpf_dataplane_canary.collect_environment",
                return_value={},
            ),
            patch("satellite.bpf_dataplane_canary.attach_blockers", return_value=[]),
            patch(
                "satellite.bpf_mpls_live.verify_mpls_clsact",
                return_value=[
                    {
                        "returncode": 0,
                        "clsact_lifecycle_event": "show_before_expected_reuse",
                        "interface_ref": {"interface_ref": "netns:gw205:ix204"},
                    }
                ],
            ),
            patch(
                "satellite.bpf_mpls_live.load_mpls_maps",
                return_value={
                    "ok": False,
                    "reason": "active_generation_update_failed",
                },
            ),
            patch("satellite.bpf_mpls_live.attach_mpls") as attach_mpls,
        ):
            report = _bpf_owned_prepare_mpls_backend(
                proof={"generation_id": 42, "map_entry_count": 1},
                contexts=(),
                reuse_attached_readiness=previous_readiness,
                fail_closed_on_reuse_miss=True,
            )

        attach_mpls.assert_not_called()
        self.assertFalse(report["ok"])
        self.assertTrue(report["fail_closed"])
        self.assertEqual(report["blocked_stage"], "bpf_hotpath_map_reload")
        self.assertEqual(report["reason"], "operational_clean_bpf_map_reload_failed")
        self.assertEqual(report["attach_reuse"]["reason"], "map_reload_failed_before_full_prepare")
        self.assertEqual(
            report["steps"]["reuse_map_load_attempt"]["reason"],
            "active_generation_update_failed",
        )

    def test_parameter_batch_reclassification_keeps_pure_parameter_batch(self) -> None:
        result = _parameter_batch_reclassification(
            classification="parameter_epoch",
            planned_operations=[
                {"operation": "update_slot_parameters", "slot_name": "ut1"}
            ],
            runtime_update_plan=SimpleNamespace(to_dict=lambda: {"node_subplans": {}}),
            trigger_reasons=["bandwidth_threshold_exceeded"],
        )

        self.assertFalse(result["parameter_batch_reclassified_to_runtime"])
        self.assertEqual(result["reclassification_reason"], "pure_parameter_update_batch")
        self.assertEqual(result["parameter_update_count"], 1)

    def test_parameter_batch_reclassification_marks_noop(self) -> None:
        result = _parameter_batch_reclassification(
            classification="parameter_epoch",
            planned_operations=[],
            runtime_update_plan=SimpleNamespace(to_dict=lambda: {"node_subplans": {}}),
            trigger_reasons=[],
        )

        self.assertFalse(result["parameter_batch_reclassified_to_runtime"])
        self.assertEqual(result["reclassification_reason"], "noop_parameter_update_batch")

    def test_parameter_batch_reclassification_reports_structural_contamination(self) -> None:
        result = _parameter_batch_reclassification(
            classification="parameter_epoch",
            planned_operations=[
                {"operation": "rebind_slot_attachment", "slot_name": "ut1"},
                {"operation": "update_slot_parameters", "slot_name": "ut2"},
            ],
            runtime_update_plan=SimpleNamespace(
                to_dict=lambda: {
                    "node_subplans": {
                        "stl-1": {"actions": [{"kind": "gz"}, {"kind": "gz"}]}
                    }
                }
            ),
            trigger_reasons=["gz_fec_path_change", "zone_active_serving_satellite_change"],
        )

        self.assertTrue(result["parameter_batch_reclassified_to_runtime"])
        self.assertEqual(
            result["reclassification_reason"],
            "structural_operations_in_parameter_update_batch",
        )
        self.assertEqual(result["slot_rebind_count"], 1)
        self.assertEqual(result["parameter_update_count"], 1)
        self.assertEqual(result["changed_gz_fec_count"], 2)
        self.assertEqual(result["changed_zone_anchor_path_count"], 2)

    def test_parameter_batch_reclassification_blocks_hidden_bpf_runtime_work(self) -> None:
        result = _parameter_batch_reclassification(
            classification="parameter_epoch",
            planned_operations=[
                {"operation": "update_slot_parameters", "slot_name": "ut1"},
            ],
            runtime_update_plan=SimpleNamespace(
                to_dict=lambda: {
                    "node_subplans": {
                        "stl-1": {
                            "actions": [
                                {"kind": "bpf_generation_content_change"},
                                {"kind": "gz"},
                            ]
                        }
                    }
                }
            ),
            trigger_reasons=["bpf_generation_content_change"],
        )

        self.assertTrue(result["parameter_batch_reclassified_to_runtime"])
        self.assertEqual(
            result["reclassification_reason"],
            "runtime_update_actions_in_parameter_update_batch",
        )
        self.assertEqual(result["runtime_update_action_count"], 2)
        self.assertEqual(result["structural_operation_counts"], {})

    def test_parameter_update_batch_accounting_splits_update_costs(self) -> None:
        operations = [
            {
                "operation": "update_slot_parameters",
                "slot_type": "ut_access",
                "source_runtime_parameters": {
                    "delay_ms": 8.0,
                    "loss_percent": 0.0,
                    "routing_cost": 9,
                },
                "target_runtime_parameters": {
                    "delay_ms": 8.0,
                    "loss_percent": 0.0,
                    "routing_cost": 10,
                },
            },
            {
                "operation": "update_slot_parameters",
                "slot_type": "gw_access",
                "target_runtime_parameters": {"delay_ms": 8.0},
            },
            {
                "operation": "update_slot_parameters",
                "slot_type": "ut_access",
                "target_runtime_parameters": {"loss_percent": 0.2},
            },
            {
                "operation": "update_slot_parameters",
                "slot_type": "cross_plane_isl",
                "bpf_parameter_map_update": True,
            },
            {"operation": "rebind_slot_attachment", "slot_type": "ut_access"},
        ]

        accounting = _parameter_update_batch_accounting(
            planned_operations=operations,
            apply_results=[
                {
                    "operation": "update_slot_parameters",
                    "slot_type": "gw_access",
                    "slot_name": "gw1",
                    "qdisc_required": True,
                    "qdisc_apply_wall_seconds": 0.4,
                    "metadata_update_wall_seconds": 0.02,
                },
                {
                    "operation": "update_slot_parameters",
                    "slot_type": "ut_access",
                    "slot_name": "ut2",
                    "qdisc_required": True,
                    "qdisc_apply_wall_seconds": 0.9,
                    "metadata_update_wall_seconds": 0.03,
                },
                {
                    "operation": "update_slot_parameters",
                    "slot_type": "ut_access",
                    "slot_name": "ut1",
                    "qdisc_required": False,
                    "metadata_update_wall_seconds": 0.01,
                },
                {
                    "operation": "rebind_slot_attachment",
                    "slot_type": "ut_access",
                    "slot_name": "ut1",
                    "wall_seconds": 0.5,
                },
            ],
            host_apply_wall_seconds=2.0,
        )

        self.assertEqual(_parameter_update_category(operations[0]), "metadata_only")
        self.assertEqual(_parameter_update_category(operations[1]), "qdisc_required")
        self.assertEqual(_parameter_update_category(operations[2]), "qdisc_required")
        self.assertEqual(
            _parameter_update_category(operations[3]),
            "bpf_parameter_map_update",
        )
        self.assertEqual(
            _parameter_update_category(operations[4]),
            "topology_rebind_required",
        )
        self.assertEqual(accounting["metadata_only_count"], 1)
        self.assertEqual(accounting["qdisc_required_count"], 2)
        self.assertEqual(accounting["applied_qdisc_required_count"], 2)
        self.assertEqual(accounting["bpf_parameter_map_update_count"], 1)
        self.assertEqual(accounting["topology_rebind_required_count"], 1)
        self.assertEqual(accounting["applied_topology_rebind_required_count"], 1)
        self.assertAlmostEqual(accounting["topology_rebind_cumulative_seconds"], 0.5)
        self.assertAlmostEqual(accounting["topology_rebind_wall_seconds"], 0.5)
        self.assertAlmostEqual(
            accounting["topology_rebind_wall_to_cumulative_ratio"],
            1.0,
        )
        self.assertEqual(accounting["qdisc_command_count"], 2)
        self.assertEqual(accounting["qdisc_timed_command_count"], 2)
        self.assertEqual(accounting["qdisc_target_group_count"], 2)
        self.assertEqual(accounting["qdisc_target_key_sources"], {"slot": 2})
        self.assertEqual(
            accounting["planned_slot_type_category_counts"]["ut_access"],
            {"metadata_only": 1, "qdisc_required": 1, "topology_rebind_required": 1},
        )
        self.assertEqual(
            accounting["applied_slot_type_category_counts"]["ut_access"],
            {
                "metadata_only": 1,
                "qdisc_required": 1,
                "topology_rebind_required": 1,
            },
        )
        self.assertEqual(accounting["planned_qdisc_slot_type_counts"], {"gw_access": 1, "ut_access": 1})
        self.assertEqual(accounting["applied_qdisc_slot_type_counts"], {"gw_access": 1, "ut_access": 1})
        self.assertAlmostEqual(accounting["qdisc_cumulative_seconds"], 1.3)
        self.assertAlmostEqual(accounting["qdisc_wall_seconds"], 1.3)
        self.assertAlmostEqual(accounting["qdisc_wall_to_cumulative_ratio"], 1.0)
        self.assertAlmostEqual(accounting["metadata_cumulative_seconds"], 0.06)
        self.assertAlmostEqual(accounting["metadata_wall_seconds"], 0.06)

        parallel_accounting = _parameter_update_batch_accounting(
            planned_operations=operations[:2],
            apply_results=[
                {
                    "operation": "update_slot_parameters",
                    "slot_type": "gw_access",
                    "slot_name": "gw1",
                    "qdisc_required": True,
                    "qdisc_apply_wall_seconds": 0.8,
                },
                {
                    "operation": "update_slot_parameters",
                    "slot_type": "gw_access",
                    "slot_name": "gw2",
                    "qdisc_required": True,
                    "qdisc_apply_wall_seconds": 0.7,
                },
            ],
            host_apply_wall_seconds=0.9,
        )
        self.assertAlmostEqual(parallel_accounting["qdisc_cumulative_seconds"], 1.5)
        self.assertAlmostEqual(parallel_accounting["qdisc_wall_seconds"], 0.9)

    def test_slot_capacity_audit_blocks_under_provisioned_inventory(self) -> None:
        audit = _slot_capacity_audit(
            topology={
                "slot_inventory": {
                    "ut_access": [{"slot": "ut-1"}],
                    "gateway_access": [{"slot": "gw-1"}],
                    "same_plane_isl": [{"slot": "sp-1"}],
                    "cross_plane_isl": [{"slot": "cp-1"}],
                }
            },
            resource_budget={
                "policy": {"ut_slot_margin": 1, "gateway_slot_margin": 1},
            },
            temporal_plan={
                "orbit_samples": [
                    {"slice_id": "slice-1"},
                    {"slice_id": "slice-2"},
                ],
                "preview_update_counters": {
                    "ut_serving_satellite_change_count": 1,
                    "gw_selection_change_count": 1,
                },
            },
            planner_preview={
                "snapshots": [
                    {
                        "slice_id": "slice-1",
                        "gateway_exit_summary": {
                            "ut1": {
                                "serving_satellite_id": "sat-a",
                                "selected_gateway_id": "gw1",
                                "selected_gateway_satellite_id": "sat-a",
                            }
                        },
                    },
                    {
                        "slice_id": "slice-2",
                        "gateway_exit_summary": {
                            "ut1": {
                                "serving_satellite_id": "sat-b",
                                "selected_gateway_id": "gw1",
                                "selected_gateway_satellite_id": "sat-b",
                            }
                        },
                    },
                ]
            },
            snapshot_preview={
                "snapshots": [
                    {
                        "time_slice": {"slice_id": "slice-1"},
                        "active_links": [
                            {"slot_type": "same_plane_isl", "binding": {"satellite_a_id": "sat-a", "satellite_b_id": "sat-b"}},
                            {"slot_type": "cross_plane_isl", "binding": {"satellite_a_id": "sat-a", "satellite_b_id": "sat-c"}},
                        ],
                    },
                    {
                        "time_slice": {"slice_id": "slice-2"},
                        "active_links": [
                            {"slot_type": "same_plane_isl", "binding": {"satellite_a_id": "sat-a", "satellite_b_id": "sat-b"}},
                            {"slot_type": "cross_plane_isl", "binding": {"satellite_a_id": "sat-b", "satellite_b_id": "sat-d"}},
                        ],
                    },
                ]
            },
        )

        self.assertFalse(audit["passed"])
        self.assertEqual(audit["deficit_by_type"]["ut_access"], 1)
        self.assertEqual(audit["deficit_by_type"]["gw_access"], 1)
        self.assertEqual(audit["deficit_by_type"]["cross_plane_isl"], 1)
        self.assertIn("cross_plane_isl", audit["deficit_types"])
        self.assertEqual(
            audit["calculation_basis"]["source"],
            "selected_workload_event_timeline_horizon",
        )
        self.assertEqual(
            audit["calculation_basis"]["cross_plane_required_rule"],
            "distinct_cross_plane_link_identities_over_event_timeline_horizon",
        )

    def test_interface_inventory_contract_treats_xp_cross_plane_as_static_slot(self) -> None:
        interface_ref = "netns:stl-940120:xp120a"
        topology = {
            "slot_inventory": {
                "cross_plane_isl": [
                    {
                        "slot_name": "p003p004-stl-940120-stl-940178-cross-slot0",
                        "slot_type": "cross_plane_isl",
                        "network_name": "p003p004-stl-940120-stl-940178-cross-slot0",
                        "endpoint_a_template": {
                            "node_id": "<runtime-shell-plane-003-satellite>",
                            "interface_name": "xp120a",
                            "ip_address": "10.92.120.1/29",
                        },
                        "endpoint_b_template": {
                            "node_id": "<runtime-shell-plane-004-satellite>",
                            "interface_name": "xp120b",
                            "ip_address": "10.92.120.2/29",
                        },
                        "candidate_pairs": [
                            {
                                "satellite_a_id": "stl-940120",
                                "satellite_b_id": "stl-940178",
                            }
                        ],
                    }
                ],
                "same_plane_isl": [],
                "ut_access": [],
                "gateway_access": [],
            }
        }
        link_state = {
            "slots": [
                {
                    "slot_name": "p003p004-stl-940120-stl-940178-cross-slot0",
                    "slot_type": "cross_plane_isl",
                    "occupied": False,
                    "dynamic_endpoints": [],
                    "preconnected_endpoints": [],
                }
            ]
        }
        readiness = {
            "future_non_materialized_capacity_only_interfaces": [interface_ref],
        }

        contract = _interface_inventory_activation_contract(
            topology=topology,
            link_state=link_state,
            readiness_report=readiness,
            interface_refs=[interface_ref],
        )

        self.assertTrue(
            contract["interface_inventory_is_structural_static_within_run"]
        )
        self.assertTrue(contract["horizon_controls_activation_not_existence"])
        category = contract["categories"]["cross_plane_dynamic_isl_slots"]
        self.assertEqual(category["slot_count"], 1)
        self.assertEqual(category["bridge_count"], 1)
        self.assertEqual(category["candidate_resource_count"], 1)
        self.assertEqual(category["candidate_endpoint_count"], 2)
        self.assertEqual(
            category["per_satellite_port_count"]["sample_nodes"]["stl-940120"],
            1,
        )
        self.assertFalse(category["event_timeline_may_create_delete"])
        self.assertTrue(category["event_timeline_only_activates_deactivates"])
        self.assertEqual(category["occupied_slot_count"], 0)
        self.assertEqual(
            contract["static_inventory_materialization"]["scope_slot_count"],
            1,
        )
        self.assertTrue(
            contract["static_inventory_materialization"]["inactive_visibility_passed"]
        )
        audit = contract["interface_audits"][0]
        self.assertTrue(audit["legitimate_preallocated_slot"])
        self.assertEqual(
            audit["slot_inventory"]["slot_name"],
            "p003p004-stl-940120-stl-940178-cross-slot0",
        )
        self.assertEqual(
            audit["current_absence_reason"],
            "prepared_resource_scope_did_not_materialize_this_future_candidate_endpoint",
        )
        self.assertTrue(
            audit["should_be_materialized_as_inactive_slot_for_resident_attach"]
        )
        self.assertIn(
            "no active_generation entry for current generation",
            audit["required_invisibility_before_activation"],
        )

    def test_static_inventory_materialization_includes_future_cross_plane_candidates(self) -> None:
        topology = {
            "slot_inventory": {
                "cross_plane_isl": [
                    {
                        "slot_name": "p003p004-stl-940120-stl-940178-cross-slot0",
                        "slot_type": "cross_plane_isl",
                        "endpoint_a_template": {"interface_name": "xp120a"},
                        "endpoint_b_template": {"interface_name": "xp120b"},
                        "candidate_pairs": [
                            {
                                "satellite_a_id": "stl-940120",
                                "satellite_a_direction": "right",
                                "satellite_b_id": "stl-940178",
                                "satellite_b_direction": "left",
                            }
                        ],
                    }
                ],
                "ut_access": [
                    {
                        "slot_name": "ut2001-access-slot0",
                        "slot_type": "ut_access",
                        "candidate_satellite_ids": ["sat-a"],
                    }
                ],
            }
        }

        static_scope = _static_inventory_materialization_resource_scope_by_slot(
            topology=topology,
            slot_types=("cross_plane_isl",),
        )
        access_scope = {"ut2001-access-slot0": {"satellite:sat-a"}}
        merged = _merge_resource_scope_by_slot(access_scope, static_scope)

        self.assertIn(
            "p003p004-stl-940120-stl-940178-cross-slot0",
            static_scope,
        )
        self.assertEqual(
            len(static_scope["p003p004-stl-940120-stl-940178-cross-slot0"]),
            1,
        )
        self.assertIn("ut2001-access-slot0", merged)
        self.assertIn(
            "p003p004-stl-940120-stl-940178-cross-slot0",
            merged,
        )

    def test_inactive_materialized_cross_plane_slot_remains_invisible_without_fallback(self) -> None:
        topology = {
            "slot_inventory": {
                "cross_plane_isl": [
                    {
                        "slot_name": "cross-slot",
                        "slot_type": "cross_plane_isl",
                        "candidate_pairs": [
                            {
                                "satellite_a_id": "sat-a",
                                "satellite_a_direction": "right",
                                "satellite_b_id": "sat-b",
                                "satellite_b_direction": "left",
                            }
                        ],
                    }
                ]
            }
        }
        link_state = {
            "slots": [
                {
                    "slot_name": "cross-slot",
                    "slot_type": "cross_plane_isl",
                    "occupied": False,
                    "dynamic_endpoints": [],
                    "preconnected_endpoints": [],
                }
            ]
        }

        contract = _static_inventory_materialization_contract(
            topology=topology,
            link_state=link_state,
        )

        self.assertEqual(contract["scope_slot_count"], 1)
        self.assertEqual(contract["scope_resource_count"], 1)
        self.assertTrue(contract["inactive_visibility_passed"])
        report = contract["inactive_slot_reports"][0]
        self.assertFalse(report["occupied"])
        self.assertEqual(report["linux_ip_route_count"], 0)
        self.assertEqual(report["linux_mpls_route_count"], 0)
        self.assertEqual(report["active_generation_entry_count"], 0)
        self.assertFalse(report["reachable_peer_path"])

    def test_inactive_static_materialization_blocks_linux_fallback_routes(self) -> None:
        topology = {
            "slot_inventory": {
                "cross_plane_isl": [
                    {
                        "slot_name": "cross-slot",
                        "slot_type": "cross_plane_isl",
                        "candidate_pairs": [
                            {
                                "satellite_a_id": "sat-a",
                                "satellite_a_direction": "right",
                                "satellite_b_id": "sat-b",
                                "satellite_b_direction": "left",
                            }
                        ],
                    }
                ]
            }
        }

        contract = _static_inventory_materialization_contract(
            topology=topology,
            link_state={
                "slots": [
                    {
                        "slot_name": "cross-slot",
                        "slot_type": "cross_plane_isl",
                        "occupied": False,
                    }
                ]
            },
            linux_ip_routes_by_slot={"cross-slot": ["10.92.120.0/29 dev xp120a"]},
        )

        self.assertFalse(contract["inactive_visibility_passed"])
        self.assertEqual(
            contract["inactive_slot_reports"][0]["linux_ip_route_count"],
            1,
        )

    def test_inactive_static_materialization_blocks_active_generation_entries(self) -> None:
        topology = {
            "slot_inventory": {
                "cross_plane_isl": [
                    {
                        "slot_name": "cross-slot",
                        "slot_type": "cross_plane_isl",
                        "endpoint_a_template": {"interface_name": "xp1a"},
                        "endpoint_b_template": {"interface_name": "xp1b"},
                        "candidate_pairs": [
                            {
                                "satellite_a_id": "sat-a",
                                "satellite_a_direction": "right",
                                "satellite_b_id": "sat-b",
                                "satellite_b_direction": "left",
                            }
                        ],
                    }
                ]
            }
        }

        contract = _static_inventory_materialization_contract(
            topology=topology,
            link_state={
                "slots": [
                    {
                        "slot_name": "cross-slot",
                        "slot_type": "cross_plane_isl",
                        "occupied": False,
                    }
                ]
            },
            active_generation_interfaces={"netns:sat-a:xp1a"},
        )

        self.assertFalse(contract["inactive_visibility_passed"])
        self.assertEqual(
            contract["inactive_slot_reports"][0]["active_generation_entry_count"],
            1,
        )
        self.assertIn(
            "netns:sat-a:xp1a",
            contract["inactive_slot_reports"][0]["endpoint_interface_refs"],
        )

    def test_event_timeline_changes_occupied_not_static_inventory_existence(self) -> None:
        topology = {
            "slot_inventory": {
                "cross_plane_isl": [
                    {
                        "slot_name": "cross-slot",
                        "slot_type": "cross_plane_isl",
                        "candidate_pairs": [
                            {
                                "satellite_a_id": "sat-a",
                                "satellite_b_id": "sat-b",
                            }
                        ],
                    }
                ],
                "same_plane_isl": [],
                "ut_access": [],
                "gateway_access": [],
            }
        }
        inactive = _interface_inventory_activation_contract(
            topology=topology,
            link_state={
                "slots": [
                    {
                        "slot_name": "cross-slot",
                        "slot_type": "cross_plane_isl",
                        "occupied": False,
                    }
                ]
            },
        )
        active = _interface_inventory_activation_contract(
            topology=topology,
            link_state={
                "slots": [
                    {
                        "slot_name": "cross-slot",
                        "slot_type": "cross_plane_isl",
                        "occupied": True,
                    }
                ]
            },
        )

        self.assertEqual(
            inactive["categories"]["cross_plane_dynamic_isl_slots"]["slot_count"],
            active["categories"]["cross_plane_dynamic_isl_slots"]["slot_count"],
        )
        self.assertEqual(
            inactive["static_inventory_materialization"]["scope_slot_count"],
            active["static_inventory_materialization"]["scope_slot_count"],
        )
        self.assertEqual(
            active["categories"]["cross_plane_dynamic_isl_slots"]["occupied_slot_count"],
            1,
        )
        self.assertFalse(
            active["categories"]["cross_plane_dynamic_isl_slots"][
                "event_timeline_may_create_delete"
            ]
        )

    def test_same_plane_slot_endpoint_bridge_and_port_counts_are_distinct(self) -> None:
        topology = {
            "slot_inventory": {
                "same_plane_isl": [
                    {
                        "slot_name": "fixed-shell-0001",
                        "slot_type": "same_plane_isl",
                        "endpoint_a_template": {"interface_name": "fs0001"},
                        "endpoint_b_template": {"interface_name": "fs0001"},
                        "candidate_pairs": [
                            {
                                "satellite_a_id": "sat-a",
                                "satellite_b_id": "sat-b",
                            }
                        ],
                    },
                    {
                        "slot_name": "fixed-shell-0002",
                        "slot_type": "same_plane_isl",
                        "endpoint_a_template": {"interface_name": "fs0002"},
                        "endpoint_b_template": {"interface_name": "fs0002"},
                        "candidate_pairs": [
                            {
                                "satellite_a_id": "sat-b",
                                "satellite_b_id": "sat-a",
                            }
                        ],
                    },
                ],
                "cross_plane_isl": [],
                "ut_access": [],
                "gateway_access": [],
            }
        }

        contract = _interface_inventory_activation_contract(topology=topology)
        same_plane = contract["categories"]["fixed_isl_slots"]

        self.assertEqual(same_plane["slot_count"], 2)
        self.assertEqual(same_plane["bridge_count"], 2)
        self.assertEqual(same_plane["candidate_resource_count"], 2)
        self.assertEqual(same_plane["candidate_endpoint_count"], 4)
        self.assertEqual(
            same_plane["per_satellite_port_count"]["sample_nodes"]["sat-a"],
            2,
        )
        self.assertIn(
            "348 ring slots imply 696 endpoints",
            same_plane["terminology"]["per_satellite_port_count"],
        )

    def test_interface_inventory_contract_keeps_access_slots_activation_only(self) -> None:
        topology = {
            "slot_inventory": {
                "ut_access": [
                    {
                        "slot_name": "ut2001-access-slot0",
                        "slot_type": "ut_access",
                        "owner_node_id": "ut2001",
                        "candidate_satellite_ids": ["sat-a", "sat-b"],
                        "preconnected_endpoint": {
                            "node_id": "ut2001",
                            "interface_name": "ua-u2001s0",
                        },
                        "dynamic_endpoint_template": {
                            "node_id": "<runtime-satellite>",
                            "interface_name": "ua-s0",
                        },
                    }
                ],
                "gateway_access": [
                    {
                        "slot_name": "gw201-gateway-slot0",
                        "slot_type": "gw_access",
                        "owner_node_id": "gw201",
                        "candidate_satellite_ids": ["sat-a"],
                        "preconnected_endpoint": {
                            "node_id": "gw201",
                            "interface_name": "ga-g201s0",
                        },
                        "dynamic_endpoint_template": {
                            "node_id": "<runtime-satellite>",
                            "interface_name": "ga-s0",
                        },
                    }
                ],
                "same_plane_isl": [],
                "cross_plane_isl": [],
            }
        }
        link_state = {
            "slots": [
                {
                    "slot_name": "ut2001-access-slot0",
                    "slot_type": "ut_access",
                    "occupied": False,
                    "preconnected_endpoints": [{"node_id": "ut2001"}],
                },
                {
                    "slot_name": "gw201-gateway-slot0",
                    "slot_type": "gw_access",
                    "occupied": False,
                    "preconnected_endpoints": [{"node_id": "gw201"}],
                },
            ]
        }

        contract = _interface_inventory_activation_contract(
            topology=topology,
            link_state=link_state,
        )

        ut_category = contract["categories"]["ut_access_slots"]
        gw_category = contract["categories"]["gw_access_slots"]
        self.assertEqual(ut_category["slot_count"], 1)
        self.assertEqual(gw_category["slot_count"], 1)
        self.assertEqual(ut_category["preconnected_endpoint_count"], 1)
        self.assertEqual(gw_category["preconnected_endpoint_count"], 1)
        self.assertFalse(ut_category["event_timeline_may_create_delete"])
        self.assertFalse(gw_category["event_timeline_may_create_delete"])
        self.assertTrue(ut_category["event_timeline_only_activates_deactivates"])
        self.assertTrue(gw_category["event_timeline_only_activates_deactivates"])
        self.assertEqual(
            ut_category["candidate_satellite_fanout"][
                "max_candidate_satellites_per_slot"
            ],
            2,
        )

    def test_management_control_interfaces_are_outside_event_timeline_dataplane(self) -> None:
        contract = _interface_inventory_activation_contract(
            topology={"slot_inventory": {}},
            link_state={"slots": []},
        )

        category = contract["categories"]["management_control_interfaces"]
        self.assertEqual(
            category["inventory_category"],
            "compose_management_static_ground",
        )
        self.assertFalse(category["event_timeline_may_create_delete"])
        self.assertFalse(category["event_timeline_only_activates_deactivates"])
        self.assertIn(
            "not part of MPLS dataplane BPF attach",
            category["bpf_attached_at_startup"],
        )

    def test_connectivity_probe_can_warm_active_access_links(self) -> None:
        context = SimpleNamespace(
            flow_id="flow-a",
            serving_satellite_id="stl-950001",
            selected_gateway_id="gw1",
            target_host_node_id="as154-host-new",
            ping_target_ip="10.154.0.129",
            validation_targets=(
                {"node_id": "as154-host-new", "ip_address": "10.154.0.129"},
            ),
            ut_access_ip="10.10.0.101",
            ut_access_peer_ip="10.10.0.1",
            ut_id="ut101",
        )
        session = SimpleNamespace(
            contexts=(context,),
            target_slice={"slice_id": "slice-1"},
            backend={},
        )

        with (
            patch(
                "linux_mpls_runtime._warm_direct_access_link",
                return_value={"wall_seconds": 0.25},
            ) as warm,
            patch(
                "linux_mpls_runtime._ping_validation_targets",
                return_value=(
                    [
                        {
                            "flow_id": "flow-a",
                            "success": True,
                        }
                    ],
                    0.0,
                ),
            ),
            patch(
                "linux_mpls_runtime._ground_gateway_sanity_probe",
                return_value={"ok": True, "blockers": [], "flow_results": []},
            ),
        ):
            result = MinimalRealLinuxMplsInstaller().probe_connectivity(
                session,
                warm_access_links=True,
            )

        self.assertTrue(result["ping_success"])
        self.assertEqual(result["access_link_warmup_wall_seconds"], 0.25)
        warm.assert_called_once_with(
            node_id="ut101",
            target_ip="10.10.0.1",
            phase="observe",
            description="warm direct UT access link before connectivity probe",
        )

    def test_connectivity_probe_can_skip_ground_gateway_sanity_for_lightweight_parameter_validation(
        self,
    ) -> None:
        context = SimpleNamespace(
            flow_id="flow-a",
            serving_satellite_id="stl-950001",
            selected_gateway_id="gw1",
            target_host_node_id="as154-host-new",
            ping_target_ip="10.154.0.129",
            validation_targets=(
                {"node_id": "as154-host-new", "ip_address": "10.154.0.129"},
            ),
            ut_access_ip="10.10.0.101",
            ut_access_peer_ip="10.10.0.1",
            ut_id="ut101",
        )
        session = SimpleNamespace(
            contexts=(context,),
            target_slice={"slice_id": "slice-1"},
            backend={},
        )

        with (
            patch("linux_mpls_runtime._ground_gateway_sanity_probe") as sanity,
            patch(
                "linux_mpls_runtime._ping_validation_targets",
                return_value=(
                    [
                        {
                            "flow_id": "flow-a",
                            "success": True,
                        }
                    ],
                    0.0,
                ),
            ),
        ):
            result = MinimalRealLinuxMplsInstaller().probe_connectivity(
                session,
                include_ground_gateway_sanity=False,
            )

        sanity.assert_not_called()
        self.assertTrue(result["ping_success"])
        self.assertFalse(result["ground_gateway_sanity"]["enabled"])
        self.assertEqual(
            result["ground_gateway_sanity"]["skip_reason"],
            "parameter_epoch_lightweight_validation",
        )

    def test_connectivity_probe_runs_ground_gateway_sanity_by_default(self) -> None:
        context = SimpleNamespace(
            flow_id="flow-a",
            serving_satellite_id="stl-950001",
            selected_gateway_id="gw1",
            target_host_node_id="as154-host-new",
            ping_target_ip="10.154.0.129",
            validation_targets=(
                {"node_id": "as154-host-new", "ip_address": "10.154.0.129"},
            ),
            ut_access_ip="10.10.0.101",
            ut_access_peer_ip="10.10.0.1",
            ut_id="ut101",
        )
        session = SimpleNamespace(
            contexts=(context,),
            target_slice={"slice_id": "slice-1"},
            backend={},
        )

        with (
            patch(
                "linux_mpls_runtime._ground_gateway_sanity_probe",
                return_value={"ok": True, "blockers": [], "flow_results": []},
            ) as sanity,
            patch(
                "linux_mpls_runtime._ping_validation_targets",
                return_value=(
                    [
                        {
                            "flow_id": "flow-a",
                            "success": True,
                        }
                    ],
                    0.0,
                ),
            ),
        ):
            result = MinimalRealLinuxMplsInstaller().probe_connectivity(session)

        sanity.assert_called_once()
        self.assertTrue(result["ping_success"])
        self.assertTrue(result["ground_gateway_sanity"]["ok"])

    def test_bpf_owned_generation_mismatch_continues_to_strict_prepare(self) -> None:
        previous_readiness = {
            "ready": True,
            "ok": True,
            "generation_id": 41,
            "active_generation": 41,
        }
        proof = {
            "schema_version": "skyseed-bpf-owned-readiness-v1",
            "logical_intent_ready": True,
            "generation_id": 42,
            "attached_interfaces": ["logical:gw1:ix0"],
        }
        prepare_report = {
            "ok": True,
            "reason": "",
            "attached_interfaces": ["netns:gw1:ix0"],
            "compiled_mpls_map_counts": {
                "ingress_classifier_map": 1,
                "core_lfib_map": 1,
                "egress_handoff_map": 1,
            },
            "prepare_mode": "reuse_attached_bpf_map_reload",
            "attach_reuse": {"reuse_eligible": True},
        }

        with (
            patch(
                "linux_mpls_runtime._prove_bpf_owned_readiness",
                return_value={
                    **previous_readiness,
                    "ready": False,
                    "ok": False,
                    "readiness_mismatch_reason": (
                        "bpf_owned_readiness_active_generation_mismatch"
                    ),
                    "fallback_to_linux": True,
                },
            ),
            patch(
                "linux_mpls_runtime._derive_bpf_owned_readiness_proof",
                return_value=proof,
            ),
            patch(
                "linux_mpls_runtime._bpf_owned_host_interfaces_for_contexts",
                return_value=["netns:gw1:ix0"],
            ),
            patch(
                "linux_mpls_runtime._bpf_owned_prepare_mpls_backend",
                return_value=prepare_report,
            ) as prepare,
            patch(
                "linux_mpls_runtime._bpf_owned_readiness_mismatch_reason",
                return_value="",
            ),
        ):
            result = _prepare_bpf_owned_readiness(
                runtime_update_plan=SimpleNamespace(),
                topology_path=Path("topology.json"),
                contexts=(SimpleNamespace(),),
                bpf_owned_readiness=previous_readiness,
            )

        prepare.assert_called_once()
        self.assertIs(prepare.call_args.kwargs["reuse_attached_readiness"], previous_readiness)
        self.assertFalse(prepare.call_args.kwargs["fail_closed_on_reuse_miss"])
        self.assertTrue(result["ready"])
        self.assertFalse(result["fallback_to_linux"])
        self.assertEqual(
            result["previous_readiness_mismatch_reason"],
            "bpf_owned_readiness_active_generation_mismatch",
        )
        self.assertEqual(
            result["prepare_steps"]["real_backend_prepare"]["prepare_mode"],
            "reuse_attached_bpf_map_reload",
        )

    def test_bpf_owned_readiness_passes_resident_attach_contexts(self) -> None:
        proof = {
            "schema_version": "skyseed-bpf-owned-readiness-v1",
            "logical_intent_ready": True,
            "generation_id": 42,
            "attached_interfaces": ["logical:gw1:ix0"],
        }
        prepare_report = {
            "ok": True,
            "reason": "",
            "attached_interfaces": ["netns:gw1:ix0"],
            "active_materialized_interfaces": ["netns:gw1:ix0"],
            "resident_attach_eligible_interfaces": [],
            "future_horizon_interfaces": ["netns:sat2:if2"],
            "compile_capacity_interfaces": ["netns:gw1:ix0", "netns:sat2:if2"],
            "interface_set_contract": {
                "future_interface_classification": "FUTURE_INTERFACE_NOT_MATERIALIZED",
                "future_interfaces_readiness_blocking": False,
            },
            "resident_attach_coverage": {
                "enabled": True,
                "added_interface_count": 1,
                "future_horizon_interfaces": ["netns:sat2:if2"],
                "future_interface_classification": "FUTURE_INTERFACE_NOT_MATERIALIZED",
            },
            "compiled_mpls_map_counts": {
                "ingress_classifier_map": 1,
                "core_lfib_map": 1,
                "egress_handoff_map": 1,
            },
            "prepare_mode": "full_attach_and_map_load",
            "attach_reuse": {"reuse_eligible": False},
        }
        resident_contexts = (SimpleNamespace(flow_id="future-flow"),)

        with (
            patch(
                "linux_mpls_runtime._derive_bpf_owned_readiness_proof",
                return_value=proof,
            ),
            patch(
                "linux_mpls_runtime._bpf_owned_host_interfaces_for_contexts",
                return_value=["netns:gw1:ix0"],
            ) as host_interfaces,
            patch(
                "linux_mpls_runtime._bpf_owned_prepare_mpls_backend",
                return_value=prepare_report,
            ) as prepare,
            patch(
                "linux_mpls_runtime._bpf_owned_readiness_mismatch_reason",
                return_value="",
            ),
        ):
            result = _prepare_bpf_owned_readiness(
                runtime_update_plan=SimpleNamespace(),
                topology_path=Path("topology.json"),
                contexts=(SimpleNamespace(flow_id="current-flow"),),
                resident_attach_contexts=resident_contexts,
                bpf_owned_readiness=None,
            )

        prepare.assert_called_once()
        self.assertEqual(
            host_interfaces.call_args.kwargs["contexts"][0].flow_id,
            "current-flow",
        )
        self.assertIs(
            prepare.call_args.kwargs["resident_attach_contexts"],
            resident_contexts,
        )
        self.assertEqual(
            result["resident_attach_coverage"]["added_interface_count"],
            1,
        )
        self.assertEqual(
            result["attached_interfaces"],
            ["netns:gw1:ix0"],
        )
        self.assertEqual(result["future_horizon_interfaces"], ["netns:sat2:if2"])
        self.assertFalse(
            result["interface_set_contract"]["future_interfaces_readiness_blocking"]
        )

    def test_bpf_owned_readiness_can_fail_closed_on_reuse_miss(self) -> None:
        previous_readiness = {
            "ready": True,
            "ok": True,
            "generation_id": 41,
            "active_generation": 41,
        }
        proof = {
            "schema_version": "skyseed-bpf-owned-readiness-v1",
            "logical_intent_ready": True,
            "generation_id": 42,
            "attached_interfaces": ["logical:gw1:ix0"],
        }
        prepare_report = {
            "ok": False,
            "ready": False,
            "reason": "operational_clean_bpf_attach_reuse_not_eligible:missing_attach",
            "fail_closed": True,
            "blocked_stage": "bpf_hotpath_attach_reuse",
            "prepare_mode": "reuse_attached_bpf_map_reload",
            "attach_reuse": {
                "reuse_eligible": False,
                "reuse_rejected_reason": "missing_attach",
            },
        }

        with (
            patch(
                "linux_mpls_runtime._prove_bpf_owned_readiness",
                return_value={**previous_readiness, "ready": False, "ok": False},
            ),
            patch(
                "linux_mpls_runtime._derive_bpf_owned_readiness_proof",
                return_value=proof,
            ),
            patch(
                "linux_mpls_runtime._bpf_owned_host_interfaces_for_contexts",
                return_value=["netns:gw1:ix0"],
            ),
            patch(
                "linux_mpls_runtime._bpf_owned_prepare_mpls_backend",
                return_value=prepare_report,
            ) as prepare,
            patch(
                "linux_mpls_runtime._bpf_owned_readiness_mismatch_reason",
                return_value="",
            ),
        ):
            result = _prepare_bpf_owned_readiness(
                runtime_update_plan=SimpleNamespace(),
                topology_path=Path("topology.json"),
                contexts=(SimpleNamespace(),),
                bpf_owned_readiness=previous_readiness,
                fail_closed_on_reuse_miss=True,
            )

        prepare.assert_called_once()
        self.assertTrue(prepare.call_args.kwargs["fail_closed_on_reuse_miss"])
        self.assertFalse(result["ready"])
        self.assertFalse(result["ok"])
        self.assertTrue(result["prepare_steps"]["real_backend_prepare"]["fail_closed"])
        self.assertEqual(
            result["prepare_steps"]["real_backend_prepare"]["blocked_stage"],
            "bpf_hotpath_attach_reuse",
        )

    def test_bpf_owned_runtime_readiness_failure_message_preserves_blocker(self) -> None:
        message = _bpf_owned_runtime_readiness_failure_message(
            {
                "ready": False,
                "ok": False,
                "reason": "operational_clean_bpf_map_reload_failed",
                "fallback_reason": "operational_clean_bpf_map_reload_failed",
                "prepare_steps": {
                    "real_backend_prepare": {
                        "fail_closed": True,
                        "blocked_stage": "bpf_hotpath_map_reload",
                    }
                },
            }
        )

        self.assertIn("bpf_owned_runtime_readiness_failed", message)
        self.assertIn("reason=operational_clean_bpf_map_reload_failed", message)
        self.assertIn("blocked_stage=bpf_hotpath_map_reload", message)
        self.assertIn("fail_closed=True", message)


if __name__ == "__main__":
    unittest.main()
