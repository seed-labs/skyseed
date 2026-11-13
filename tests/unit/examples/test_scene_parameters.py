"""Preset geometry follows reviewed source records; examples build reusable Scenes."""
from datetime import datetime
import json
from pathlib import Path

import pytest
import yaml

from skyseed import Scene
from examples.s4_handover.scene import build_scene


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("name,element_id,plane_ids,total", [
    ("S1", 2, range(1, 73), 1584),
    ("S2", 1, range(1, 73), 1584),
    ("S3", 4, range(1, 37), 720),
    ("S4", 3, range(5, 11), 348),
    ("S5", 3, range(1, 5), 172),
])
def test_preset_matches_reviewed_filing_within_published_precision(name, element_id, plane_ids, total):
    presets = yaml.safe_load((ROOT / "data/starlink/shells.yaml").read_text())["shells"]
    source = json.loads((ROOT / "research/starlink/gen1/shells/fcc-sat-mod-20200417-00037-gen1.json").read_text())
    element = next(item for item in source["shells"] if item["source_element_id"] == element_id)
    source_planes = [plane for plane in element["planes"] if plane["source_plane_id"] in plane_ids]
    settings = dict(presets[name])
    settings["epoch"] = datetime.fromisoformat(settings.pop("epoch_utc"))
    scene = Scene()
    scene.create_constellation("Starlink")
    shell = scene.create_shell(name, constellation="Starlink", **settings)

    assert len(scene.nodes) == total
    assert shell.altitude_m == element["apogee_km"] * 1000
    assert shell.inclination_deg == element["inclination_deg"]
    assert shell.epoch == datetime.fromisoformat(source["system"]["filing_epoch_utc"])
    nodes_by_orbit = {orbit.group_id: [] for orbit in scene.get_orbits()}
    for node in scene.nodes:
        nodes_by_orbit[node.orbit.group_id].append(node)
    for orbit, plane in zip(scene.get_orbits(), source_planes, strict=True):
        expected_id = f"{name.lower()}-orbit-{plane['source_plane_id']:03d}"
        assert orbit.group_id == expected_id
        assert orbit.shell is shell
        assert orbit.raan_deg == pytest.approx(plane["raan_deg"])
        nodes = nodes_by_orbit[orbit.group_id]
        phases = plane["mean_anomaly_deg_by_source_satellite_id"]
        for index, (node, phase) in enumerate(zip(nodes, phases, strict=True), start=1):
            assert node.node_id == f"{expected_id}-satellite-{index:04d}"
            difference = abs((node.mean_anomaly_deg - phase + 180) % 360 - 180)
            assert difference <= .0500001  # Source values are published to one decimal place.


def test_s4_example_keeps_nodes_groups_endpoints_and_metadata(tmp_path):
    scene = build_scene()
    assert len(scene.get_satellites()) == 348
    assert len(scene.get_ground_stations()) == 32
    assert len(scene.endpoints) == 732
    assert scene.nodes[0].node_id == "s4-orbit-005-satellite-0001"
    assert scene.get_satellites()[-1].node_id == "s4-orbit-010-satellite-0058"
    assert scene.data["experiment"]["zone"] == "zone-b16-c033"
    assert scene.data["experiment"]["ut_latitude_deg"] == 30.2741
    assert scene.get_endpoint(scene.data["experiment"]["zone"]).kind == "geographic-zone/v1"
    path = tmp_path / "s4.scene"
    scene.dump(path)
    restored = Scene.load(path)
    assert restored.nodes == scene.nodes
    assert restored.groups == scene.groups
    assert restored.endpoints == scene.endpoints
    assert restored.data == scene.data
