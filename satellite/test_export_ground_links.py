#!/usr/bin/env python3
# encoding: utf-8

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SATELLITE_DIR = Path(__file__).resolve().parent
if str(SATELLITE_DIR) not in sys.path:
    sys.path.insert(0, str(SATELLITE_DIR))

from apps.visualization.export_ground_links import (
    DEFAULT_GATEWAY_CATALOG_PATH,
    DEFAULT_MAX_GROUND_LINK_DISTANCE_KM,
    PLANNED_STARLINK_PHASE1_SHELL_PRESETS,
    VisibleGroundLinkCandidate,
    _earth_clearance_km,
    _normalize_post_url,
    _select_per_satellite_best_links,
    build_ground_links_payload,
    generate_planned_shell_records,
    load_ground_station_catalog,
    NodePosition,
)


def candidate(
    satellite_id: str,
    ground_station_id: str,
    elevation_deg: float,
    distance_km: float,
) -> VisibleGroundLinkCandidate:
    return VisibleGroundLinkCandidate(
        satellite_id,
        ground_station_id,
        elevation_deg,
        distance_km,
        earth_clearance_km=100.0,
    )


def datetime_from_millis(value: str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)


def select_per_satellite(
    candidates,
    *,
    previous_assignment_by_satellite=None,
    hold_steps_by_satellite=None,
    hysteresis_elevation_deg=2.0,
    capacity_by_station=None,
    top_k=1,
):
    return _select_per_satellite_best_links(
        candidates,
        previous_assignment_by_satellite=previous_assignment_by_satellite or {},
        hold_steps_by_satellite=hold_steps_by_satellite or {},
        hysteresis_elevation_deg=hysteresis_elevation_deg,
        capacity_by_station=capacity_by_station or {"gw1": None, "gw2": None},
        top_k=top_k,
        min_hold_steps=2,
        elevation_weight=1.0,
        distance_weight=0.002,
        handover_penalty=3.0,
        load_penalty=0.5,
    )


