#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

import unittest

from satellite.apps.probe_theaterq_timeline import (
    DEFAULT_LIMIT_PACKETS,
    csv_row,
    segment_to_theaterq_extended_row,
    segment_to_theaterq_simple_row,
    trace_mapping_summary,
)
from satellite.apps.compile_parameter_timeline_to_qdisc import scaled_loss_u32


class TheaterQTimelineProbeTests(unittest.TestCase):
    def test_segment_to_simple_trace_uses_theaterq_units(self) -> None:
        segment = self._segment()

        row = segment_to_theaterq_simple_row(segment)

        self.assertEqual(row[0], 2_500_000)
        self.assertEqual(row[1], 1_250_000)
        self.assertEqual(row[2], 12_500_000)
        self.assertEqual(row[3], scaled_loss_u32(2.5))
        self.assertEqual(row[4], DEFAULT_LIMIT_PACKETS)
        self.assertEqual(csv_row(row), f"2500000,1250000,12500000,{scaled_loss_u32(2.5)},1000")

    def test_segment_to_extended_trace_marks_absent_reorder_as_zero(self) -> None:
        row = segment_to_theaterq_extended_row(self._segment())

        self.assertEqual(row[:6], [2_500_000, 1_250_000, 0, 12_500_000, scaled_loss_u32(2.5), 1000])
        self.assertEqual(row[6:], [0, 0, 0, 0])

    def test_trace_mapping_records_queue_and_reorder_as_absent(self) -> None:
        compiled = {
            "summary": {
                "timeline_segment_count": 1,
                "field_coverage": {
                    "delay_ms": {"present": 1, "total_segments": 1, "coverage_ratio": 1.0},
                    "bandwidth_mbps": {"present": 1, "total_segments": 1, "coverage_ratio": 1.0},
                    "loss_percent": {"present": 1, "total_segments": 1, "coverage_ratio": 1.0},
                    "routing_cost": {"present": 1, "total_segments": 1, "coverage_ratio": 1.0},
                },
            },
            "generation_cadence": {"fixed_10s_assumed": False},
        }

        mapping = trace_mapping_summary(compiled)["field_mapping"]

        self.assertEqual(mapping["queue"]["status"], "absent_in_parameter_epoch_artifact")
        self.assertEqual(mapping["reorder"]["status"], "absent_in_parameter_epoch_artifact")
        self.assertIsNone(mapping["routing_cost"]["theaterq_field"])

    @staticmethod
    def _segment() -> dict:
        return {
            "activation_relative_time_seconds": 10.0,
            "end_relative_time_seconds": 12.5,
            "qdisc_parameters": {
                "delay": {"delay_ms": 1.25},
                "rate": {"bandwidth_mbps": 12.5},
                "loss": {"loss_percent": 2.5},
                "queue": {"limit_packets": None},
                "reorder": {"route_id": None, "queue_id": None},
            },
        }


if __name__ == "__main__":
    unittest.main()
