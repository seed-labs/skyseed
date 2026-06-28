#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runtime_execution_trace import (
    validate_trace_span,
    write_execution_trace_artifacts,
)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_spans(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "execution_trace.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _runtime_epoch(epoch_id: str = "runtimeepoch-1") -> dict:
    return {
        "epoch_id": epoch_id,
        "epoch_type": "runtime_epoch",
        "event_timeline_type": "runtime_transaction",
        "segment_epoch_key": "initial:runtime",
        "outcome": {"success": True, "failure_reason": None},
        "timing": {
            "event_wait_seconds": 5.0,
            "host_operation_apply_wall_seconds": 2.0,
            "node_local_apply_wall_seconds": 3.0,
            "network_update_seconds": 5.0,
            "post_apply_overhead_seconds": 7.0,
            "parameter_qdisc_required_update_count": 0,
            "parameter_qdisc_apply_seconds": 0.0,
        },
        "host_operation_timing_summary": {"operation_count": 4},
        "node_local_execution": {
            "backend": {
                "bpf_owned_readiness": {
                    "prepare_steps": {
                        "load": {
                            "active_generation": 2,
                            "active_generation_flip_attempted": True,
                            "active_generation_flip_wall_seconds": 0.2,
                            "active_generation_update_count": 1,
                            "map_write_wall_seconds": 0.9,
                            "ok": True,
                            "total_update_count": 8,
                            "generation_gc_cleanup": {
                                "maps": {
                                    "ingress_classifier_map": {
                                        "old_generation_delete_update_count": 3,
                                        "target_generation_delete_update_count": 1,
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    }


def _parameter_epoch() -> dict:
    return {
        "epoch_id": "parameterepoch-1",
        "epoch_type": "parameter_epoch",
        "event_timeline_type": "parameter_update_batch",
        "segment_epoch_key": "initial:parameter",
        "outcome": {"success": True, "failure_reason": None},
        "timing": {
            "event_wait_seconds": 0.0,
            "host_operation_apply_wall_seconds": 1.0,
            "node_local_apply_wall_seconds": 0.0,
            "network_update_seconds": 1.0,
            "parameter_qdisc_required_update_count": 5,
            "parameter_qdisc_apply_seconds": 12.0,
        },
    }


class RuntimeExecutionTraceTests(unittest.TestCase):
    def _run_dir(self, rows: list[dict]) -> tempfile.TemporaryDirectory:
        tmp = tempfile.TemporaryDirectory(dir="/home/seed/skyseed_runs")
        run_dir = Path(tmp.name)
        _write_json(
            run_dir / "profile_runner_stdout.json",
            {
                "run_id": "trace-test",
                "startup_strategy": {
                    "compose_stage_results": [
                        {
                            "stage_id": "helpers",
                            "stage_type": "helper",
                            "wall_seconds": 1.5,
                            "service_count": 2,
                            "timed_out": False,
                        }
                    ]
                },
            },
        )
        _write_jsonl(run_dir / "epoch_execution_details.jsonl", rows)
        _write_json(
            run_dir / "runtime_progress.json",
            {"phase_timing": {"compose_down_after_wall_seconds": 2.5}},
        )
        _write_json(
            run_dir / "real_runtime_report.json",
            {
                "final_cleanup": {
                    "host_artifact_cleanup": {
                        "cleanup_completed": True,
                        "cleanup_metrics": {"cleanup_resource_attempted_count": 3},
                        "timing": {"cleanup_wall_seconds": 4.5},
                    },
                    "bpf_backend_cleanup": {
                        "residue": {"clean": True},
                        "steps": [{"args": ["rm"]}],
                    },
                }
            },
        )
        _write_json(
            run_dir / "normal_failure_cleanup_report.json",
            {
                "external_cleanup_success": True,
                "after": {
                    "clean": True,
                    "counts": {
                        "direct_backend_host_interfaces": 0,
                        "bpf_pins": 0,
                        "compose_containers": 0,
                        "compose_networks": 0,
                        "runtime_locks": 0,
                        "docker_network_pool_overlaps": 0,
                    },
                },
            },
        )
        return tmp

    def test_trace_span_schema_validation(self) -> None:
        with self._run_dir([_runtime_epoch()]) as tmp:
            run_dir = Path(tmp)
            write_execution_trace_artifacts(run_dir)
            spans = _read_spans(run_dir)

        self.assertGreater(len(spans), 0)
        self.assertEqual(validate_trace_span(spans[0]), [])

    def test_non_additive_fields_are_not_summed_into_parent_wall_time(self) -> None:
        with self._run_dir([_runtime_epoch()]) as tmp:
            run_dir = Path(tmp)
            summary = write_execution_trace_artifacts(run_dir)

        self.assertGreater(
            summary["categories"]["dataplane-nonblocking prepare"],
            0.0,
        )
        self.assertEqual(summary["categories"]["runtime update work"], 5.0)
        self.assertGreater(summary["categories"]["post-apply/reporting overhead"], 5.0)

    def test_bpf_map_update_span_records_count_execution_mode_blocking_scope(self) -> None:
        with self._run_dir([_runtime_epoch()]) as tmp:
            run_dir = Path(tmp)
            write_execution_trace_artifacts(run_dir)
            spans = _read_spans(run_dir)
        span = next(item for item in spans if item["operation"] == "target_generation_map_update")

        self.assertEqual(span["count"], 8)
        self.assertEqual(span["execution_mode"], "bpftool")
        self.assertEqual(span["blocking_scope"], "dataplane_nonblocking_prepare")

    def test_active_generation_flip_occurs_after_map_update_span(self) -> None:
        with self._run_dir([_runtime_epoch()]) as tmp:
            run_dir = Path(tmp)
            summary = write_execution_trace_artifacts(run_dir)
            spans = _read_spans(run_dir)
        update_index = next(
            index for index, span in enumerate(spans)
            if span["operation"] == "target_generation_map_update"
        )
        flip_index = next(
            index for index, span in enumerate(spans)
            if span["operation"] == "active_generation_flip"
        )
        flip = spans[flip_index]

        self.assertLess(update_index, flip_index)
        self.assertEqual(flip["blocking_scope"], "current_dataplane_affecting")
        self.assertEqual(summary["validation_errors"], [])

    def test_qdisc_update_is_current_dataplane_affecting(self) -> None:
        with self._run_dir([_parameter_epoch()]) as tmp:
            run_dir = Path(tmp)
            write_execution_trace_artifacts(run_dir)
            spans = _read_spans(run_dir)
        span = next(item for item in spans if item["operation"] == "qdisc_update")

        self.assertEqual(span["execution_mode"], "tc")
        self.assertEqual(span["blocking_scope"], "current_dataplane_affecting")
        self.assertEqual(span["count"], 5)

    def test_cleanup_spans_require_external_residue_proof(self) -> None:
        with self._run_dir([_runtime_epoch()]) as tmp:
            run_dir = Path(tmp)
            summary = write_execution_trace_artifacts(run_dir)

        proof = summary["cleanup_external_residue_proof"]
        self.assertTrue(proof["available"])
        self.assertTrue(proof["external_cleanup_success"])
        self.assertEqual(proof["after_counts"]["compose_containers"], 0)

    def test_aggregation_does_not_require_s4_specific_counts(self) -> None:
        rows = [_runtime_epoch("runtimeepoch-small")]
        rows[0]["host_operation_timing_summary"] = {"operation_count": 1}
        with self._run_dir(rows) as tmp:
            run_dir = Path(tmp)
            summary = write_execution_trace_artifacts(run_dir)

        self.assertTrue(summary["trace_contract"]["no_s4_specific_counts_required"])
        self.assertEqual(summary["span_count"] > 0, True)

    def test_backfilled_and_inferred_spans_have_explicit_provenance(self) -> None:
        with self._run_dir([_runtime_epoch()]) as tmp:
            run_dir = Path(tmp)
            write_execution_trace_artifacts(run_dir)
            spans = _read_spans(run_dir)

        map_update = next(
            item for item in spans if item["operation"] == "target_generation_map_update"
        )
        gc_delete = next(item for item in spans if item["operation"] == "generation_gc_delete")

        self.assertEqual(map_update["source_type"], "artifact_backfill")
        self.assertTrue(map_update["backfilled_from_existing_artifact"])
        self.assertFalse(map_update["inferred_estimate"])
        self.assertEqual(gc_delete["source_type"], "inferred_estimate")
        self.assertTrue(gc_delete["backfilled_from_existing_artifact"])
        self.assertTrue(gc_delete["inferred_estimate"])

    def test_native_and_inferred_spans_are_not_mixed_without_provenance(self) -> None:
        with self._run_dir([_runtime_epoch()]) as tmp:
            run_dir = Path(tmp)
            summary = write_execution_trace_artifacts(run_dir)
            spans = _read_spans(run_dir)

        self.assertEqual(summary["validation_errors"], [])
        for span in spans:
            self.assertIn("source_type", span)
            self.assertIn("source_detail", span)
            self.assertFalse(span["native_span"] and span["inferred_estimate"])

    def test_critical_path_summary_includes_confidence_and_provenance(self) -> None:
        with self._run_dir([_runtime_epoch(), _parameter_epoch()]) as tmp:
            run_dir = Path(tmp)
            write_execution_trace_artifacts(run_dir)
            critical = json.loads((run_dir / "critical_path_summary.json").read_text())

        provenance = critical["category_provenance"]
        self.assertIn("runtime update work", provenance)
        self.assertIn(
            provenance["runtime update work"]["optimization_decision_confidence"],
            {"low", "medium", "high"},
        )
        self.assertGreaterEqual(provenance["runtime update work"]["backfilled_span_count"], 1)
        self.assertIn("coverage_summary", critical)

    def test_profile_runner_does_not_invent_module_owned_native_evidence(self) -> None:
        with self._run_dir([_runtime_epoch(), _parameter_epoch()]) as tmp:
            run_dir = Path(tmp)
            summary = write_execution_trace_artifacts(run_dir)
            spans = _read_spans(run_dir)

        bpf = next(
            row
            for row in summary["coverage_summary"]
            if row["module"] == "bpf_map_loader"
            and row["operation"] == "target_generation_map_update"
        )
        qdisc = next(
            row
            for row in summary["coverage_summary"]
            if row["module"] == "qdisc_backend" and row["operation"] == "qdisc_update"
        )
        bpf_span = next(
            item for item in spans if item["operation"] == "target_generation_map_update"
        )
        qdisc_span = next(item for item in spans if item["operation"] == "qdisc_update")

        self.assertEqual(bpf["native_span_count"], 0)
        self.assertTrue(bpf["missing_native_spans"])
        self.assertEqual(bpf_span["future_native_owner_module"], "satellite/bpf_mpls_live.py")
        self.assertNotEqual(bpf_span["source_type"], "native_module_instrumentation")
        self.assertEqual(qdisc["native_span_count"], 0)
        self.assertTrue(qdisc["missing_native_spans"])
        self.assertEqual(qdisc_span["future_native_owner_module"], "satellite/network_runtime.py")
        self.assertNotEqual(qdisc_span["source_type"], "native_module_instrumentation")


if __name__ == "__main__":
    unittest.main()
