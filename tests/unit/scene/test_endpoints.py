import json
import pytest
from skyseed.scene import Scene, AccessEndpoint


def test_endpoints_have_separate_identity_and_survive_scene_files(tmp_path):
    scene = Scene()
    scene.create_ground_station("shared", latitude_deg=0, longitude_deg=0)
    zone = scene.create_zone("shared", latitude_deg=10, longitude_deg=179,
                             bounds={"south_deg": 5, "north_deg": 15, "west_deg": 170, "east_deg": -170})
    terminal = scene.create_endpoint("ut", kind="terminal/v1", data={"attachment": "shared"})
    assert scene.get_endpoint("shared") is zone
    assert scene.get_endpoints("terminal/v1") == (terminal,)
    path = tmp_path / "endpoints.scene"
    scene.dump(path)
    restored = Scene.load(path)
    assert restored.endpoints == scene.endpoints
    assert len(restored.nodes) == 1  # Logical endpoints are not deployed nodes.
    with pytest.raises(ValueError, match="duplicate endpoint"):
        scene.add_endpoint(zone)
    restored.get_endpoint("ut").data["attachment"] = "changed"
    assert terminal.data["attachment"] == "shared"
    raw = json.loads(path.read_text())
    del raw["endpoints"]
    path.write_text(json.dumps(raw))
    assert Scene.load(path).endpoints == ()  # Existing v2 scenes remain readable.


def test_default_zones_cover_globe_and_are_optional():
    scene = Scene()
    assert scene.endpoints == ()
    zones = scene.create_default_zones()
    assert len({zone.endpoint_id for zone in zones}) == len(zones)
    area = 0
    for zone in zones:
        bounds = zone.data["bounds"]
        width = (bounds["east_deg"] - bounds["west_deg"]) % 360
        area += width * (bounds["north_deg"] - bounds["south_deg"])
    assert area == pytest.approx(360 * 180)
    assert min(zone.data["bounds"]["south_deg"] for zone in zones) == -90
    assert max(zone.data["bounds"]["north_deg"] for zone in zones) == 90
    with pytest.raises(ValueError, match="already exist"):
        scene.create_default_zones()
    assert scene.endpoints == zones


def test_edited_invalid_endpoint_is_rejected_before_replacing_file(tmp_path):
    scene = Scene()
    zone = scene.create_zone("z", latitude_deg=0, longitude_deg=0)
    path = tmp_path / "scene.json"
    scene.dump(path)
    previous = path.read_bytes()
    zone.data["latitude_deg"] = 100
    with pytest.raises(ValueError, match="latitude_deg"):
        scene.dump(path)
    assert path.read_bytes() == previous
