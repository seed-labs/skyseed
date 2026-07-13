from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from skyseed_v1.runtime.experiment import (
    _frozen_horizon_topology_identity_gate,
    _frozen_horizon_time_alignment_gate,
    _prepared_attach_capability_readiness_for_preload,
    _replenishment_preview_args,
    _run_replenishment_preview,
)
from skyseed_v1.runtime.maintenance_worker import (
    _prepared_attach_capability_readiness,
)
from skyseed_v1.runtime.profile import load_profile


class ReplenishmentTimeBindingTests(unittest.TestCase):
    def test_canonical_s4_and_s5_profiles_share_one_continuous_physical_timeline(self) -> None:
        for profile_name in (
            "rich_s4_operational_clean_bpf",
            "rich_s5_operational_clean_bpf",
        ):
            profile = load_profile(profile_name)
            runner_args = argparse.Namespace(**dict(profile["runner_args"]))
            horizon_seconds = float(runner_args.horizon_seconds)
            base_time = runner_args.start_time.replace("Z", "+00:00")
            base = datetime.fromisoformat(base_time)

            segments = [
                _replenishment_preview_args(
                    runner_args,
                    start_time_utc=(
                        base + timedelta(seconds=index * horizon_seconds)
                    ).isoformat().replace("+00:00", "Z"),
                    horizon_seconds=horizon_seconds,
                )
                for index in range(int(runner_args.loop_count))
            ]

            self.assertEqual(
                [segment.visualization_link_frame_offset for segment in segments],
                [0, 24, 48, 72],
                msg=profile_name,
            )
            self.assertEqual(
                {
                    segment.visualization_link_start_timestamp
                    for segment in segments
                },
                {runner_args.start_time},
                msg=profile_name,
            )

    def test_worker_attach_handoff_projection_drops_compiler_payload_duplicates(self) -> None:
        detail = {
            "interface_ref": "netns:stl-1:xp1a",
            "interface_name": "xp1a",
            "ifindex": 7,
            "mac_address": "02:00:00:00:00:07",
            "expected_roles": ["core_lfib"],
        }
        huge_duplicate = {"entries": ["x" * 1000 for _ in range(100)]}
        report = {
            "ok": True,
            "reason": "",
            "attach_plan": {"netns:stl-1:xp1a": ["ingress"]},
            "attached_interfaces": ["netns:stl-1:xp1a"],
            "attached_interface_details": [detail],
            "compiled_runtime_inputs": huge_duplicate,
            "prepare_steps": {
                "preactivation_attach_prepare": {
                    "ok": True,
                    "compiled_runtime_inputs": huge_duplicate,
                    "attach_plan": {"netns:stl-1:xp1a": ["ingress"]},
                    "attached_interfaces": ["netns:stl-1:xp1a"],
                    "attached_interface_details": [detail],
                }
            },
        }
        compact = _prepared_attach_capability_readiness(report)
        rendered = json.dumps(compact, separators=(",", ":"))
        self.assertNotIn("compiled_runtime_inputs", rendered)
        self.assertLess(len(rendered), 10_000)
        self.assertEqual(compact["attached_interface_details"], [detail])
        consumed = _prepared_attach_capability_readiness_for_preload(compact)
        self.assertTrue(consumed["frozen_interface_binding_cache"]["complete"])
        self.assertEqual(
            consumed["frozen_interface_binding_cache"]["ifindex_by_interface_ref"],
            {"netns:stl-1:xp1a": 7},
        )

    def test_frozen_horizon_time_gate_accepts_contiguous_shared_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            segments = []
            for index in range(2):
                config = root / f"h{index}"
                config.mkdir()
                start_minute = index * 4
                end_minute = (index + 1) * 4
                start = f"2026-07-09T00:{start_minute:02d}:00Z"
                end = f"2026-07-09T00:{end_minute:02d}:00Z"
                snapshots = [
                    {"time_slice": {"absolute_time_utc": start}},
                    {"time_slice": {"absolute_time_utc": end}},
                ]
                snapshot_path = config / "snapshot_preview.json"
                snapshot_path.write_text(
                    json.dumps(
                        {
                            "pipeline": {
                                "visualization_link_frame_offset": index * 24
                            },
                            "snapshots": snapshots,
                        }
                    ),
                    encoding="utf-8",
                )
                (config / "visualization_physical_links.json").write_text(
                    json.dumps(
                        {
                            "timestamp": "1783555200000",
                            "interval": "10s",
                            "links": [{} for _ in range(50)],
                        }
                    ),
                    encoding="utf-8",
                )
                segments.append(
                    {
                        "horizon_id": f"H{index}",
                        "start_time_utc": start,
                        "end_time_utc": end,
                        "snapshot_preview_path": snapshot_path,
                        "temporal_plan": {
                            "orbit_samples": [
                                {"absolute_time_utc": start},
                                {"absolute_time_utc": end},
                            ]
                        },
                    }
                )
            gate = _frozen_horizon_time_alignment_gate(segments)
        self.assertTrue(gate["passed"])
        self.assertEqual([item["frame_offset"] for item in gate["horizons"]], [0, 24])

    def test_frozen_horizon_gate_rejects_a_different_compose_slot_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "h0.json"
            second = root / "h1.json"
            base = {
                "satellites": [{"satellite_id": "stl-1"}],
                "gateways": [],
                "user_terminals": [],
                "internet_exchanges": [],
                "slot_inventory": {"cross_plane_isl": [{"slot_name": "xp1"}]},
            }
            first.write_text(json.dumps(base), encoding="utf-8")
            second.write_text(
                json.dumps(
                    {
                        **base,
                        "slot_inventory": {
                            "cross_plane_isl": [{"slot_name": "xp2"}]
                        },
                    }
                ),
                encoding="utf-8",
            )
            gate = _frozen_horizon_topology_identity_gate(
                [
                    {"horizon_id": "H0", "topology_path": first},
                    {"horizon_id": "H1", "topology_path": second},
                ]
            )
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["mismatch_count"], 1)
        self.assertEqual(gate["mismatches"][0]["horizon_id"], "H1")

    def test_successor_horizon_uses_offset_into_shared_physical_timeline(self) -> None:
        source = argparse.Namespace(
            run_mode="real-runtime",
            start_time="2026-07-09T00:00:00Z",
            visualization_link_start_timestamp="2026-07-09T00:00:00Z",
            visualization_link_interval="10s",
            horizon_seconds=240.0,
            sample_interval=10.0,
            step_seconds=10.0,
            slice_count=25,
        )

        segment = _replenishment_preview_args(
            source,
            start_time_utc="2026-07-09T00:08:00Z",
            horizon_seconds=240.0,
        )

        self.assertEqual(segment.start_time, "2026-07-09T00:08:00Z")
        self.assertEqual(
            segment.visualization_link_start_timestamp,
            "2026-07-09T00:00:00Z",
        )
        self.assertEqual(segment.visualization_link_frame_offset, 48)
        self.assertEqual(segment.slice_count, 25)
        self.assertEqual(source.visualization_link_start_timestamp, "2026-07-09T00:00:00Z")

    def test_real_replenishment_caller_passes_the_bound_time_to_preview(self) -> None:
        source = argparse.Namespace(
            run_mode="real-runtime",
            start_time="2026-07-09T00:00:00Z",
            visualization_link_start_timestamp="2026-07-09T00:00:00Z",
            visualization_link_interval="10s",
            horizon_seconds=240.0,
            sample_interval=10.0,
            step_seconds=10.0,
            slice_count=25,
        )
        completed = Mock(returncode=0, stdout="{}", stderr="")
        segment = {"summary": {"ok": True}}
        with tempfile.TemporaryDirectory() as directory, patch(
            "skyseed_v1.runtime.experiment._preview_command",
            return_value=["preview"],
        ) as preview_command, patch(
            "skyseed_v1.runtime.experiment.subprocess.run",
            return_value=completed,
        ), patch(
            "skyseed_v1.runtime.experiment._load_temporal_artifacts",
            return_value={"artifacts": {}},
        ), patch(
            "skyseed_v1.runtime.experiment._preview_summary_to_segment",
            return_value=segment,
        ):
            result = _run_replenishment_preview(
                args=source,
                run_dir=Path(directory),
                segment_index=2,
                start_time_utc="2026-07-09T00:08:00Z",
                horizon_seconds=240.0,
                topology_path=Path(directory) / "topology.json",
                link_state_path=Path(directory) / "link_state.json",
                run_start_time_utc="2026-07-09T00:00:00Z",
            )

        self.assertEqual(result, segment)
        bound_args = preview_command.call_args.kwargs["args"]
        self.assertEqual(bound_args.start_time, "2026-07-09T00:08:00Z")
        self.assertEqual(
            bound_args.visualization_link_start_timestamp,
            "2026-07-09T00:00:00Z",
        )
        self.assertEqual(bound_args.visualization_link_frame_offset, 48)

    def test_successor_horizon_rejects_non_aligned_physical_frame_time(self) -> None:
        source = argparse.Namespace(
            start_time="2026-07-09T00:00:00Z",
            visualization_link_start_timestamp="2026-07-09T00:00:00Z",
            visualization_link_interval="10s",
            sample_interval=10.0,
            step_seconds=10.0,
        )
        with self.assertRaisesRegex(ValueError, "must align"):
            _replenishment_preview_args(
                source,
                start_time_utc="2026-07-09T00:08:01Z",
                horizon_seconds=240.0,
            )


if __name__ == "__main__":
    unittest.main()
