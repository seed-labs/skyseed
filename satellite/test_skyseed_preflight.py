#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skyseed_preflight import (  # noqa: E402
    PREFLIGHT_PROFILE_DEFAULT,
    PREFLIGHT_PROFILE_LEGACY_MPLS,
    PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED,
    _assess_skyseed_preflight,
    _skipped_host_mpls_readiness,
    cleanup_direct_backend_host_artifacts,
    cleanup_skyseed_environment_residue,
    render_preflight_summary,
)


def _base_checks() -> dict:
    return {
        "docker_toolchain": {
            "docker": {"ok": True},
            "docker_compose": {"ok": True},
        },
        "docker_state": {
            "containers": {
                "compose_project_containers": [],
                "named_skyseed_containers": [],
                "compose_project_state_counts": {
                    "running": 0,
                    "exited": 0,
                    "other": 0,
                },
            },
            "networks": {
                "compose_project_networks": [],
                "named_skyseed_networks": [],
            },
        },
        "compose_processes": {"processes": []},
        "direct_backend_host_artifacts": {"interfaces": []},
        "runtime_lock_files": {"files": []},
        "disk_space": {"free_bytes": 32 * 1024 * 1024 * 1024},
        "python_runtime": {
            "supported_version": True,
            "preferred_seed310_exists": False,
            "preferred_seed310_selected": False,
            "legacy_seed_emulator_pythonpath_override_present": False,
        },
        "host_mpls": {
            "assessment": {
                "ready": True,
                "blocking_reasons": [],
            },
        },
        "bridge_netfilter": {"passthrough_ready": True},
    }


