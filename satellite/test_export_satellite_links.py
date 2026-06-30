#!/usr/bin/env python3
# encoding: utf-8

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SATELLITE_DIR = Path(__file__).resolve().parent
if str(SATELLITE_DIR) not in sys.path:
    sys.path.insert(0, str(SATELLITE_DIR))

from apps.visualization.export_satellite_links import (
    build_satellite_links_payload,
    build_satellite_links_result,
)


class ExportSatelliteLinksTests(unittest.TestCase):
    def test_planned_shell_payload_matches_external_schema(self) -> None:
        payload = build_satellite_links_payload(
            planned_shell="S4",
            start_timestamp="1782439276134",
            interval="1s",
            steps=3,
            max_satellites=24,
        )

        self.assertEqual(payload["timestamp"], "1782439276134")
        self.assertEqual(payload["interval"], "1s")
        self.assertEqual(len(payload["satelliteLinks"]), 3)
        for sample_links in payload["satelliteLinks"]:
            self.assertIsInstance(sample_links, list)
            for link in sample_links:
                self.assertEqual(set(link), {"satelliteAId", "satelliteBId"})
                self.assertIsInstance(link["satelliteAId"], str)
                self.assertIsInstance(link["satelliteBId"], str)

    def test_same_plane_links_can_be_exported_without_cross_plane_links(self) -> None:
        result = build_satellite_links_result(
            planned_shell="S4",
            start_timestamp="1782439276134",
            interval="1s",
            steps=1,
            max_satellites=24,
            include_cross_plane=False,
        )

        self.assertGreater(len(result["payload"]["satelliteLinks"][0]), 0)

    def test_nearest_cross_plane_strategy_can_choose_non_same_index_pairs(self) -> None:
        nearest = build_satellite_links_result(
            planned_shell="S4",
            start_timestamp="1782439276134",
            interval="30s",
            steps=1,
            max_satellites=120,
            include_same_plane=False,
            cross_plane_strategy="nearest-adjacent-plane",
            cross_plane_candidate_window=5,
        )
        same_index = build_satellite_links_result(
            planned_shell="S4",
            start_timestamp="1782439276134",
            interval="30s",
            steps=1,
            max_satellites=120,
            include_same_plane=False,
            cross_plane_strategy="same-index",
        )

        nearest_links = {
            (link["satelliteAId"], link["satelliteBId"])
            for link in nearest["payload"]["satelliteLinks"][0]
        }
        same_index_links = {
            (link["satelliteAId"], link["satelliteBId"])
            for link in same_index["payload"]["satelliteLinks"][0]
        }
        self.assertGreater(len(nearest_links), 0)
        self.assertNotEqual(nearest_links, same_index_links)

    def test_cli_runs_when_four_file_bundle_is_copied_outside_skyseed_tree(self) -> None:
        apps_dir = Path(__file__).resolve().parent / "apps" / "visualization"
        bundle_names = (
            "export_ground_links.py",
            "export_ground_links_USAGE.md",
            "export_satellite_links.py",
            "export_satellite_links_USAGE.md",
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            for name in bundle_names:
                shutil.copy2(apps_dir / name, target / name)
            result = subprocess.run(
                [
                    sys.executable,
                    str(target / "export_satellite_links.py"),
                    "--planned-shell",
                    "S4",
                    "--start-timestamp",
                    "1782439276134",
                    "--interval",
                    "1s",
                    "--steps",
                    "2",
                    "--max-satellites",
                    "24",
                ],
                check=True,
                capture_output=True,
                cwd="/tmp",
                text=True,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["timestamp"], "1782439276134")
        self.assertEqual(payload["interval"], "1s")
        self.assertEqual(len(payload["satelliteLinks"]), 2)

if __name__ == "__main__":
    unittest.main()
