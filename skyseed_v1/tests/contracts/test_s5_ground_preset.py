from __future__ import annotations

import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GROUND_ROOT = PROJECT_ROOT / "data" / "ground"


def load(name: str):
    return json.loads((GROUND_ROOT / name).read_text(encoding="utf-8"))


class S5GroundPresetTests(unittest.TestCase):
    def test_selected_nodes_are_qualified_for_every_two_hour_frame(self) -> None:
        report = load("s5_operational_clean_2h_coverage_report.json")
        self.assertEqual(report["frame_count"], 720)
        self.assertEqual(report["duration_seconds"], 7200)
        self.assertIn("not a periodicity claim", str(report["selection_rule"]).lower())
        for kind in ("gateway", "ut"):
            for identifier in report[f"selected_{kind}_ids"]:
                evidence = report[f"{kind}_coverage"][identifier]
                self.assertEqual(evidence["visible_frame_count"], 720)
                self.assertTrue(evidence["continuously_serviceable"])
                self.assertEqual(evidence["maximum_unserviceable_gap_seconds"], 0)

    def test_runtime_policy_references_only_selected_s5_gateways(self) -> None:
        report = load("s5_operational_clean_2h_coverage_report.json")
        selected = set(report["selected_gateway_ids"])
        policies = load("s5_operational_clean_ut_gateway_policy_9.json")
        self.assertEqual(
            {entry["ut_id"] for entry in policies},
            set(report["selected_ut_ids"]),
        )
        for policy in policies:
            rules = list(policy["rules"])
            self.assertEqual({rule["rule_role"] for rule in rules}, {"default", "special"})
            self.assertEqual(len({rule["gateway_id"] for rule in rules}), 2)
            self.assertTrue({rule["gateway_id"] for rule in rules} <= selected)

    def test_s5_catalog_cardinality_is_explicit(self) -> None:
        self.assertEqual(len(load("s5_operational_clean_gateway_binding_5.json")), 5)
        self.assertEqual(len(load("s5_operational_clean_ut_catalog_9.json")), 9)
        self.assertEqual(len(load("s5_operational_clean_zone_catalog_6.json")), 6)


if __name__ == "__main__":
    unittest.main()
