#!/usr/bin/env python3
# encoding: utf-8

import json
import tempfile
import unittest
from pathlib import Path

from apps.pre_long_run_contract_check import (
    _frozen_live_state_alias_consumer_check,
    _read_compact_named_object,
    _read_compact_jsonl_record_containing,
    _read_last_compact_named_object,
    _previous_failed_cleanup_evidence_check,
    _previous_failed_worker_input_evidence_check,
    _r136_useful_compose_build_timeout_check,
    _r134_shared_gz_replay_report_check,
    _r138_runtime_consumer_contract_replay_check,
)


class PreLongRunContractCheckTests(unittest.TestCase):
    def test_frozen_live_state_alias_consumer_check(self) -> None:
        check = _frozen_live_state_alias_consumer_check()

        self.assertTrue(check["ok"])
        self.assertTrue(check["evidence"]["equivalent_alias_selected"])
        self.assertTrue(check["evidence"]["stale_alias_rejected"])

    def test_r136_timeout_check_returns_structured_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            r136 = root / "r136"
            r136.mkdir()
            (r136 / "first_failure_summary.json").write_text(
                json.dumps(
                    {
                        "first_failure_label": "docker_compose_build_timeout",
                        "runtime_epochs_completed": 0,
                        "runtime_blocker": {
                            "timeout_seconds": 900.0,
                            "wall_seconds": 900.1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            timeout_check = _r136_useful_compose_build_timeout_check(r136)

        self.assertTrue(timeout_check["ok"])
        self.assertEqual(timeout_check["name"], "r136_useful_compose_build_timeout_removed")

    def test_r138_consumer_replay_requires_all_four_horizons_and_noop_successors(self) -> None:
        records = [
            {
                "segment_epoch_key": f"H{index // 25}:slice-{index % 25 + 1}",
                "active_flow_count": 56,
                "changed_flow_count": 0,
                "reapplied_return_intent_count": 0,
                "ping_fallback_reason": "no_reapplied_flows",
                "ping_selected_flow_count": 12,
                "ok": True,
            }
            for index in range(100)
        ]
        report = {
            "schema_version": (
                "skyseed-prepared-runtime-consumer-contract-replay-v1"
            ),
            "ok": True,
            "blockers": [],
            "gate": {
                "ok": True,
                "mini_internet_explicitly_disabled": True,
                "consumer_binding_ref_count": 1005,
                "retired_ix_consumer_binding_ref_count": 0,
                "record_count": 100,
                "horizon_counts": {f"H{index}": 25 for index in range(4)},
                "transition_counts": {"shared_gz_noop_hold": 100},
                "blockers": [],
                "records": records,
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            failed_run = root / "r138"
            failed_run.mkdir()
            (failed_run / "first_failure_summary.json").write_text(
                json.dumps(
                    {
                        "failure_reason": (
                            "active return-state accounting requires "
                            "shared_gz_fec_with_zone_edge_delivery"
                        ),
                        "runtime_epochs_completed": 0,
                        "startup_materialization": {"progress_ratio": 1.0},
                    }
                ),
                encoding="utf-8",
            )
            replay_path = root / "replay.json"
            replay_path.write_text(json.dumps(report), encoding="utf-8")
            check = _r138_runtime_consumer_contract_replay_check(
                failed_run,
                replay_path,
            )

        self.assertTrue(check["ok"])
        self.assertEqual(check["evidence"]["replayed_record_count"], 100)
        self.assertEqual(check["evidence"]["invalid_record_count"], 0)
        self.assertEqual(
            check["evidence"]["retired_ix_consumer_binding_ref_count"],
            0,
        )
        self.assertTrue(check["evidence"]["manual_mpls_pop_contract_present"])

    def test_r134_shared_gz_replay_requires_complete_four_horizon_evidence(self) -> None:
        records = []
        for index in range(100):
            context_count = 56
            records.append(
                {
                    "ok": True,
                    "blockers": [],
                    "horizon_id": f"H{index // 25}",
                    "physical_generation": index % 32,
                    "context_count": context_count,
                    "observer_request_count": context_count,
                    "unique_selector_flow_count": context_count,
                    "unique_delivery_flow_count": context_count,
                    "map_conflict_count": 0,
                    "compiled_map_counts": {
                        "ut_gateway_policy_map": context_count,
                        "gateway_selector_ingress_map": context_count,
                        "zone_edge_delivery_map": context_count,
                    },
                }
            )
        report = {
            "schema_version": "skyseed-r134-shared-gz-offline-replay-v1",
            "current_compiled_schema": (
                "skyseed-bpf-owned-compiled-runtime-inputs-v2"
            ),
            "replayed_generation_count": 100,
            "horizon_counts": {f"H{index}": 25 for index in range(4)},
            "records": records,
            "blockers": [],
            "ok": True,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "replay.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            check = _r134_shared_gz_replay_report_check(path)

        self.assertTrue(check["ok"])
        self.assertTrue(check["evidence"]["generation_zero_present"])

    def test_last_compact_named_object_reads_target_from_large_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            path.write_text(
                json.dumps({"target": {"old": True}, "padding": "x" * 10000})
                + "\n"
                + json.dumps({"target": {"new": True, "nested": {"brace": "}"}}})
                + "\n",
                encoding="utf-8",
            )

            loaded = _read_last_compact_named_object(path, object_name="target")

        self.assertEqual(loaded, {"new": True, "nested": {"brace": "}"}})

    def test_compact_jsonl_record_containing_reads_only_matching_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            path.write_text(
                json.dumps({"event_type": "large", "padding": "x" * 10000})
                + "\n"
                + json.dumps(
                    {
                        "event_type": "runtime_cleanup_completed",
                        "phase_detail": {"remaining_host_interface_count": 91},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            loaded = _read_compact_jsonl_record_containing(
                path,
                marker='"event_type": "runtime_cleanup_completed"',
            )

        self.assertEqual(
            loaded["phase_detail"]["remaining_host_interface_count"],
            91,
        )

    def test_compact_named_object_reads_only_requested_section_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "compact.json"
            path.write_text(
                json.dumps(
                    {
                        "unrelated": {"target": {"wrong": True}},
                        "plans": {
                            "target": {
                                "nested": {"text": 'brace } and escaped " quote'},
                                "operations": [1, 2],
                            }
                        },
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )

            loaded = _read_compact_named_object(
                path,
                section_name="plans",
                object_name="target",
            )

        self.assertEqual(loaded["operations"], [1, 2])
        self.assertEqual(
            loaded["nested"]["text"],
            'brace } and escaped " quote',
        )

    def test_previous_worker_cache_failure_requires_boundary_and_missing_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            segment_dir = run_dir / "replenished_segments" / "segment_001"
            segment_dir.mkdir(parents=True)
            (segment_dir / "horizon_maintenance_process_work.json").write_text(
                json.dumps({"schema_version": "old-process-work"}) + "\n",
                encoding="utf-8",
            )
            (run_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "event_type": "runtime_exception",
                        "failure_reason": (
                            "horizon_maintenance_ready_missing_before_boundary:H1"
                        ),
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            check = _previous_failed_worker_input_evidence_check(run_dir)

        self.assertTrue(check["ok"])
        self.assertTrue(check["evidence"]["previous_failed_artifact_rejected"])

    def test_previous_failed_cleanup_evidence_requires_active_h_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            events = [
                {
                    "event_type": "runtime_epoch_result",
                    "segment_epoch_key": "H0:epoch-1",
                },
                {
                    "event_type": "bpf_retired_generation_cleanup_queued",
                    "horizon_id": "H0",
                    "generation_id": 1,
                },
            ]
            (run_dir / "events.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )

            check = _previous_failed_cleanup_evidence_check(run_dir)

        self.assertTrue(check["ok"])
        self.assertTrue(check["evidence"]["previous_failed_artifact_rejected"])
        self.assertEqual(
            check["evidence"]["active_horizon_cleanup_violation_count"],
            1,
        )

    def test_previous_failed_cleanup_evidence_fails_without_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            (run_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "event_type": "runtime_epoch_result",
                        "segment_epoch_key": "H0:epoch-1",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            check = _previous_failed_cleanup_evidence_check(run_dir)

        self.assertFalse(check["ok"])
        self.assertEqual(
            check["blockers"],
            [{"reason": "previous_cleanup_failure_not_reproduced"}],
        )


if __name__ == "__main__":
    unittest.main()
