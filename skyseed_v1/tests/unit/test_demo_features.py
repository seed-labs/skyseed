from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import skyseed_v1.visualization.demo_globe as demo_globe
from skyseed_v1.operations.demo_ping import (
    _ping_once,
    latest_ping_target,
    static_ping_targets,
)
from skyseed_v1.visualization.demo_globe import (
    _HTML,
    build_run_globe_payload,
)
from skyseed_v1.topology.experiment import FrozenVisualizationLinkVisibilityModel


class DemoFeatureTests(unittest.TestCase):
    def test_independent_visualization_planner_does_not_exist(self) -> None:
        self.assertFalse(hasattr(demo_globe, "build_globe_payload"))

    def _write_run_projection_fixture(
        self,
        root: Path,
        *,
        physical_timestamp: str,
        frame_offset: int = 0,
    ) -> Path:
        run = root / "example" / "output" / "run-1"
        config = run / "config"
        config.mkdir(parents=True)
        (run / "effective_profile.json").write_text(
            json.dumps({"runner_args": {"planned_shell_id": "S4"}}), encoding="utf-8"
        )
        nodes = {
            "stl-1": {
                "node_type": "satellite",
                "position_state": {"x_km": 6931.0, "y_km": 0.0, "z_km": 0.0},
            },
            "ut1": {
                "node_type": "user_terminal",
                "fixed_coordinates": {"latitude_deg": 0.0, "longitude_deg": 0.0},
                "attachment": {"active_serving_satellite_id": "stl-1"},
            },
            "gw1": {
                "node_type": "gateway",
                "fixed_coordinates": {"latitude_deg": 1.0, "longitude_deg": 1.0},
            },
        }
        snapshots = []
        for index, timestamp in enumerate(("2026-07-09T00:00:00Z", "2026-07-09T00:00:10Z")):
            snapshots.append(
                {
                    "time_slice": {"absolute_time_utc": timestamp},
                    "nodes": nodes,
                    "ut_zone_membership": {"ut1": "zone1"},
                    "zones": {
                        "zone1": {
                            "active_serving_satellite_id": "stl-1",
                            "fixed_coordinates": {"latitude_deg": 0.0, "longitude_deg": 0.0},
                            "radius_km": 100.0,
                        }
                    },
                    "planner_hints": {"ut_gateway_policies": {}},
                }
            )
        (config / "snapshot_preview.json").write_text(
            json.dumps(
                {
                    "pipeline": {
                        "visualization_link_frame_offset": frame_offset,
                    },
                    "snapshots": snapshots,
                }
            ),
            encoding="utf-8",
        )
        (config / "visualization_physical_links.json").write_text(
            json.dumps(
                {
                    "timestamp": physical_timestamp,
                    "interval": "10s",
                    "links": [
                        {
                            "satelliteLinks": [],
                            "groundLinks": [{"frame": index}],
                            "marker": index,
                        }
                        for index in range(frame_offset + 1)
                    ],
                }
            ),
            encoding="utf-8",
        )
        return run

    def test_ping_target_uses_latest_valid_non_superseded_route(self) -> None:
        records = [
            {
                "ut_id": "ut1",
                "target_ip": "198.18.1.1",
                "observation_status": "valid",
                "superseded": False,
            },
            {
                "ut_id": "ut2",
                "target_ip": "198.18.2.1",
                "observation_status": "superseded",
                "superseded": True,
            },
            {
                "ut_id": "ut3",
                "target_ip": "198.18.3.1",
                "observation_status": "valid",
                "superseded": False,
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime_ping_observation.jsonl"
            path.write_text(
                "\n".join(json.dumps(item) for item in records) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(latest_ping_target(path)["ut_id"], "ut3")
            self.assertEqual(latest_ping_target(path, "ut1")["target_ip"], "198.18.1.1")
            self.assertIsNone(latest_ping_target(path, "ut2"))

    def test_globe_is_standalone_and_exposes_semantic_layers(self) -> None:
        self.assertNotIn("https://", _HTML)
        self.assertNotIn("http://", _HTML)
        for layer in ("isl", "gw", "ut", "policy", "zones", "labels"):
            self.assertIn(f'data-layer="{layer}"', _HTML)
        self.assertIn('id="time"', _HTML)
        self.assertIn("__SKYSEED_DATA__", _HTML)
        # Satellite frames encode [id, latitude, longitude, altitude].  Losing
        # this normalization turns the id into NaN and makes the timeline look
        # static even though its source frames differ.
        self.assertIn("function coordinates(p)", _HTML)
        self.assertIn("typeof p[0]==='string'?p.slice(1):p", _HTML)
        self.assertIn("if(A[2]<0||B[2]<0)return", _HTML)
        self.assertIn("f.utAttachments.forEach", _HTML)
        self.assertNotIn("f.utAccessCandidates", _HTML)

    def test_run_globe_projects_exact_frozen_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self._write_run_projection_fixture(
                Path(directory), physical_timestamp="1783555200000"
            )
            payload = build_run_globe_payload(run)
        self.assertEqual(payload["schema"], "skyseed-run-globe-v1")
        self.assertEqual(
            payload["frames"][0]["utAttachments"],
            [{"groundStationId": "ut1", "satelliteId": "stl-1", "zoneId": "zone1"}],
        )

    def test_run_globe_rejects_snapshot_physical_time_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self._write_run_projection_fixture(
                Path(directory), physical_timestamp="1783555680000"
            )
            with self.assertRaisesRegex(ValueError, "canonical segment time mismatch"):
                build_run_globe_payload(run)

    def test_run_globe_uses_successor_offset_into_shared_physical_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self._write_run_projection_fixture(
                Path(directory),
                physical_timestamp="1783554720000",
                frame_offset=48,
            )
            payload = build_run_globe_payload(run)
        self.assertEqual(payload["sourceArtifacts"][0]["physicalFrameOffset"], 48)
        self.assertEqual(payload["frames"][0]["gatewayAccess"], [{"frame": 48}])

    def test_frozen_visibility_model_applies_shared_timeline_offset(self) -> None:
        model = FrozenVisualizationLinkVisibilityModel(
            link_payload={
                "interval": "10s",
                "links": [{"frame": index} for index in range(50)],
            },
            frame_offset=48,
        )
        orbit_slice = Mock(time_slice={"relative_time_seconds": 10.0})
        self.assertEqual(model._frame_for_slice(orbit_slice)["frame"], 49)

    def test_static_ping_targets_resolve_every_policy_rule(self) -> None:
        config = Mock()
        config.ground.ut_ids = ("ut1",)
        config.ground.ut_gateway_policy_catalog = "policy.json"
        config.ground.gateway_binding_catalog = "bindings.json"
        policy = [{
            "ut_id": "ut1",
            "rules": [
                {"gateway_id": "gw1", "prefix": "0.0.0.0/0", "rule_role": "default"},
                {"gateway_id": "gw2", "prefix": "198.18.2.1/32", "rule_role": "special"},
            ],
        }]
        bindings = [
            {"gateway_id": "gw1", "gateway_service_ipv4": "198.18.1.1/32"},
            {"gateway_id": "gw2", "gateway_service_ipv4": "198.18.2.1/32"},
        ]
        with patch(
            "skyseed_v1.operations.demo_ping._catalog",
            side_effect=(policy, bindings),
        ):
            targets = static_ping_targets(config, None)
        self.assertEqual([item["target_ip"] for item in targets], ["198.18.1.1", "198.18.2.1"])
        self.assertEqual([item["rule_role"] for item in targets], ["default", "special"])

    def test_ping_executes_inside_the_resolved_ut_container(self) -> None:
        completed = Mock(returncode=0, stdout="ok", stderr="")
        with patch("skyseed_v1.operations.demo_ping.subprocess.run", return_value=completed) as run:
            result = _ping_once({"ut_id": "ut7", "target_ip": "198.18.7.1"}, 3)
        self.assertEqual(result, (0, "ok", ""))
        run.assert_called_once_with(
            ["docker", "exec", "ut7", "ping", "-n", "-c", "3", "-W", "2", "198.18.7.1"],
            capture_output=True,
            text=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
