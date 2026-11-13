"""Small scene facts only; no global service or runtime fixtures."""
from datetime import datetime, timezone
import pytest
from skyseed.scene import Scene


@pytest.fixture
def tiny_scene():
    scene = Scene(data={"source": {"file": "inputs/example.csv"}})
    scene.create_constellation("demo", planes=1, satellites_per_plane=2,
                               altitude_km=550, inclination_deg=53,
                               epoch=datetime(2026, 1, 1, tzinfo=timezone.utc))
    scene.create_ground_station("G1", latitude_deg=40, longitude_deg=116,
                                data={"researcher": {"antenna": "custom"}})
    return scene