class ExportGroundLinksTests(unittest.TestCase):
    def test_default_ground_station_catalog_uses_starlink_gateways_file(self) -> None:
        stations = load_ground_station_catalog()

        self.assertTrue(DEFAULT_GATEWAY_CATALOG_PATH.exists())
        self.assertEqual(len(stations), 250)
        self.assertEqual(stations[0].ground_station_id, "starlink-gs-0001-aerzen-germany")
        self.assertEqual(stations[-1].ground_station_id, "starlink-gs-0250-yamaguchi-japan")

    def test_all_planned_shells_generate_s1_through_s5_records(self) -> None:
        records = generate_planned_shell_records(
            shell_id="ALL",
            epoch_utc=datetime_from_millis("1782439276134"),
        )

        expected_count = sum(
            preset.satellite_count
            for preset in PLANNED_STARLINK_PHASE1_SHELL_PRESETS.values()
        )
        self.assertEqual(len(records), expected_count)
        self.assertEqual(records[0].norad_id, 910001)
        self.assertEqual(records[-1].norad_id, 950172)

    def test_markdown_post_url_is_normalized(self) -> None:
        self.assertEqual(
            _normalize_post_url("[http://localhost:9091/api](http://localhost:9091/api)"),
            "http://localhost:9091/api",
        )

    def test_default_distance_cap_is_explicit(self) -> None:
        self.assertEqual(DEFAULT_MAX_GROUND_LINK_DISTANCE_KM, 2500.0)

    def test_zero_distance_cap_disables_distance_filter(self) -> None:
        capped = build_ground_links_payload(
            planned_shell="S4",
            start_timestamp="1782439276134",
            interval="1s",
            steps=1,
            max_satellites=24,
            max_ground_stations=5,
            max_distance_km=1.0,
        )
        disabled = build_ground_links_payload(
            planned_shell="S4",
            start_timestamp="1782439276134",
            interval="1s",
            steps=1,
            max_satellites=24,
            max_ground_stations=5,
            max_distance_km=0.0,
        )

        self.assertEqual(len(capped["links"][0]["groundLinks"]), 0)
        self.assertGreater(len(disabled["links"][0]["groundLinks"]), 0)

    def test_planned_shell_payload_matches_external_schema(self) -> None:
        payload = build_ground_links_payload(
            planned_shell="S5",
            start_timestamp="1782439276134",
            interval="1s",
            steps=3,
            max_satellites=12,
            max_ground_stations=2,
        )

        self.assertEqual(payload["timestamp"], "1782439276134")
        self.assertEqual(payload["interval"], "1s")
        self.assertEqual(len(payload["links"]), 3)
        for sample in payload["links"]:
            self.assertEqual(set(sample), {"groundLinks", "satelliteLinks"})
            self.assertEqual(sample["satelliteLinks"], [])
            sample_links = sample["groundLinks"]
            self.assertIsInstance(sample_links, list)
            for link in sample_links:
                self.assertEqual(set(link), {"satelliteId", "groundStationId"})
                self.assertIsInstance(link["satelliteId"], str)
                self.assertIsInstance(link["groundStationId"], str)

    def test_cli_defaults_to_dry_run_without_post_url(self) -> None:
        script = Path(__file__).resolve().parent / "apps" / "visualization" / "export_ground_links.py"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--planned-shell",
                "S5",
                "--start-timestamp",
                "1782439276134",
                "--interval",
                "1s",
                "--steps",
                "3",
                "--max-satellites",
                "12",
                "--max-ground-stations",
                "2",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["timestamp"], "1782439276134")
        self.assertEqual(payload["interval"], "1s")
        self.assertEqual(len(payload["links"]), 3)
        self.assertTrue(result.stdout.startswith('{"timestamp":"1782439276134","interval":"1s","links":['))
        first_non_empty_sample = next(
            (sample["groundLinks"] for sample in payload["links"] if sample["groundLinks"]),
            None,
        )
        if first_non_empty_sample is not None:
            self.assertEqual(list(first_non_empty_sample[0]), ["satelliteId", "groundStationId"])

    def test_custom_ground_station_id_is_preserved_in_payload(self) -> None:
        station_id = "starlink-gs-0178-puerto-montt-chile"
        with tempfile.TemporaryDirectory() as tmp:
            catalog_path = Path(tmp) / "ground_stations.json"
            catalog_path.write_text(
                json.dumps(
                    [
                        {
                            "groundStationId": station_id,
                            "latitude_deg": 64.1466,
                            "longitude_deg": -21.9426,
                            "altitude_km": 0.0,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            payload = build_ground_links_payload(
                planned_shell="S4",
                ground_station_catalog=catalog_path,
                start_timestamp="1782439276134",
                interval="1s",
                steps=1,
                max_satellites=24,
            )

        emitted_station_ids = {
            link["groundStationId"]
            for sample in payload["links"]
            for sample_links in [sample["groundLinks"]]
            for link in sample_links
        }
        self.assertEqual(emitted_station_ids, {station_id})

    def test_cli_runs_when_copied_outside_skyseed_tree(self) -> None:
        script = Path(__file__).resolve().parent / "apps" / "visualization" / "export_ground_links.py"
        with tempfile.TemporaryDirectory() as tmp:
            copied_script = Path(tmp) / "export_ground_links.py"
            copied_script.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(copied_script),
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
                    "--max-ground-stations",
                    "2",
                ],
                check=True,
                capture_output=True,
                cwd="/tmp",
                text=True,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["timestamp"], "1782439276134")
        self.assertEqual(payload["interval"], "1s")
        self.assertEqual(len(payload["links"]), 2)
        for sample in payload["links"]:
            self.assertEqual(set(sample), {"groundLinks", "satelliteLinks"})

    def test_per_satellite_selection_allows_one_gateway_to_serve_many_satellites(self) -> None:
        selected = select_per_satellite(
            (
                candidate("sat-a", "gw1", 40.0, 900.0),
                candidate("sat-a", "gw2", 30.0, 1000.0),
                candidate("sat-b", "gw1", 35.0, 950.0),
                candidate("sat-b", "gw2", 20.0, 1100.0),
            ),
        )

        self.assertEqual(
            [(item.satellite_id, item.ground_station_id) for item in selected],
            [("sat-a", "gw1"), ("sat-b", "gw1")],
        )

    def test_capacity_limit_spills_satellites_to_next_best_ground_station(self) -> None:
        selected = select_per_satellite(
            (
                candidate("sat-a", "gw1", 40.0, 900.0),
                candidate("sat-a", "gw2", 30.0, 1000.0),
                candidate("sat-b", "gw1", 35.0, 950.0),
                candidate("sat-b", "gw2", 25.0, 1050.0),
            ),
            capacity_by_station={"gw1": 1, "gw2": None},
        )

        self.assertEqual(
            [(item.satellite_id, item.ground_station_id) for item in selected],
            [("sat-a", "gw1"), ("sat-b", "gw2")],
        )

    def test_hysteresis_keeps_previous_ground_station_inside_margin(self) -> None:
        selected = select_per_satellite(
            (
                candidate("sat-a", "gw1", 38.0, 950.0),
                candidate("sat-a", "gw2", 39.5, 930.0),
            ),
            previous_assignment_by_satellite={"sat-a": "gw1"},
        )

        self.assertEqual(
            [(item.satellite_id, item.ground_station_id) for item in selected],
            [("sat-a", "gw1")],
        )

    def test_score_includes_distance_after_elevation(self) -> None:
        selected = select_per_satellite(
            (
                candidate("sat-a", "gw1", 30.0, 900.0),
                candidate("sat-a", "gw2", 30.0, 1400.0),
            )
        )

        self.assertEqual(selected[0].ground_station_id, "gw1")
        self.assertGreater(selected[0].score, 0.0)

    def test_top_k_can_select_multiple_ground_stations_per_satellite(self) -> None:
        selected = select_per_satellite(
            (
                candidate("sat-a", "gw1", 40.0, 900.0),
                candidate("sat-a", "gw2", 38.0, 950.0),
            ),
            top_k=2,
        )

        self.assertEqual(
            [(item.satellite_id, item.ground_station_id) for item in selected],
            [("sat-a", "gw1"), ("sat-a", "gw2")],
        )

    def test_earth_clearance_detects_chord_through_earth(self) -> None:
        clearance = _earth_clearance_km(
            NodePosition(EARTH_RADIUS_KM := 6371.0, 0.0, 0.0),
            NodePosition(-EARTH_RADIUS_KM, 0.0, 0.0),
        )

        self.assertLess(clearance, 0.0)


if __name__ == "__main__":
    unittest.main()
