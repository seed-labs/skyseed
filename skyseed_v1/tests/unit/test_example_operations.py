from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from skyseed_v1.operations.example import execute


def _preflight_report(*, ready: bool) -> dict:
    return {
        "profile": "operational-clean-bpf-owned",
        "assessment": {
            "profile": "operational-clean-bpf-owned",
            "ready": ready,
            "clean_environment_required": True,
            "environment_clean": ready,
            "blocking_reasons": [] if ready else ["environment_not_clean"],
            "warning_reasons": [],
        },
        "checks": {
            "compose_processes": {"processes": []},
            "docker_state": {
                "containers": {
                    "compose_project_containers": [],
                    "named_skyseed_containers": [],
                    "compose_project_state_counts": {},
                },
                "networks": {
                    "compose_project_networks": [],
                    "named_skyseed_networks": [],
                },
            },
            "disk_space": {"free_bytes": 50 * 1024**3},
            "python_runtime": {
                "current_executable": "/python",
                "current_version": "3.10",
                "preferred_seed310_selected": True,
                "legacy_seed_emulator_pythonpath_override_present": False,
            },
            "direct_backend_host_artifacts": {"interfaces": []},
            "runtime_lock_files": {"files": []},
            "bridge_netfilter": {"passthrough_ready": True},
        },
        "cleanup_suggestions": [],
    }


class ExampleOperationsTests(unittest.TestCase):
    def test_preflight_defaults_to_short_human_summary(self) -> None:
        output = io.StringIO()
        with patch(
            "skyseed_v1.operations.preflight.run_skyseed_preflight",
            return_value=_preflight_report(ready=True),
        ), contextlib.redirect_stdout(output):
            result = execute(Mock(), "preflight")

        self.assertEqual(result, 0)
        rendered = output.getvalue()
        self.assertIn("Skyseed preflight", rendered)
        self.assertIn("  ready=True", rendered)
        self.assertIn("  blocking_reasons=none", rendered)
        self.assertNotIn('"checks"', rendered)

    def test_preflight_json_remains_available_for_audit(self) -> None:
        output = io.StringIO()
        with patch(
            "skyseed_v1.operations.preflight.run_skyseed_preflight",
            return_value=_preflight_report(ready=False),
        ), contextlib.redirect_stdout(output):
            result = execute(Mock(), "preflight", output_format="json")

        self.assertEqual(result, 1)
        self.assertIn('"ready": false', output.getvalue())
        self.assertIn('"checks"', output.getvalue())

    def test_clean_applies_scoped_cleanup_then_requires_ready_postflight(self) -> None:
        before = _preflight_report(ready=False)
        after = _preflight_report(ready=True)
        cleanup_result = {
            "scope": "skyseed_only",
            "dry_run": False,
            "applied": True,
            "success": True,
            "resource_counts": {
                "containers": 3,
                "networks": 1,
                "direct_backend_host_interfaces": 0,
                "bpf_pinned_maps": 0,
                "runtime_lock_files": 1,
            },
            "command_results": [],
        }
        output = io.StringIO()
        with patch(
            "skyseed_v1.operations.preflight.run_skyseed_cleanup_preflight",
            side_effect=(before, after),
        ), patch(
            "skyseed_v1.operations.preflight.cleanup_skyseed_environment_residue",
            return_value=cleanup_result,
        ) as cleanup, contextlib.redirect_stdout(output):
            result = execute(Mock(), "clean")

        self.assertEqual(result, 0)
        cleanup.assert_called_once_with(preflight_report=before, apply=True)
        self.assertIn("  ready=True", output.getvalue())
        self.assertIn("    success=True", output.getvalue())

    def test_clean_refuses_to_remove_an_active_skyseed_run(self) -> None:
        before = _preflight_report(ready=False)
        before["checks"]["compose_processes"] = {
            "processes": [{"pid": 123, "args": "docker-compose up"}],
        }
        output = io.StringIO()
        with patch(
            "skyseed_v1.operations.preflight.run_skyseed_cleanup_preflight",
            return_value=before,
        ), patch(
            "skyseed_v1.operations.preflight.cleanup_skyseed_environment_residue",
        ) as cleanup, contextlib.redirect_stdout(output):
            result = execute(Mock(), "clean")

        self.assertEqual(result, 2)
        cleanup.assert_not_called()
        self.assertIn("blocked=active_skyseed_processes_present", output.getvalue())


if __name__ == "__main__":
    unittest.main()
