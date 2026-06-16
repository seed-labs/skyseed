import json
import tempfile
import unittest
from pathlib import Path

from apps.run_starlink_shell_experiment import (
    _append_resource_usage_sample,
    _host_resource_snapshot,
)


class ResourceUsageSamplingTest(unittest.TestCase):
    def test_host_resource_snapshot_includes_memory_disk_and_run_dir_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            (run_dir / "artifact.txt").write_text("abcd", encoding="utf-8")

            snapshot = _host_resource_snapshot(run_dir=run_dir)

        self.assertIn("memory", snapshot)
        self.assertIn("workspace_disk", snapshot)
        self.assertIn("run_dir", snapshot)
        self.assertGreaterEqual(snapshot["run_dir"]["size_bytes"], 4)

    def test_append_resource_usage_sample_writes_jsonl_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            sample = {
                "heartbeat_utc": "2026-06-11T00:00:00Z",
                "run_id": "run-a",
                "run_mode": "real-runtime",
                "current_phase": "epoch:runtime_epoch:node_local_apply",
                "phase_started_utc": "2026-06-11T00:00:00Z",
                "seconds_in_phase": 1.25,
                "seconds_since_progress": 0.5,
                "runtime_epochs_completed": 2,
                "parameter_epochs_completed": 3,
                "current_epoch_context": {"slice_id": "slice-1"},
                "self_process": {"pid": 123, "vmrss_kib": 456},
                "host_resources": {
                    "memory": {"memavailable_kib": 789, "swapfree_kib": 10},
                    "run_dir": {"size_bytes": 42},
                },
                "docker_runtime": {"compose_project_container_count": 230},
                "direct_backend_host_interface_count": 7,
            }

            _append_resource_usage_sample(
                run_dir=run_dir,
                snapshot=sample,
                sample_reason="heartbeat",
            )

            rows = (run_dir / "resource_usage.jsonl").read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(rows), 1)
        row = json.loads(rows[0])
        self.assertEqual(row["sample_reason"], "heartbeat")
        self.assertEqual(row["current_phase"], "epoch:runtime_epoch:node_local_apply")
        self.assertEqual(row["self_process"]["vmrss_kib"], 456)
        self.assertEqual(row["host_resources"]["memory"]["swapfree_kib"], 10)


if __name__ == "__main__":
    unittest.main()
