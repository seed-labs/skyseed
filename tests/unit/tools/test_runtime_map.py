"""Check runtime-map projection and optional GZ metadata independently of S4."""
from skyseed import Scene
from skyseed.pc.network import GroundStationZoneRoutingModel
from tools.internet_map import zone_associations


def test_zone_associations_follow_the_routing_model():
    scene = Scene()
    scene.create_ground_station("ground", latitude_deg=0, longitude_deg=0)
    scene.create_zone("zone", latitude_deg=0, longitude_deg=0)

    class CustomGZ(GroundStationZoneRoutingModel):
        pass

    assert tuple(zone_associations(scene, CustomGZ())["zone"]) == ("ground",)
    assert zone_associations(scene, object()) == {}


def test_map_javascript():
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import pytest

    node = os.environ.get("SKYSEED_TEST_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for packet text mapping")
    for name in ("test_internet_map_packets.mjs", "test_internet_map_receipts.cjs", "test_internet_map_interaction.mjs"):
        subprocess.run([node, str(Path(__file__).with_name(name))], check=True)
