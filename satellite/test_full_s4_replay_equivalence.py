#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

import json
from pathlib import Path
import argparse
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "apps"))

from full_s4_replay_equivalence import write_replay_equivalence_report
from run_starlink_shell_experiment import (
    _apply_replay_pinned_source_time,
    _pin_replay_epoch_selection_from_source,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _build_run(
    run_dir: Path,
    *,
    source_timestamp_utc: str = "2026-06-23T00:00:00Z",
) -> None:
    _write_json(
        run_dir / "config" / "effective_workload_catalog.json",
        {
            "coordinate_policy": "shell_aware_consistent",
            "coordinate_semantics": {
                "mode": "shell_aware_consistent",
                "source_slice_id": "slice-1",
                "source_timestamp_utc": source_timestamp_utc,
            },
            "effective_catalogs": {
                "uts": [{"ut_id": "ut2001"}, {"ut_id": "ut2002"}],
                "gateways": [{"gateway_id": "gw201"}],
                "zones": [{"zone_id": "zone-a", "required_for_runtime": True}],
            },
        },
    )
    _write_json(
        run_dir / "shell_manifest.json",
        {
            "orbit_input_mode": "planned-shell",
            "orbit_input_kind": "planned_shell_circular_orbit",
            "orbit_source": "starlink_phase1_S4",
            "selection_input_mode": "planned_shell_preset",
            "shell_selection": {
                "selected_satellite_count": 2,
                "max_satellites_requested": 2,
                "included_satellite_ids": ["stl-940001", "stl-940002"],
                "plane_manifest": [{"plane": 1}],
            },
            "ground_model": {
                "coordinate_semantics": {
                    "mode": "shell_aware_consistent",
                    "source_slice_id": "slice-1",
                    "source_timestamp_utc": source_timestamp_utc,
                }
            },
            "layout": {"satellites": [{"satellite_id": "stl-940001"}]},
        },
    )
    _write_json(
        run_dir / "temporal_plan.json",
        {
            "policy": {"horizon_seconds": 120, "sample_interval_seconds": 10},
            "orbit_samples": [
                {
                    "sample_id": "sample-1",
                    "sample_index": 0,
                    "slice_id": "slice-1",
                    "relative_time_seconds": 0,
                    "absolute_time_utc": source_timestamp_utc,
                    "satellite_count": 2,
                }
            ],
            "observation_frames": [{"frame_id": "frame-1", "slice_id": "slice-1"}],
            "event_queue": [
                {
                    "event_id": "event-runtime-1",
                    "event_type": "runtime_epoch",
                    "activation_group_id": "activation-0",
                    "activation_relative_time_seconds": 0,
                    "activation_time_utc": "2026-06-23T00:00:00Z",
                    "source_slice_id": "slice-1",
                },
                {
                    "event_id": "event-parameter-1",
                    "event_type": "parameter_epoch",
                    "activation_group_id": "activation-10",
                    "activation_relative_time_seconds": 10,
                    "activation_time_utc": "2026-06-23T00:00:10Z",
                    "source_slice_id": "slice-1",
                },
            ],
            "runtime_epochs": [{"slice_id": "slice-1", "runtime_plan_id": "rt-1"}],
            "parameter_epochs": [
                {"slice_id": "slice-1", "parameter_batch_id": "param-1"}
            ],
        },
    )
    _write_json(
        run_dir / "prepared_update_bundle.json",
        {
            "strict_mode": True,
            "source": "event_timeline",
            "selected_runtime_slice_ids": ["slice-1"],
            "selected_parameter_slice_ids": ["slice-1"],
            "runtime_transaction_count": 1,
            "parameter_update_batch_count": 1,
            "physical_sample_count": 1,
            "refined_event_count": 2,
            "sample_aligned_event_count": 1,
            "skipped_noop_sample_count": 0,
            "all_sample_frames": [{"slice_id": "slice-1", "active_link_count": 3}],
            "selected_epoch_queue": [
                {
                    "sequence_index": 1,
                    "segment_epoch_key": "initial:slice-1",
                    "epoch_type": "runtime_epoch",
                    "event_timeline_type": "runtime_transaction",
                    "source_event_type": "runtime_epoch",
                    "slice_id": "slice-1",
                    "epoch_id": "runtimeepoch-1",
                    "activation_group_id": "activation-0",
                    "activation_relative_time_seconds": 0,
                    "source_event_id": "event-runtime-1",
                    "trigger_reasons": ["initial_runtime_epoch"],
                    "runtime_plan_id": "rt-1",
                },
                {
                    "sequence_index": 2,
                    "segment_epoch_key": "initial:slice-1:param",
                    "epoch_type": "parameter_epoch",
                    "event_timeline_type": "parameter_update_batch",
                    "source_event_type": "parameter_epoch",
                    "slice_id": "slice-1",
                    "epoch_id": "parameterepoch-1",
                    "activation_group_id": "activation-10",
                    "activation_relative_time_seconds": 10,
                    "source_event_id": "event-parameter-1",
                    "parameter_batch_id": "param-1",
                },
            ],
            "resource_budget": {"ut_access": 2},
            "continuous_operation_prepared": {"epoch_queue_prepared": True},
            "runtime_epoch_summaries": [{"slice_id": "slice-1", "runtime_plan_id": "rt-1"}],
            "parameter_epoch_summaries": [
                {"slice_id": "slice-1", "parameter_batch_id": "param-1"}
            ],
        },
    )
    _write_json(
        run_dir / "zone_anchor_semantic_validation.json",
        {
            "passed": True,
            "summary": {"zero_active_required_zone_count": 0},
            "violation_table": [],
            "zone_anchor_ledger": [
                {
                    "generation": 0,
                    "slice_id": "slice-1",
                    "zone_id": "zone-a",
                    "active_anchor_count": 1,
                    "active_anchor_candidates": ["stl-940001"],
                }
            ],
        },
    )
    _write_json(
        run_dir / "gz_fec_ledger.json",
        {
            "record_count": 1,
            "records": [
                {
                    "fec_id": "fec-zone-a",
                    "fec_kind": "default_z_fec",
                    "generation": 0,
                }
            ],
        },
    )
    _write_json(
        run_dir / "bpf_mpls_offline_validation.json",
        {
            "selected_runtime_slice_ids": ["slice-1"],
            "compiled_slice_count": 1,
            "aggregate": {
                "classifier_entry_count": 2,
                "core_lfib_entry_count": 1,
                "egress_handoff_entry_count": 1,
                "generation_count": 1,
                "unsafe_conflict_count": 0,
                "safe_duplicate_count": 0,
            },
            "compiled_slices": [
                {
                    "generation": 0,
                    "generation_source": "slice",
                    "runtime_plan_id": "rt-1",
                    "slice_id": "slice-1",
                    "context_count": 1,
                    "compiled_mpls_map_counts": {
                        "ingress_classifier_map": 2,
                        "core_lfib_map": 1,
                        "egress_handoff_map": 1,
                    },
                }
            ],
            "label_allocation_ledger_count": 1,
            "label_allocation_ledger": [
                {"generation": 0, "fec_id": "fec-zone-a", "incoming_label": 100}
            ],
        },
    )
    _write_json(
        run_dir / "path_cardinality_audit.json",
        {
            "semantic_validation_passed": True,
            "bpf_offline_validation_passed": True,
            "selected_runtime_slice_ids": ["slice-1"],
            "aggregate": {
                "eligible_core_forwarding_scope_count": 1,
                "compiled_bpf_owned_core_forwarding_count": 1,
                "linux_gz_mpls_lfib_fallback_count": 0,
                "skipped_eligible_core_scope_count": 0,
                "direct_handoff_count": 1,
                "gateway_class_selected_gateway_mismatch_count": 0,
                "blocker_count": 0,
                "meaningfulness_gate_passed": True,
            },
            "per_slice": [{"slice_id": "slice-1", "generation": 0}],
            "risk_summary": {"blockers": [], "warnings": []},
        },
    )


class FullS4ReplayEquivalenceTests(unittest.TestCase):
    def test_identical_prepared_artifacts_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            candidate = Path(temp_dir) / "candidate"
            _build_run(source)
            shutil.copytree(source, candidate)

            report = write_replay_equivalence_report(
                source_run_dir=source,
                candidate_run_dir=candidate,
            )

        self.assertTrue(report["equivalent"])
        self.assertEqual(report["mismatch_count"], 0)
        self.assertTrue(report["runtime_conclusion_allowed"])

    def test_parameter_update_batch_shape_change_blocks_runtime_conclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            candidate = Path(temp_dir) / "candidate"
            _build_run(source)
            shutil.copytree(source, candidate)
            bundle = json.loads((candidate / "prepared_update_bundle.json").read_text())
            bundle["selected_parameter_slice_ids"] = []
            bundle["parameter_update_batch_count"] = 0
            bundle["selected_epoch_queue"] = [
                record
                for record in bundle["selected_epoch_queue"]
                if record["epoch_type"] != "parameter_epoch"
            ]
            _write_json(candidate / "prepared_update_bundle.json", bundle)

            report = write_replay_equivalence_report(
                source_run_dir=source,
                candidate_run_dir=candidate,
            )

        self.assertFalse(report["equivalent"])
        self.assertFalse(report["runtime_conclusion_allowed"])
        self.assertIn(
            "prepared_bundle",
            [mismatch["name"] for mismatch in report["mismatches"]],
        )

    def test_source_timestamp_drift_blocks_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            candidate = Path(temp_dir) / "candidate"
            _build_run(source, source_timestamp_utc="2026-06-23T00:48:17Z")
            _build_run(candidate, source_timestamp_utc="2026-06-23T14:08:34Z")

            report = write_replay_equivalence_report(
                source_run_dir=source,
                candidate_run_dir=candidate,
            )

        self.assertFalse(report["equivalent"])
        self.assertFalse(report["runtime_conclusion_allowed"])
        self.assertIn("catalog", [mismatch["name"] for mismatch in report["mismatches"]])
        source_catalog = report["source_fingerprint"]["catalog"]
        candidate_catalog = report["candidate_fingerprint"]["catalog"]
        self.assertNotEqual(
            source_catalog["coordinate_semantics_sha256"],
            candidate_catalog["coordinate_semantics_sha256"],
        )

    def test_replay_invocation_pins_source_timestamp_from_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            _build_run(source, source_timestamp_utc="2026-06-23T00:48:17Z")
            args = argparse.Namespace(
                replay_prepared_runtime=source,
                start_time=None,
            )

            pinned_args, pin = _apply_replay_pinned_source_time(args)

        self.assertEqual(pinned_args.start_time, "2026-06-23T00:48:17Z")
        self.assertEqual(pin["source_timestamp_utc"], "2026-06-23T00:48:17Z")
        self.assertTrue(pin["pinned"])

    def test_replay_invocation_rejects_mismatched_explicit_start_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            _build_run(source, source_timestamp_utc="2026-06-23T00:48:17Z")
            args = argparse.Namespace(
                replay_prepared_runtime=source,
                start_time="2026-06-23T14:08:34Z",
            )

            with self.assertRaisesRegex(RuntimeError, "does not match baseline"):
                _apply_replay_pinned_source_time(args)

    def test_replay_epoch_selection_uses_baseline_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            _build_run(source)
            source_bundle = json.loads((source / "prepared_update_bundle.json").read_text())
            candidate_queue = [
                *source_bundle["selected_epoch_queue"],
                {
                    **source_bundle["selected_epoch_queue"][0],
                    "source_event_id": "event-runtime-extra",
                    "slice_id": "slice-extra",
                },
            ]

            selected_queue, runtime_slices, parameter_slices, pin = (
                _pin_replay_epoch_selection_from_source(
                    source_run_dir=source,
                    candidate_epoch_queue=candidate_queue,
                )
            )

        self.assertEqual(len(selected_queue), 2)
        self.assertEqual(runtime_slices, ["slice-1"])
        self.assertEqual(parameter_slices, ["slice-1"])
        self.assertEqual(pin["selected_epoch_queue_count"], 2)


if __name__ == "__main__":
    unittest.main()
