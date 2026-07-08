#!/usr/bin/env python3
# encoding: utf-8

import unittest

from skyseed_session_check import (
    canonical_fast_long_run_profile,
    format_status_note,
    operational_iteration_profile,
    render_session_check_summary,
)


class SkyseedSessionCheckTests(unittest.TestCase):
    def test_render_session_check_summary_includes_fixed_gate_fields(self) -> None:
        report = {
            "generated_at": "2026-06-10T12:00:00-04:00",
            "git_status": {
                "branch_line": "## main...origin/main [ahead 19]",
                "dirty_entry_count": 3,
            },
            "goal_readiness": {
                "static_evidence_ready": True,
                "long_real_run_ready": False,
                "next_action": "run_host_prepare_then_recheck_preflight_before_real_run",
                "distance_diversity": {},
                "return_path_migration": {},
                "runtime_update_protection": {},
                "timing_classification": {},
                "preflight": {"warning_reasons": ["host_mpls_not_ready"]},
            },
            "operational_iteration_gate": {
                "ready": True,
                "non_blocking_warnings": [],
                "suppressed_legacy_host_runtime_warnings": [
                    "host_mpls_not_ready"
                ],
            },
            "operational_iteration_profile": {
                "profile_id": "s4_h3_240s_bpf_owned_operational_iteration",
            },
            "canonical_fast_long_run_profile": {
                "profile_id": "1shell_172_12ut6gw_gz_2h_fast_representative",
                "representative_validation_profile": {
                    "runtime_epoch_validation_interval": 0,
                    "parameter_epoch_validation_interval": 0,
                    "runtime_epoch_validation_scope": "reapplied_gateway_class_sample",
                },
                "timing_policy": {
                    "actual_network_update_headline": [
                        "semantic_data_plane_mutation_apply_seconds",
                        "node_local_apply_wall_seconds",
                    ],
                    "scheduler_pressure_headline": ["transition_wall_seconds"],
                    "cleanup_optimization_bucket": [
                        "semantic_pre_apply_cleanup_or_blocking_verification_seconds",
                        "node_local_incremental_cleanup_wall_seconds",
                    ],
                },
            },
        }

        rendered = render_session_check_summary(report)

        self.assertIn("Skyseed session check", rendered)
        self.assertIn("branch=## main...origin/main [ahead 19]", rendered)
        self.assertIn("dirty_entries=3", rendered)
        self.assertIn("static_evidence_ready=True", rendered)
        self.assertIn(
            "next_action=run_host_prepare_then_recheck_preflight_before_real_run",
            rendered,
        )
        self.assertIn("operational_iteration_ready=True", rendered)
        self.assertIn(
            "operational_iteration_profile=s4_h3_240s_bpf_owned_operational_iteration",
            rendered,
        )
        self.assertIn("operational_iteration_warnings=none", rendered)
        self.assertNotIn("warnings=host_mpls_not_ready", rendered)
        self.assertIn(
            "canonical_fast_profile=1shell_172_12ut6gw_gz_2h_fast_representative",
            rendered,
        )
        self.assertIn("runtime_epoch_interval=0", rendered)
        self.assertIn("actual_update=semantic_data_plane_mutation_apply_seconds", rendered)

    def test_format_status_note_keeps_compact_session_record(self) -> None:
        report = {
            "generated_at": "2026-06-10T12:00:00-04:00",
            "git_status": {
                "branch_line": "## main...origin/main [ahead 19]",
                "dirty_entry_count": 3,
            },
            "goal_readiness": {
                "static_evidence_ready": True,
                "long_real_run_ready": False,
                "next_action": "run_host_prepare_then_recheck_preflight_before_real_run",
                "preflight": {
                    "warning_reasons": [
                        "host_mpls_not_ready",
                        "bridge_netfilter_passthrough_not_ready",
                    ],
                },
            },
            "operational_iteration_gate": {
                "ready": True,
                "non_blocking_warnings": [],
                "suppressed_legacy_host_runtime_warnings": [
                    "host_mpls_not_ready",
                    "bridge_netfilter_passthrough_not_ready",
                ],
            },
        }

        note = format_status_note(report)

        self.assertIn("## Session Check Log", note)
        self.assertIn("dirty entries `3`", note)
        self.assertIn("long_real_run_ready `False`", note)
        self.assertIn("operational_iteration_ready `True`", note)
        self.assertIn("operational warnings `none`", note)

    def test_operational_iteration_profile_pins_current_240s_route(self) -> None:
        profile = operational_iteration_profile()
        command = list(profile["run_command"])

        self.assertEqual(
            profile["profile_id"],
            "s4_h3_240s_bpf_owned_operational_iteration",
        )
        self.assertTrue(profile["fresh_prepared_inputs_allowed"])
        self.assertFalse(profile["load_frozen_prepared_bundle_required"])
        self.assertFalse(profile["stale_static_goal_evidence_blocks_iteration"])
        self.assertIn("--operational-profile", command)
        self.assertEqual(
            "s4_h3_240s_bpf_owned_clean",
            command[command.index("--operational-profile") + 1],
        )
        contract = dict(profile["resolved_runtime_contract"])
        self.assertEqual(240, contract["horizon_seconds"])
        self.assertEqual(10, contract["sample_interval_seconds"])
        self.assertEqual(10, contract["event_evaluation_interval_seconds"])
        self.assertEqual(3, contract["loop_count"])
        self.assertEqual("operational_clean", contract["runtime_mode"])
        self.assertEqual("bpf_owned", contract["forwarding_backend"])
        self.assertEqual("auto", contract["host_prepare_policy"])
        self.assertNotIn("--load-frozen-prepared-bundle", command)

    def test_canonical_fast_profile_pins_representative_long_run_route(self) -> None:
        profile = canonical_fast_long_run_profile()
        command = list(profile["run_command"])
        validation = dict(profile["representative_validation_profile"])
        timing = dict(profile["timing_policy"])

        self.assertIn("--run-mode", command)
        self.assertIn("real-runtime", command)
        self.assertIn("--max-satellites", command)
        self.assertIn("172", command)
        self.assertIn("--max-uts", command)
        self.assertIn("12", command)
        self.assertIn("--max-gateways", command)
        self.assertIn("6", command)
        self.assertIn("--ut-ids", command)
        self.assertIn("--gateway-ids", command)
        self.assertEqual("", command[command.index("--ut-ids") + 1])
        self.assertEqual("", command[command.index("--gateway-ids") + 1])
        self.assertIn("--ut-catalog", command)
        self.assertEqual(
            "satellite/configs/starlink_shell_ground/one_shell_ut_catalog_12_grouped_valid6_gateways.json",
            command[command.index("--ut-catalog") + 1],
        )
        self.assertIn("--zone-catalog", command)
        self.assertEqual(
            "satellite/configs/starlink_shell_ground/one_shell_zone_catalog_6_valid6_gateways.json",
            command[command.index("--zone-catalog") + 1],
        )
        self.assertIn("--ground-placement", command)
        self.assertEqual("catalog", command[command.index("--ground-placement") + 1])
        self.assertIn("--start-time", command)
        self.assertEqual("2026-06-11T09:57:20Z", command[command.index("--start-time") + 1])
        self.assertIn("--horizon-seconds", command)
        self.assertIn("7200", command)
        self.assertIn("--runtime-mode", command)
        self.assertIn("fast-demo", command)
        self.assertIn("--preflight-policy", command)
        self.assertIn("require-clean", command)
        self.assertIn("--host-prepare-policy", command)
        self.assertIn("auto", command)
        self.assertEqual(0, validation["runtime_epoch_validation_interval"])
        self.assertEqual(0, validation["parameter_epoch_validation_interval"])
        self.assertIn(
            "--runtime-epoch-validation-interval 1",
            validation["diagnostic_flags_not_for_long_run"],
        )
        self.assertIn(
            "semantic_data_plane_mutation_apply_seconds",
            timing["actual_network_update_headline"],
        )
        self.assertIn(
            "transition_wall_seconds",
            timing["do_not_report_as_network_update"],
        )


if __name__ == "__main__":
    unittest.main()
