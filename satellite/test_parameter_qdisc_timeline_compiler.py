#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

import unittest

from satellite.apps.compile_parameter_timeline_to_qdisc import (
    compile_qdisc_timeline,
    compact_for_baseline,
    scaled_loss_u32,
)


class ParameterQdiscTimelineCompilerTests(unittest.TestCase):
    def test_compiles_link_segments_and_end_times(self) -> None:
        artifacts = {
            "source_root": "/tmp/fixture",
            "temporal_plan_path": "/tmp/fixture/temporal_plan.json",
            "parameter_epoch_summary_path": "/tmp/fixture/parameter_epoch_summary.json",
            "epoch_execution_details_path": "/tmp/fixture/epoch_execution_details.jsonl",
            "temporal_plan": {
                "parameter_epochs": [
                    {
                        "epoch_id": "parameterepoch-a",
                        "slice_id": "slice-a",
                        "sample_id": "sample-a",
                        "trigger_reasons": ["routing_cost_changed"],
                    }
                ]
            },
            "parameter_epoch_summary": [],
            "epoch_execution_details": [
                self._record("parameterepoch-a", 3.0, "2026-06-11T00:00:03Z", 10.0),
                self._record("parameterepoch-b", 8.5, "2026-06-11T00:00:08Z", 20.0),
            ],
        }

        compiled = compile_qdisc_timeline(artifacts)
        self.assertFalse(compiled["generation_cadence"]["fixed_10s_assumed"])
        self.assertEqual(compiled["summary"]["link_timeline_count"], 1)
        self.assertEqual(compiled["summary"]["timeline_segment_count"], 2)
        link = compiled["link_timelines"][0]
        self.assertEqual(link["segments"][0]["end_relative_time_seconds"], 8.5)
        self.assertIsNone(link["segments"][1]["end_relative_time_seconds"])
        self.assertEqual(
            compiled["summary"]["field_coverage"]["delay_ms"]["present"],
            2,
        )
        self.assertEqual(
            link["segments"][0]["qdisc_parameters"]["loss"]["loss_scaled_u32"],
            scaled_loss_u32(1.5),
        )

    def test_compact_report_keeps_scaffold_not_kernel_demo(self) -> None:
        artifacts = {
            "source_root": "/tmp/fixture",
            "temporal_plan_path": "/tmp/fixture/temporal_plan.json",
            "parameter_epoch_summary_path": "/tmp/fixture/parameter_epoch_summary.json",
            "epoch_execution_details_path": "/tmp/fixture/epoch_execution_details.jsonl",
            "temporal_plan": {},
            "parameter_epoch_summary": [],
            "epoch_execution_details": [
                self._record("parameterepoch-a", 0.0, "2026-06-11T00:00:00Z", 10.0),
            ],
        }

        report = compact_for_baseline(compile_qdisc_timeline(artifacts))
        self.assertFalse(report["scope"]["external_kernel_module_loaded"])
        self.assertFalse(report["isolated_fallback_demo_scaffold"]["runs_kernel_qdisc"])
        self.assertTrue(report["theaterq_feasibility"]["source_accessible"])
        self.assertTrue(report["theaterq_feasibility"]["kernel_module_required"])

    def test_counts_noop_parameter_records_without_qdisc_updates(self) -> None:
        artifacts = {
            "source_root": "/tmp/fixture",
            "temporal_plan_path": "/tmp/fixture/temporal_plan.json",
            "parameter_epoch_summary_path": "/tmp/fixture/parameter_epoch_summary.json",
            "epoch_execution_details_path": "/tmp/fixture/epoch_execution_details.jsonl",
            "temporal_plan": {"parameter_epochs": []},
            "parameter_epoch_summary": [],
            "epoch_execution_details": [
                self._record("parameterepoch-a", 3.0, "2026-06-11T00:00:03Z", 10.0),
                {
                    "epoch_id": "parameterepoch-noop",
                    "epoch_type": "parameter_epoch",
                    "activation_relative_time_seconds": 4.0,
                    "activation_time_utc": "2026-06-11T00:00:04Z",
                    "host_mutation_execution": {"apply_results": []},
                },
            ],
        }

        compiled = compile_qdisc_timeline(artifacts)

        self.assertEqual(compiled["summary"]["parameter_epoch_detail_record_count"], 2)
        self.assertEqual(compiled["summary"]["timeline_segment_count"], 1)
        self.assertEqual(
            compiled["summary"]["skipped_parameter_records_without_qdisc_updates"],
            1,
        )

    @staticmethod
    def _record(epoch_id: str, activation: float, activation_utc: str, delay_ms: float) -> dict:
        return {
            "epoch_id": epoch_id,
            "epoch_type": "parameter_epoch",
            "activation_group_id": f"activation-{activation}",
            "activation_relative_time_seconds": activation,
            "activation_time_utc": activation_utc,
            "host_mutation_execution": {
                "apply_results": [
                    {
                        "operation": "update_slot_parameters",
                        "slot_name": "ut101-access-slot0",
                        "slot_type": "ut_access",
                        "resulting_state": {
                            "binding": {"ut_id": "ut101", "satellite_id": "stl-1"},
                            "runtime_parameters": {
                                "bandwidth_mbps": 123.4,
                                "delay_ms": delay_ms,
                                "loss_percent": 1.5,
                                "routing_cost": 42,
                                "slot_type": "ut_access",
                            },
                        },
                    }
                ]
            },
        }


if __name__ == "__main__":
    unittest.main()
