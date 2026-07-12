#!/usr/bin/env python3
# encoding: utf-8

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "apps"))

from skyseed_session_check import (
    _operational_iteration_gate,
    format_status_note,
    operational_iteration_profile,
    render_session_check_summary,
)


class SkyseedSessionCheckTests(unittest.TestCase):
    def test_operational_clean_gate_does_not_require_linux_mpls_or_bridge_prepare(
        self,
    ) -> None:
        profile = operational_iteration_profile()
        gate = _operational_iteration_gate(
            readiness={
                "preflight": {
                    "present": True,
                    "scoped_hygiene_ready": True,
                    "preferred_seed310_selected": True,
                    "blocking_reasons": [],
                    "warning_reasons": [
                        "host_mpls_not_ready",
                        "bridge_netfilter_passthrough_not_ready",
                    ],
                }
            },
            profile=profile,
        )

        self.assertTrue(gate["ready"])
        self.assertEqual(gate["blockers"], [])
        self.assertEqual(
            gate["non_blocking_warnings"],
            [
                "bridge_netfilter_passthrough_not_ready",
                "host_mpls_not_ready",
            ],
        )

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
                "preflight": {"warning_reasons": []},
            },
            "operational_iteration_gate": {
                "ready": True,
                "non_blocking_warnings": [],
            },
            "operational_iteration_profile": {
                "profile_id": "rich_s4_operational_clean_bpf_4h_validation",
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
            "operational_iteration_profile=rich_s4_operational_clean_bpf_4h_validation",
            rendered,
        )
        self.assertIn("operational_iteration_warnings=none", rendered)
        self.assertIn("warnings=none", rendered)
        self.assertNotIn("canonical_fast_profile", rendered)
        self.assertNotIn("representative_validation", rendered)

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
                    "warning_reasons": [],
                },
            },
            "operational_iteration_gate": {
                "ready": True,
                "non_blocking_warnings": [],
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
            "rich_s4_operational_clean_bpf_4h_validation",
        )
        self.assertTrue(profile["fresh_prepared_inputs_allowed"])
        self.assertFalse(profile["load_frozen_prepared_bundle_required"])
        self.assertFalse(profile["stale_static_goal_evidence_blocks_iteration"])
        self.assertEqual("env", command[0])
        self.assertIn("PYTHONNOUSERSITE=1", command)
        self.assertTrue(
            any(
                item.startswith("PYTHONPATH=")
                and "/home/seed/seed-emulator" not in item
                for item in command
            )
        )
        self.assertTrue(
            any("run_skyseed_validation_profile.py" in item for item in command)
        )
        self.assertIn("--profile", command)
        self.assertEqual(
            "rich_s4_operational_clean_bpf",
            command[command.index("--profile") + 1],
        )
        contract = dict(profile["resolved_runtime_contract"])
        self.assertEqual(240, contract["horizon_seconds"])
        self.assertEqual(10, contract["sample_interval_seconds"])
        self.assertEqual(10, contract["event_evaluation_interval_seconds"])
        self.assertEqual(4, contract["loop_count"])
        self.assertEqual(30, contract["runtime_ping_observer_seconds"])
        self.assertEqual("operational_clean", contract["runtime_mode"])
        self.assertEqual("bpf_owned", contract["forwarding_backend"])
        self.assertEqual("auto", contract["host_prepare_policy"])
        self.assertNotIn("--load-frozen-prepared-bundle", command)


if __name__ == "__main__":
    unittest.main()
