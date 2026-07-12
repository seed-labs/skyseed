from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from skyseed_v1.observability.console_progress import (
    ConsoleProgressMonitor,
    progress_identity,
    render_launch_lines,
    render_phase_heartbeat_line,
    render_progress_line,
)


class ConsoleProgressTests(unittest.TestCase):
    def test_launch_acknowledgement_includes_identity_shape_and_phase(self) -> None:
        lines = render_launch_lines(
            profile_name="rich_s5_operational_clean_bpf",
            run_id="s5-test",
            run_dir=Path("/tmp/s5-test"),
            profile={
                "runner_args": {
                    "planned_shell_id": "S5",
                    "max_satellites": 172,
                    "loop_count": 4,
                    "horizon_seconds": 240,
                    "sample_interval": 10,
                },
                "validation_selector": {
                    "ut": {"ids": ["ut1", "ut2"]},
                    "gateway": {"ids": ["gw1"]},
                    "zone": {"ids": ["zone1"]},
                },
            },
        )

        rendered = "\n".join(lines)
        self.assertIn("launch=accepted", rendered)
        self.assertIn("run_id=s5-test", rendered)
        self.assertIn("shell:S5", rendered)
        self.assertIn("horizons:4x240s", rendered)
        self.assertIn("phase=initial_preview", rendered)

    def test_render_includes_phase_epoch_counts_and_current_epoch(self) -> None:
        line = render_progress_line(
            {
                "current_phase": "epoch:runtime_epoch:node_local_apply",
                "runtime_epochs_completed": 17,
                "parameter_epochs_completed": 2,
                "last_event_type": "runtime_epoch_node_local_apply_started",
                "current_epoch_context": {"segment_epoch_key": "H1:slice-0018"},
            }
        )

        self.assertIn("runtime:17,parameter:2", line)
        self.assertIn("current=H1:slice-0018", line)

    def test_compose_stage_identity_and_rendering_include_real_batch(self) -> None:
        first = {
            "current_phase": "compose_up_stage",
            "last_event_type": "compose_up_stage_started",
            "phase_detail": {
                "stage_id": "ground-uts-01",
                "stage_number": 7,
                "stage_total": 31,
                "stage_type": "endpoint",
                "service_count": 1,
                "container_names": ["ut2002"],
            },
        }
        second = {
            **first,
            "phase_detail": {**first["phase_detail"], "stage_id": "ground-uts-02"},
        }

        self.assertNotEqual(progress_identity(first), progress_identity(second))
        line = render_progress_line(first)
        self.assertIn("stage=ground-uts-01", line)
        self.assertIn("batch=7/31", line)
        self.assertIn("type=endpoint", line)
        self.assertIn("services=1", line)
        self.assertIn("targets=ut2002", line)

    def test_identity_coalesces_heartbeat_only_updates(self) -> None:
        first = {
            "current_phase": "compose:build",
            "runtime_epochs_completed": 0,
            "last_event_type": "compose_build_started",
            "heartbeat_utc": "one",
        }
        second = {**first, "heartbeat_utc": "two"}

        self.assertEqual(progress_identity(first), progress_identity(second))

    def test_phase_heartbeat_includes_elapsed_child_and_container_state(self) -> None:
        line = render_phase_heartbeat_line(
            {
                "current_phase": "compose_build",
                "seconds_in_phase": 42.8,
                "child_processes": [
                    {"cmd": "docker-compose build", "elapsed_seconds": 42},
                ],
                "docker_runtime": {
                    "ok": True,
                    "compose_project_container_count": 0,
                    "compose_project_running_count": 0,
                    "sample": [],
                },
            }
        )

        self.assertIn("phase=compose_build", line)
        self.assertIn("status=working", line)
        self.assertIn("elapsed=42s", line)
        self.assertIn("activity=compose_image_build", line)
        self.assertIn('command="docker-compose build"', line)
        self.assertIn("containers=running:0,created:0", line)
        self.assertIn("building_images_containers_start_at_compose_up", line)

        preview_line = render_phase_heartbeat_line(
            {
                "current_phase": "prepare_frozen_horizon_segments",
                "seconds_in_phase": 157,
                "child_processes": [
                    {
                        "cmd": (
                            "/python -m skyseed_v1.operations.preview "
                            "--planned-shell-id S5 --horizon-seconds 240"
                        )
                    }
                ],
                "docker_runtime": {
                    "ok": True,
                    "compose_project_container_count": 0,
                    "compose_project_running_count": 0,
                    "sample": [],
                },
            }
        )
        self.assertIn("activity=horizon_preview", preview_line)
        self.assertIn(
            'command="python -m skyseed_v1.operations.preview"', preview_line
        )
        self.assertNotIn("--planned-shell-id", preview_line)
        self.assertNotIn("containers=", preview_line)

    def test_host_command_summary_is_bounded_and_keeps_network_target(self) -> None:
        line = render_phase_heartbeat_line(
            {
                "current_phase": "current_horizon_host_install",
                "seconds_in_phase": 23,
                "child_processes": [
                    {
                        "cmd": (
                            "sudo -n nsenter -t 558305 -n sh -lc "
                            "'ip addr flush dev fs0086 || true; "
                            "ip addr add 10.243.86.1/30 dev fs0086 && "
                            "ip link set dev fs0086 up'"
                        )
                    }
                ],
                "docker_runtime": {
                    "ok": True,
                    "compose_project_container_count": 195,
                    "compose_project_running_count": 195,
                    "sample": [],
                },
            }
        )

        self.assertIn("activity=namespace_address_update", line)
        self.assertIn("command=", line)
        self.assertIn("nsenter -t 558305", line)
        self.assertIn("ip addr add 10.243.86.1/30 dev fs0086", line)
        self.assertLess(len(line), 400)

    def test_activity_recognizes_absolute_nsenter_and_direct_sudo_ip(self) -> None:
        namespace_line = render_phase_heartbeat_line(
            {
                "current_phase": "host_install",
                "seconds_in_phase": 8,
                "child_processes": [
                    {
                        "cmd": (
                            "sudo -n /usr/bin/nsenter -t 42 -n sh -lc "
                            "'set -e; ip link set dev fs0031 up'"
                        )
                    }
                ],
            }
        )
        host_line = render_phase_heartbeat_line(
            {
                "current_phase": "artifact_cleanup",
                "seconds_in_phase": 8,
                "child_processes": [{"cmd": "sudo -n ip link del vhs123"}],
            }
        )

        self.assertIn("activity=namespace_link_update", namespace_line)
        self.assertIn("activity=host_link_update", host_line)

    def test_heartbeat_filters_monitor_probe_and_defunct_child(self) -> None:
        line = render_phase_heartbeat_line(
            {
                "current_phase": "scheduler_wait",
                "seconds_in_phase": 12,
                "child_processes": [
                    {"cmd": "ps --ppid 42 -o pid,args"},
                    {"cmd": "[python] <defunct>"},
                    {
                        "cmd": (
                            "docker ps --filter "
                            "label=com.docker.compose.project=skyseed"
                        )
                    },
                ],
            }
        )

        self.assertNotIn("activity=", line)
        self.assertNotIn("command=", line)

    def test_monitor_emits_once_for_unchanged_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime_progress.json"
            path.write_text(
                json.dumps(
                    {
                        "current_phase": "runtime",
                        "runtime_epochs_completed": 1,
                        "last_event_type": "epoch_completed",
                    }
                ),
                encoding="utf-8",
            )
            lines: list[str] = []
            monitor = ConsoleProgressMonitor(
                path,
                interval_seconds=0.1,
                emit=lines.append,
            )
            monitor.start()
            time.sleep(0.35)
            monitor.stop()

        self.assertEqual(len(lines), 1)

    def test_monitor_emits_exactly_one_line_per_completed_epoch_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            progress_path = root / "runtime_progress.json"
            event_path = root / "events.raw.jsonl"
            lines: list[str] = []
            monitor = ConsoleProgressMonitor(
                progress_path,
                event_path=event_path,
                interval_seconds=0.1,
                emit=lines.append,
            )
            monitor.start()
            events = [
                {
                    "event_type": "runtime_epoch_result",
                    "segment_epoch_key": f"H0:slice-{index}",
                    "connectivity_gate_passed": True,
                    "transition_wall_seconds": 0.25,
                }
                for index in range(3)
            ]
            event_path.write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
            time.sleep(0.35)
            monitor.stop()

        epoch_lines = [line for line in lines if "epoch_completed=" in line]
        self.assertEqual(len(epoch_lines), 3)
        self.assertIn("epoch_completed=3", epoch_lines[-1])

    def test_monitor_emits_bounded_heartbeat_before_snapshot_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime_progress.json"
            lines: list[str] = []
            monitor = ConsoleProgressMonitor(
                path,
                interval_seconds=0.05,
                waiting_phase="initial_preview",
                waiting_heartbeat_seconds=0.1,
                emit=lines.append,
            )
            monitor.start()
            time.sleep(0.28)
            monitor.stop()

        heartbeat_lines = [line for line in lines if "progress_snapshot=pending" in line]
        self.assertGreaterEqual(len(heartbeat_lines), 2)
        self.assertTrue(all("status=working" in line for line in heartbeat_lines))

    def test_monitor_emits_launch_lines_from_observer_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lines: list[str] = []
            monitor = ConsoleProgressMonitor(
                Path(temporary) / "runtime_progress.json",
                interval_seconds=0.1,
                initial_lines=("launch-one", "launch-two"),
                emit=lines.append,
            )
            monitor.start()
            time.sleep(0.05)
            monitor.stop()

        self.assertEqual(lines[:2], ["launch-one", "launch-two"])

    def test_monitor_emits_bounded_heartbeat_for_unchanged_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime_progress.json"
            path.write_text(
                json.dumps(
                    {
                        "current_phase": "prepare_horizons",
                        "seconds_in_phase": 25,
                        "last_event_type": "prepare_horizons_started",
                    }
                ),
                encoding="utf-8",
            )
            lines: list[str] = []
            monitor = ConsoleProgressMonitor(
                path,
                interval_seconds=0.05,
                waiting_heartbeat_seconds=0.1,
                emit=lines.append,
            )
            monitor.start()
            time.sleep(0.38)
            monitor.stop()

        phase_lines = [line for line in lines if "status=working" in line]
        self.assertEqual(len(phase_lines), 1)
        self.assertTrue(all("phase=prepare_horizons" in line for line in phase_lines))

    def test_monitor_suppresses_identical_heartbeat_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime_progress.json"
            path.write_text(
                json.dumps(
                    {
                        "current_phase": "prepare_horizons",
                        "seconds_in_phase": 25,
                        "last_event_type": "prepare_horizons_started",
                    }
                ),
                encoding="utf-8",
            )
            lines: list[str] = []
            monitor = ConsoleProgressMonitor(
                path,
                interval_seconds=0.05,
                waiting_heartbeat_seconds=0.1,
                emit=lines.append,
            )
            monitor.start()
            time.sleep(0.38)
            monitor.stop()

        phase_lines = [line for line in lines if "status=working" in line]
        self.assertEqual(len(phase_lines), 1)


if __name__ == "__main__":
    unittest.main()
