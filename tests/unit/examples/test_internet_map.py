"""The runtime tool joins deployed Scene nodes to accepted Runner operations."""
from tools.internet_map import runtime

def test_map_includes_deployed_nodes_absent_from_physical_plans(tiny_scene, tmp_path, monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from unittest.mock import Mock

    import yaml
    from seedemu import Docker, Routing
    from skyseed import SkyBase, SkyEmulator, PlanCompiler
    from skyseed.pc.network import ForwardingRule, LogicalExit, RouteDestination, RoutePlan
    from examples.s4_handover.experiment.ut.map import display_terminal
    from functools import partial

    from skyseed.scene import ScenePositionSource
    tiny_scene.create_satellite("independent", position=ScenePositionSource("test/track"))
    tiny_scene.create_ground_station("G2", latitude_deg=0, longitude_deg=0)
    tiny_scene.create_zone("zone", latitude_deg=30, longitude_deg=120)
    tiny_scene.data["experiment"] = {"ut_latitude_deg": 30, "ut_longitude_deg": 120,
                                     "ut_address": "192.0.2.2/32", "zone": "zone"}
    emulator, base = SkyEmulator(), SkyBase()
    autonomous_system = base.createSkyAutonomousSystem(150)
    autonomous_system.loadScene(tiny_scene)
    autonomous_system.createNetwork("lan", "10.150.0.0/24")
    emulator.addLayer(base)
    emulator.addLayer(Routing())
    emulator.getNode("G1").joinNetwork("lan", "10.150.0.1")
    emulator.getNode("G2").joinNetwork("lan", "10.150.0.2")
    emulator.render()
    directory = tmp_path / "deployment"
    emulator.compile(Docker(internetMapEnabled=False), str(directory))
    satellite = tiny_scene.get_satellites()[0].node_id
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rule = ForwardingRule(RouteDestination("endpoint", "zone"), "push",
                          LogicalExit(("G1", satellite), "G1"), outgoing_label=20)
    logical = RoutePlan(start, start + timedelta(seconds=1), {"G1": (rule,)})
    physical, = PlanCompiler(directory).compile_plans(route_plans=[logical])
    assert "G2" not in {item["node_id"] for item in physical.targets["nodes"]}

    # Keep real SEED labels and compiled targets; replace only live Docker facts/calls.
    compose = yaml.safe_load((directory / "docker-compose.yml").read_text())
    containers = {service: {"Id": service, "Name": "/" + service, "State": {"Pid": index},
                             "Config": {"Labels": spec.get("labels", {})}}
                  for index, (service, spec) in enumerate(compose["services"].items(), start=1)}
    deployment = SimpleNamespace(containers=lambda: containers)
    (tmp_path / "terminal.json").write_text(json.dumps({"ut_container": "ut", "ground_ips": {}}))
    zones = {"zone": ["G1"]}
    ut = {"Id": "ut", "Name": "/ut", "State": {"Pid": 999}, "Config": {"Labels": {}}}
    check_output = Mock(side_effect=[json.dumps([ut]).encode(), "map-container\n"])
    monkeypatch.setattr(runtime.subprocess, "check_output", check_output)
    monkeypatch.setattr(runtime.subprocess, "run", Mock())
    monkeypatch.setattr(runtime.InternetMap, "_wait_ready", Mock())

    basic = runtime.configuration(tiny_scene, deployment, [physical], None, None, None, None)
    assert set(basic["nodes"]) == set(tiny_scene.get_node_ids())
    assert basic["nodes"]["independent"]["group"] == "Ungrouped"
    assert basic["zones"] == {} and basic["attachment"] is None
    assert "UT" not in {heading["name"] for heading in basic["layout"]["headings"]}

    with runtime.InternetMap(tiny_scene, deployment, [physical], tmp_path, zones=zones,
                             extra=partial(display_terminal, tiny_scene, tmp_path)):
        config = json.loads((tmp_path / "map-config.json").read_text())
        nodes = config["nodes"]
        assert set(nodes) == {*tiny_scene.get_node_ids(), "ut"}
        assert nodes["G2"]["container"] in containers
        assert nodes["G2"]["identity_address"] == ""
        assert nodes["G1"]["identity_address"].startswith("198.18.")
        assert nodes["ut"]["container"] == "ut"
        assert config["zones"]["zone"]["prefix"] == physical.targets["destinations"][0]["prefix"]
        assert config["zones"]["zone"]["ground_stations"] == ["G1"]


def test_layout_separates_regions_for_shell_presets_and_multiple_shells():
    import math
    from examples.s4_handover.scene import build_scene
    from tools.internet_map.layout import display_nodes

    for shells, count in [(("S4",), 348), (("S5",), 172), (("S1", "S2", "S3"), 3888)]:
        scene = build_scene(shells)
        nodes, layout = display_nodes(scene)
        satellites = [node for node in nodes.values() if node["role"] == "Satellite"]
        grounds = [node for node in nodes.values() if node["role"] == "GS"]
        assert len(satellites) == count
        assert layout["ut"]["x"] < min(node["x"] for node in satellites) - 200
        assert min(node["x"] for node in grounds) > max(node["x"] for node in satellites) + 200
        assert layout["zones"]["y"] > max(node["y"] for node in nodes.values()) + 200
        assert display_nodes(scene) == (nodes, layout)
        for orbit in scene.get_orbits():
            ring = [node for node in satellites if node["group"] == orbit.group_id]
            for left, right in zip(ring, ring[1:] + ring[:1]):
                assert math.hypot(left["x"] - right["x"], left["y"] - right["y"]) > 40
        if shells == ("S5",):
            assert [orbit.raan_deg for orbit in scene.get_orbits()] == [75.7, 87.7, 99.7, 117.7]
            assert scene.get_satellites()[0].mean_anomaly_deg == .29
