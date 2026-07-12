#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skyseed_preflight import (  # noqa: E402
    PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED,
    _assess_skyseed_preflight,
    _docker_container_state_project_scoped,
    _docker_network_state_project_scoped,
    cleanup_direct_backend_host_artifacts,
    cleanup_skyseed_environment_residue,
    render_preflight_summary,
    run_skyseed_preflight,
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

    def test_operational_clean_preflight_defers_global_diagnostics(self) -> None:
        with (
            patch("skyseed_preflight._run_jsonable", return_value={"ok": True}),
            patch(
                "skyseed_preflight._docker_container_state_project_scoped",
                return_value={
                    "compose_project_containers": [],
                    "named_skyseed_containers": [],
                    "compose_project_state_counts": {"running": 0, "exited": 0, "other": 0},
                    "scope": "compose_project_label",
                    "full_docker_container_scan": False,
                },
            ) as container_scope,
            patch(
                "skyseed_preflight._docker_network_state_project_scoped",
                return_value={
                    "compose_project_networks": [],
                    "named_skyseed_networks": [],
                    "scope": "compose_project_label",
                    "full_docker_network_scan": False,
                },
            ) as network_scope,
            patch("skyseed_preflight._direct_backend_host_artifacts") as direct_scan,
            patch("skyseed_preflight._bpf_pinned_map_artifacts") as bpf_scan,
            patch("skyseed_preflight._find_compose_outputs") as compose_outputs,
            patch("skyseed_preflight._compose_processes", return_value={"processes": []}),
            patch("skyseed_preflight._runtime_lock_files", return_value={"files": []}),
            patch("skyseed_preflight._disk_space", return_value={"free_bytes": 32 * 1024 * 1024 * 1024}),
            patch("skyseed_preflight._bridge_netfilter", return_value={"passthrough_ready": True}),
            patch(
                "skyseed_preflight._python_runtime",
                return_value={
                    "supported_version": True,
                    "preferred_seed310_exists": False,
                    "preferred_seed310_selected": False,
                    "legacy_seed_emulator_pythonpath_override_present": False,
                },
            ),
        ):
            report = run_skyseed_preflight(
                profile=PREFLIGHT_PROFILE_OPERATIONAL_CLEAN_BPF_OWNED
            )

        container_scope.assert_called_once()
        network_scope.assert_called_once()
        direct_scan.assert_not_called()
        bpf_scan.assert_not_called()
        compose_outputs.assert_not_called()
        self.assertEqual(report["diagnostic_policy"]["mode"], "operational_clean_lightweight")
        self.assertTrue(report["checks"]["direct_backend_host_artifacts"]["skipped"])
        self.assertTrue(report["checks"]["bpf_pinned_map_artifacts"]["skipped"])
        self.assertEqual(
            set(report["checks"]),
            {
                "docker_toolchain",
                "docker_state",
                "compose_processes",
                "direct_backend_host_artifacts",
                "bpf_pinned_map_artifacts",
                "runtime_lock_files",
                "disk_space",
                "python_runtime",
                "bridge_netfilter",
                "known_compose_outputs",
            },
        )
        self.assertTrue(report["assessment"]["ready"])

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
