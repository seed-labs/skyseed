#!/usr/bin/env python3
# encoding: utf-8

import json
import tempfile
import unittest
from pathlib import Path

from inspect_starlink_distance_matrix import (
    render_distance_matrix_summary,
    summarize_distance_matrix_run,
)


def _flow_context(
    *,
    flow_id: str,
    ut_id: str,
    gateway_id: str,
    satellite_path: list[str],
) -> dict:
    return {
        "flow_id": flow_id,
        "ut_id": ut_id,
        "selected_gateway_id": gateway_id,
        "gateway_selection_mode": "stable_preferred_gateway_order",
        "satellite_path": list(satellite_path),
    }


def _action(flow_context: dict) -> dict:
    return {
        "intent": {
            "outcome": {
                "flow_context": dict(flow_context),
            }
        }
    }


class StarlinkDistanceMatrixInspectorTests(unittest.TestCase):
    def test_summarizes_preview_distance_matrix_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "ground_manifest.json").write_text(
                json.dumps(
                    {
                        "data_provenance": {
                            "ground": {
                                "ut_coordinates": [
                                    {"ut_id": "ut1"},
                                    {"ut_id": "ut2"},
                                    {"ut_id": "ut3"},
                                ],
                                "gateway_coordinates": [
                                    {"gateway_id": "gw1"},
                                    {"gateway_id": "gw2"},
                                ],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "runtime_update_plan_summary.json").write_text(
                json.dumps(
                    [
                        {
                            "slice_id": "slice-a",
                            "runtime_update_action_count": 6,
                            "node_subplan_count": 3,
                        },
                        {
                            "slice_id": "slice-b",
                            "runtime_update_action_count": 10,
                            "node_subplan_count": 5,
                        },
                    ]
                ),
                encoding="utf-8",
            )
            (run_dir / "planner_preview.json").write_text(
                json.dumps(
                    {
                        "snapshots": [
                            {
                                "slice_id": "slice-a",
                                "runtime_update_plan": {
                                    "node_subplans": {
                                        "gw1": {
                                            "actions": [
                                                _action(
                                                    _flow_context(
                                                        flow_id="flow-a",
                                                        ut_id="ut1",
                                                        gateway_id="gw1",
                                                        satellite_path=["s1"],
                                                    )
                                                )
                                            ]
                                        },
                                        "gw2": {
                                            "actions": [
                                                _action(
                                                    _flow_context(
                                                        flow_id="flow-b",
                                                        ut_id="ut2",
                                                        gateway_id="gw2",
                                                        satellite_path=[
                                                            "s2",
                                                            "s3",
                                                            "s4",
                                                        ],
                                                    )
                                                )
                                            ]
                                        },
                                    }
                                },
                            },
                            {
                                "slice_id": "slice-b",
                                "runtime_update_plan": {
                                    "node_subplans": {
                                        "gw1": {
                                            "actions": [
                                                _action(
                                                    _flow_context(
                                                        flow_id="flow-c",
                                                        ut_id="ut1",
                                                        gateway_id="gw1",
                                                        satellite_path=["s1", "s2"],
                                                    )
                                                )
                                            ]
                                        }
                                    }
                                },
                            },
                            {
                                "slice_id": "non-runtime-slice",
                                "runtime_update_plan": {
                                    "node_subplans": {},
                                },
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            summary = summarize_distance_matrix_run(run_dir)
            rendered = render_distance_matrix_summary({"runs": [summary]})

        self.assertEqual(summary["all_snapshot_count"], 3)
        self.assertEqual(summary["runtime_snapshot_count"], 2)
        self.assertEqual(summary["selected_ut_count"], 3)
        self.assertEqual(summary["covered_runtime_ut_count"], 2)
        self.assertEqual(summary["runtime_flow_count_min"], 1)
        self.assertEqual(summary["runtime_flow_count_max"], 2)
        self.assertEqual(summary["runtime_flow_count_avg"], 1.5)
        self.assertEqual(summary["isl_hop_count_avg"], 1.0)
        self.assertEqual(summary["isl_hop_count_max"], 2)
        self.assertEqual(summary["satellite_path_length_avg"], 2.0)
        self.assertEqual(summary["satellite_path_length_max"], 3)
        self.assertEqual(summary["runtime_update_action_count_avg"], 8.0)
        self.assertEqual(summary["node_subplan_count_avg"], 4.0)
        self.assertEqual(summary["selected_gateway_counts"], {"gw1": 2, "gw2": 1})
        self.assertIn("covered_uts=2/3", rendered)
        self.assertIn("isl_hops_max=2", rendered)
        self.assertIn("gateways=gw1:2,gw2:1", rendered)


if __name__ == "__main__":
    unittest.main()
