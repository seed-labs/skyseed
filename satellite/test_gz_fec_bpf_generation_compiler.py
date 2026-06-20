#!/usr/bin/env python3
# encoding: utf-8

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "apps"))

from compile_gz_fec_to_bpf_generation import (  # noqa: E402
    active_lookup_decision,
    compile_generation_map,
    lookup_decision,
)


def forward_fec(fec_id: str, gateway: str, satellite: str) -> dict:
    return {
        "fec_id": fec_id,
        "group_kind": "g_forward_fec",
        "selected_gateway_id": gateway,
        "selected_gateway_satellite_id": satellite,
        "serving_satellite_id": satellite,
        "zone_id": "zone-north",
        "forward_link_ids": [f"{gateway}-gateway-slot0"],
        "forward_labels": [11792],
        "satellite_path": [satellite],
    }


def return_fec(fec_id: str, gateway: str, satellite: str) -> dict:
    return {
        "fec_id": fec_id,
        "group_kind": "gz_return_fec",
        "selected_gateway_id": gateway,
        "selected_gateway_satellite_id": satellite,
        "zone_serving_satellite_id": satellite,
        "zone_id": "zone-north",
        "reverse_link_ids": [f"{gateway}-gateway-slot0"],
        "reverse_labels": [200000],
        "satellite_path": [satellite],
    }


class GzFecBpfGenerationCompilerTests(unittest.TestCase):
    def test_compiles_generation_maps_and_fail_closed_guards(self) -> None:
        plan_sequence = {
            "model": "gz_fec_plan_sequence_v1",
            "plans": [
                {
                    "plan_id": "rtplan-gen0",
                    "target_snapshot": {"slice_id": "slice-0", "snapshot_id": "snap-0"},
                    "forward_fecs": [forward_fec("g-old", "gw1", "stl-1")],
                    "return_fecs": [return_fec("gz-stable", "gw1", "stl-1")],
                },
                {
                    "plan_id": "rtplan-gen1",
                    "target_snapshot": {"slice_id": "slice-1", "snapshot_id": "snap-1"},
                    "forward_fecs": [forward_fec("g-new", "gw2", "stl-2")],
                    "return_fecs": [return_fec("gz-stable", "gw1", "stl-1")],
                },
            ],
        }
        diff_sequence = {
            "model": "gz_update_diff_summary_v1",
            "diffs": [
                {"removed_fec_groups": []},
                {
                    "removed_fec_groups": ["g-old"],
                    "estimated_lfib_route_operation_items": 3,
                },
            ],
        }
        temporal_plan = {
            "runtime_epochs": [
                {"slice_id": "slice-0", "epoch_id": "runtimeepoch-0"},
                {"slice_id": "slice-1", "epoch_id": "runtimeepoch-1"},
            ]
        }

        compiled = compile_generation_map(
            plan_sequence,
            diff_sequence,
            temporal_plan,
            active_generation=0,
        )

        self.assertEqual(compiled["summary"]["generation_count"], 2)
        self.assertEqual(compiled["summary"]["active_fec_count"], 4)
        self.assertEqual(compiled["summary"]["inactive_fec_tombstone_count"], 1)
        self.assertEqual(compiled["summary"]["fec_kind_counts"], {"G": 2, "GZ": 2})
        self.assertTrue(compiled["self_checks"]["missing_generation_fail_closed"]["ok"])
        self.assertTrue(compiled["self_checks"]["missing_fec_fail_closed"]["ok"])
        self.assertTrue(compiled["self_checks"]["inactive_fec_fail_closed"]["ok"])
        self.assertTrue(compiled["self_checks"]["future_generation_not_active"]["ok"])

        self.assertEqual(
            lookup_decision(compiled, 999, "g-old")["reason"],
            "missing_generation",
        )
        self.assertEqual(
            lookup_decision(compiled, 0, "not-a-real-fec")["reason"],
            "missing_fec",
        )
        self.assertEqual(lookup_decision(compiled, 1, "g-old")["reason"], "inactive_fec")
        self.assertNotEqual(active_lookup_decision(compiled, "g-new")["reason"], "active_fec")

        gen0_g = compiled["generations"][0]["fec_forwarding"][0]
        self.assertEqual(gen0_g["fec_kind"], "G")
        self.assertEqual(gen0_g["selected_gateway_id"], "gw1")
        self.assertEqual(gen0_g["responsible_satellite_id"], "stl-1")
        self.assertEqual(gen0_g["next_hop"]["logical_node"], "gw1")
        self.assertIn("next_hop_gateway", gen0_g["expected_counter_labels"])

        gen0_gz = compiled["generations"][0]["fec_forwarding"][1]
        self.assertEqual(gen0_gz["fec_kind"], "GZ")
        self.assertTrue(gen0_gz["local_delivery_marker"]["value"])
        self.assertIn("local_delivery", gen0_gz["expected_counter_labels"])

        leakage = compiled["linux_mpls_label_leakage"]
        self.assertTrue(leakage["found"])
        self.assertIn("forward_labels", leakage["ignored_by_compiler"])
        self.assertIn("reverse_labels", leakage["ignored_by_compiler"])
        self.assertIn("estimated_lfib_route_operation_items", leakage["ignored_by_compiler"])


if __name__ == "__main__":
    unittest.main()