class SkyseedPreflightTests(unittest.TestCase):
    def test_operational_clean_profile_blocks_dirty_environment(self) -> None:
        checks = _base_checks()
        checks["docker_state"]["containers"]["named_skyseed_containers"] = [
            {"name": "stl-1", "state": "exited"}
        ]
        checks["docker_state"]["networks"]["compose_project_networks"] = [
            {"name": "seed_output_net_ix_ix200", "attachment_count": 0}
        ]
        checks["runtime_lock_files"]["files"] = [
            "satellite/output/skyseed_real_runtime_active.lock"
        ]

        assessment = _assess_skyseed_preflight(
            checks,
            profile=PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED,
        )

        self.assertFalse(assessment["ready"])
        self.assertTrue(assessment["clean_environment_required"])
        self.assertFalse(assessment["environment_clean"])
        self.assertIn("skyseed_environment_not_clean", assessment["blocking_reasons"])
        self.assertEqual(
            assessment["cleanliness_counts"]["named_skyseed_containers"],
            1,
        )
        self.assertEqual(
            assessment["cleanliness_counts"]["compose_project_networks"],
            1,
        )
        self.assertEqual(assessment["cleanliness_counts"]["runtime_lock_files"], 1)

    def test_default_profile_keeps_residue_as_warning(self) -> None:
        checks = _base_checks()
        checks["docker_state"]["containers"]["named_skyseed_containers"] = [
            {"name": "stl-1", "state": "exited"}
        ]

        assessment = _assess_skyseed_preflight(
            checks,
            profile=PREFLIGHT_PROFILE_DEFAULT,
        )

        self.assertTrue(assessment["ready"])
        self.assertFalse(assessment["clean_environment_required"])
        self.assertIn(
            "named_skyseed_containers_present",
            assessment["warning_reasons"],
        )
        self.assertNotIn(
            "skyseed_environment_not_clean",
            assessment["blocking_reasons"],
        )

    def test_operational_clean_skips_host_mpls_readiness(self) -> None:
        skipped = _skipped_host_mpls_readiness(
            profile=PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED
        )
        checks = _base_checks()
        checks["host_mpls"] = skipped

        assessment = _assess_skyseed_preflight(
            checks,
            profile=PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED,
        )

        self.assertIsNone(skipped["assessment"]["ready"])
        self.assertTrue(skipped["assessment"]["skipped"])
        self.assertTrue(assessment["ready"])
        self.assertIn("host_mpls_preflight_skipped", assessment["warning_reasons"])
        self.assertNotIn("host_mpls_not_ready", assessment["blocking_reasons"])

    def test_legacy_mpls_profile_blocks_when_mpls_is_not_ready(self) -> None:
        checks = _base_checks()
        checks["host_mpls"] = {
            "assessment": {
                "ready": False,
                "blocking_reasons": ["host_mpls_modules_not_loaded"],
            }
        }

        assessment = _assess_skyseed_preflight(
            checks,
            profile=PREFLIGHT_PROFILE_LEGACY_MPLS,
        )

        self.assertFalse(assessment["ready"])
        self.assertIn("host_mpls_not_ready", assessment["blocking_reasons"])

    def test_cleanup_dry_run_plans_scoped_resources_without_execution(self) -> None:
        checks = _base_checks()
        checks["docker_state"]["containers"]["compose_project_containers"] = [
            {"name": "stl-1", "state": "exited"}
        ]
        checks["docker_state"]["networks"]["compose_project_networks"] = [
            {"name": "seed_output_default", "attachment_count": 0}
        ]
        checks["runtime_lock_files"]["files"] = [
            "satellite/output/skyseed_real_runtime_active.lock"
        ]
        report = {
            "checks": checks,
            "assessment": _assess_skyseed_preflight(
                checks,
                profile=PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED,
            ),
        }

        cleanup = cleanup_skyseed_environment_residue(
            preflight_report=report,
            apply=False,
        )

        self.assertTrue(cleanup["dry_run"])
        self.assertFalse(cleanup["applied"])
        self.assertEqual(cleanup["resource_counts"]["containers"], 1)
        self.assertEqual(cleanup["resource_counts"]["networks"], 1)
        self.assertEqual(cleanup["resource_counts"]["runtime_lock_files"], 1)
        self.assertTrue(
            all(result["executed"] is False for result in cleanup["command_results"])
        )

        report["cleanup_result"] = cleanup
        rendered = render_preflight_summary(report)
        self.assertIn("cleanup_result:", rendered)
        self.assertIn("dry_run=True", rendered)

    def test_cleanup_apply_can_remove_scoped_runtime_lock_file(self) -> None:
        lock_path = Path("satellite/output/test_skyseed_preflight_cleanup.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("stale", encoding="utf-8")
        checks = _base_checks()
        checks["runtime_lock_files"]["files"] = [str(lock_path)]
        report = {
            "checks": checks,
            "assessment": _assess_skyseed_preflight(
                checks,
                profile=PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED,
            ),
        }
        try:
            cleanup = cleanup_skyseed_environment_residue(
                preflight_report=report,
                apply=True,
            )
        finally:
            lock_path.unlink(missing_ok=True)

        self.assertFalse(lock_path.exists())
        self.assertFalse(cleanup["dry_run"])
        self.assertTrue(cleanup["applied"])
        self.assertTrue(cleanup["success"])
        self.assertEqual(cleanup["removed_runtime_lock_files"], [str(lock_path.resolve())])

    def test_direct_backend_cleanup_reports_bounded_progress(self) -> None:
        states = [
            {
                "interfaces": [
                    "sbr0123456789a",
                    "vhs0123456789a",
                    "not-skyseed0",
                ]
            },
            {"interfaces": []},
        ]
        progress = []

        def fake_artifacts() -> dict:
            return states.pop(0)

        class Completed:
            returncode = 0
            stderr = ""

        with patch(
            "skyseed_preflight._direct_backend_host_artifacts",
            side_effect=fake_artifacts,
        ), patch("skyseed_preflight._run", return_value=Completed()):
            cleanup = cleanup_direct_backend_host_artifacts(
                ["sbr0123456789a", "vhs0123456789a", "not-skyseed0"],
                progress_callback=progress.append,
            )

        self.assertEqual(cleanup["requested_interface_count"], 2)
        self.assertEqual(cleanup["removed_interface_count"], 2)
        self.assertEqual(cleanup["remaining_interface_count"], 0)
        self.assertEqual(progress[0]["completed_interface_count"], 0)
        self.assertEqual(progress[-1]["completed_interface_count"], 2)
        self.assertEqual(progress[-1]["removed_interface_count"], 2)
        self.assertEqual(progress[-1]["total_interface_count"], 2)


if __name__ == "__main__":
    unittest.main()
