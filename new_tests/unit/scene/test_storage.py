import json
import os
import pytest
from skyseed.scene import Scene, ScenePositionSource


def test_scene_file_restores_shared_geometry_identities_and_all_levels_of_data(tiny_scene, tmp_path):
    tiny_scene.get_group("demo").data["operator"] = "A"
    tiny_scene.get_group("demo/shell-0").data["research"] = {"label": "shell"}
    tiny_scene.get_group("demo/shell-0/plane-0").data["purpose"] = "survey"
    group = tiny_scene.create_ground_station_group("ground", data={"owner": "A"})
    tiny_scene.create_ground_station("G2", latitude_deg=50, longitude_deg=10, group=group.group_id)
    path = tmp_path / "tiny.scene"
    tiny_scene.dump(path)
    raw = json.loads(path.read_text())
    assert raw["format"] == "skyseed.scene" and raw["version"] == 2
    shell_data = next(item for item in raw["groups"] if item["kind"] == "shell")
    assert shell_data["altitude_m"] == 550000 and shell_data["inclination_deg"] == 53
    assert raw["nodes"][0]["orbit_id"] == "demo/shell-0/plane-0"
    assert all("altitude_m" not in item and "inclination_deg" not in item for item in raw["nodes"])
    restored = Scene.load(path)
    assert restored.nodes == tiny_scene.nodes and restored.groups == tiny_scene.groups
    assert restored.data == {"source": {"file": "inputs/example.csv"}}
    first, second = restored.get_satellites()
    assert first.orbit is second.orbit is restored.get_group("demo/shell-0/plane-0")
    assert first.orbit.shell is restored.get_group("demo/shell-0")
    assert first.orbit.shell.constellation is restored.get_group("demo")
    assert restored.get_node("G2").group is restored.get_group("ground")
    restored.get_node("G1").data["researcher"]["antenna"] = "different"
    restored.get_group("demo/shell-0").data["research"]["label"] = "different"
    assert tiny_scene.get_node("G1").data["researcher"]["antenna"] == "custom"
    assert tiny_scene.get_group("demo/shell-0").data["research"]["label"] == "shell"


@pytest.mark.parametrize("problem", ["draft-version", "version", "duplicate-node", "duplicate-group",
                                     "missing-orbit", "wrong-parent-kind", "cycle", "phase", "dual-position"])
def test_corrupt_scene_never_returns_partial_data(tiny_scene, tmp_path, problem):
    path = tmp_path / "bad.scene"
    tiny_scene.dump(path)
    raw = json.loads(path.read_text())
    if problem in ("draft-version", "version"):
        raw["version"] = 1 if problem == "draft-version" else 999
    elif problem == "duplicate-node":
        raw["nodes"].append(raw["nodes"][0])
    elif problem == "duplicate-group":
        raw["groups"].append(raw["groups"][0])
    elif problem == "missing-orbit":
        raw["nodes"][0]["orbit_id"] = "absent"
    elif problem == "wrong-parent-kind":
        raw["nodes"][0]["orbit_id"] = "demo"
    elif problem == "cycle":
        raw["groups"][0]["parent_id"] = raw["groups"][-1]["group_id"]
    elif problem == "phase":
        del raw["nodes"][0]["mean_anomaly_deg"]
    else:
        raw["nodes"][0]["position_source"] = {"kind": "custom", "data": {}}
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError) as failure:
        Scene.load(path)
    assert str(path) in str(failure.value)
    if problem in {"missing-orbit", "wrong-parent-kind", "phase", "dual-position"}:
        assert "node 'demo/shell-0/plane-0/sat-0'" in str(failure.value)


@pytest.mark.parametrize("owner", ["scene", "group", "node", "position"])
def test_invalid_edited_data_identifies_owner_and_preserves_file(tiny_scene, tmp_path, owner):
    path = tmp_path / "kept.scene"
    independent = tiny_scene.create_satellite("independent", position=tiny_scene.get_satellites()[0].position)
    tiny_scene.dump(path)
    before = path.read_bytes()
    if owner == "position":
        independent.position_source.data["altitude_m"] = -1
        expected = "satellite 'independent': altitude_m"
    else:
        data, label = {
            "scene": (tiny_scene.data, "Scene.data"),
            "group": (tiny_scene.get_group("demo/shell-0").data, "group 'demo/shell-0'.data"),
            "node": (tiny_scene.get_node("G1").data, "node 'G1'.data"),
        }[owner]
        data["samples"] = [float("nan")]
        expected = label + "['samples'][0]"
    with pytest.raises(ValueError) as failure:
        tiny_scene.dump(path)
    assert expected in str(failure.value)
    assert path.read_bytes() == before


def test_failed_replacement_preserves_file_and_removes_temporary_file(tiny_scene, tmp_path, monkeypatch):
    path = tmp_path / "kept.scene"
    tiny_scene.dump(path)
    before = path.read_bytes()
    def fail_replace(source, destination):
        raise OSError("replacement failed")
    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failed"):
        tiny_scene.dump(path)
    assert path.read_bytes() == before and list(tmp_path.iterdir()) == [path]


def test_group_order_in_file_does_not_change_relationships(tiny_scene, tmp_path):
    path = tmp_path / "unordered.scene"
    tiny_scene.dump(path)
    raw = json.loads(path.read_text())
    raw["groups"].reverse()
    path.write_text(json.dumps(raw))
    restored = Scene.load(path)
    assert restored.nodes == tiny_scene.nodes
    assert restored.get_node_ids(groups=["demo"]) == tiny_scene.get_node_ids(groups=["demo"])
    orbit = restored.get_satellites()[0].orbit
    assert orbit.shell is restored.get_shells("demo")[0]


def test_independent_position_source_round_trip_needs_no_invented_groups(tmp_path):
    scene = Scene()
    scene.create_satellite("survey", position=ScenePositionSource(
        "measured-trajectory", {"file": "samples.csv", "columns": ["utc", "x", "y", "z"]}))
    scene.create_ground_station("G1", latitude_deg=0, longitude_deg=0)
    path = tmp_path / "custom.scene"
    scene.dump(path)
    restored = Scene.load(path)
    assert restored.groups == () and restored.nodes == scene.nodes
    assert restored.get_node("survey").position.data["file"] == "samples.csv"
