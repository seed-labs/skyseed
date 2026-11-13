"""Parameter-built shells preserve domain ownership and accept custom counts."""
from datetime import datetime, timezone

import pytest

from skyseed import Scene
from skyseed.scene import ScenePositionSource


EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_generated_shell_geometry_identity_and_saved_ownership(tmp_path):
    scene = Scene()
    constellation = scene.create_constellation("research")
    shell = scene.create_shell(
        "low", constellation="research", altitude_km=550, inclination_deg=53,
        epoch=EPOCH, planes=2, satellites_per_plane=3, raan_start_deg=350,
        phase_start_deg=10, phase_factor=1, id_prefix="test", first_orbit=5,
    )
    orbits = scene.get_orbits("low")
    assert shell.constellation is constellation
    assert [orbit.group_id for orbit in orbits] == ["test-orbit-005", "test-orbit-006"]
    assert [orbit.raan_deg for orbit in orbits] == [350, 170]
    assert [node.mean_anomaly_deg for node in scene.get_satellites()] == [10, 130, 250, 70, 190, 310]
    assert scene.get_node("test-orbit-006-satellite-0003").orbit is orbits[1]
    assert scene.get_node_ids(groups=["research", "low"]) == [node.node_id for node in scene.nodes]
    scene.nodes[0].data["payload"] = "camera"
    scene.dump(tmp_path / "scene.scene")
    restored = Scene.load(tmp_path / "scene.scene")
    assert restored.nodes == scene.nodes
    assert restored.get_node(scene.nodes[0].node_id).orbit.shell is restored.get_group("low")
    assert restored.get_node(scene.nodes[0].node_id).data == {"payload": "camera"}


@pytest.mark.parametrize("count,total", [(58, 348), (59, 354), (60, 360)])
def test_satellite_count_is_a_parameter_not_a_named_preset(count, total):
    scene = Scene()
    scene.create_constellation("custom")
    scene.create_shell("custom-polar", constellation="custom", altitude_km=560,
                       inclination_deg=97.6, epoch=EPOCH, planes=6,
                       satellites_per_plane=count, phase_factor=1)
    assert len(scene.nodes) == total
    assert len(scene.get_orbits("custom-polar")) == 6
    assert all(len(scene.get_satellites(orbit.group_id)) == count for orbit in scene.get_orbits())
    if count == 60:
        # 60 satellites give six-degree in-plane spacing and one degree per plane.
        assert scene.nodes[1].mean_anomaly_deg == 6
        assert scene.nodes[60].mean_anomaly_deg == 1


def test_explicit_plane_directions_keep_irregular_layouts_compact():
    scene = Scene()
    scene.create_constellation("custom")
    scene.create_shell("irregular", constellation="custom", altitude_km=560,
                       inclination_deg=97.6, epoch=EPOCH, planes=4,
                       satellites_per_plane=2, orbit_raan_deg=[75.7, 87.7, 99.7, 117.7])
    assert [orbit.raan_deg for orbit in scene.get_orbits()] == [75.7, 87.7, 99.7, 117.7]
    assert [node.mean_anomaly_deg for node in scene.nodes] == [0, 180] * 4


def test_invalid_layout_or_late_identity_conflict_does_not_add_partial_shell():
    scene = Scene()
    scene.create_constellation("custom")
    collision = "low-orbit-002-satellite-0002"
    scene.create_satellite(collision, position=ScenePositionSource("custom"))
    before = scene.groups, scene.nodes
    settings = dict(constellation="custom", altitude_km=550, inclination_deg=53,
                    epoch=EPOCH, planes=2, satellites_per_plane=2)
    with pytest.raises(ValueError, match="already exist"):
        scene.create_shell("low", **settings)
    with pytest.raises(ValueError, match="one direction per plane"):
        scene.create_shell("other", **settings, orbit_raan_deg=[0])
    with pytest.raises(ValueError, match="positive integer"):
        scene.create_shell("other", **{**settings, "satellites_per_plane": 0})
    with pytest.raises(ValueError, match="positive integer"):
        scene.create_shell("other", **{**settings, "planes": None})
    with pytest.raises(ValueError, match="share an identity"):
        scene.create_shell("x-orbit-001", **settings, id_prefix="x")
    assert (scene.groups, scene.nodes) == before
