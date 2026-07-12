from __future__ import annotations

import hashlib
import json
import unittest

from skyseed_v1.topology.pipeline import build_demo_topology_snapshot_pipeline


class TopologyPipelineTests(unittest.TestCase):
    def test_demo_pipeline_has_stable_stage_ownership(self) -> None:
        pipeline = build_demo_topology_snapshot_pipeline()

        self.assertEqual(
            pipeline.metadata(),
            {
                "orbit_producer": "mock_time_slice_orbit_producer",
                "visibility_model": "static_visibility_model",
                "zone_generator": "explicit_zone_generator",
                "topology_slicer": "static_topology_slicer",
            },
        )

    def test_demo_pipeline_snapshot_sequence_is_stable(self) -> None:
        snapshots = build_demo_topology_snapshot_pipeline().produce_snapshots()
        payload = [snapshot.to_dict() for snapshot in snapshots]
        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))

        self.assertEqual(
            [snapshot.snapshot_id for snapshot in snapshots],
            ["topsnap-6b2d78ea6353b3e6", "topsnap-1e7124cf6c1c3f17"],
        )
        self.assertTrue(all(snapshot.validation_result().is_valid for snapshot in snapshots))
        self.assertEqual(
            hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "5c902dc9471f32237ffb23ff973ff2e743a3239caec05d88b3670eef62ca2f86",
        )


if __name__ == "__main__":
    unittest.main()
