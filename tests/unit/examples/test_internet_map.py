"""Live display must follow observed interfaces and partial adoption receipts."""
from examples.s4_handover.experiment.internet_map.collector import Receipts, connected_links, current_zones, interfaces
from skyseed.runner.netlink import RouteSocket


def test_map_preparation_errors_explain_the_missing_prerequisite(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest
    from examples.s4_handover.experiment.internet_map import build, runtime

    with pytest.raises(FileNotFoundError, match="Install the SEED checkout/revision"):
        build.prepare(tmp_path, source=tmp_path / "missing-seed-tool")
    with pytest.raises(FileNotFoundError, match="python map.py"):
        runtime.InternetMap.check_ready(tmp_path)
    (tmp_path / "observations").mkdir()
    (tmp_path / "observations/map-positions.json").write_text("{}")
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=1, stderr="No such image: skyseed-s4-internet-map"))
    with pytest.raises(RuntimeError, match=r"No such image.*[\s\S]*python map.py"):
        runtime.InternetMap.check_ready(tmp_path)


def test_map_includes_deployed_nodes_absent_from_physical_plans(tiny_scene, tmp_path, monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from unittest.mock import Mock

    import yaml
    from seedemu import Docker, Routing
    from skyseed import SkyBase, SkyEmulator, PlanCompiler
    from skyseed.pc.network import ForwardingRule, LogicalExit, RouteDestination, RoutePlan
    from examples.s4_handover.experiment.internet_map import runtime

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
    repetition = SimpleNamespace(plans=(physical,), start=start, seconds=1,
                                 iterate=lambda **kwargs: iter((physical,)))
    (tmp_path / "terminal.json").write_text(json.dumps({"ut_container": "ut", "ground_ips": {}}))
    (tmp_path / "build.json").write_text(json.dumps({"routing": {"associations": {"zone": ["G1"]}}}))
    ut = {"Id": "ut", "Name": "/ut", "State": {"Pid": 999}}
    check_output = Mock(side_effect=[json.dumps([ut]).encode(), "map-container\n"])
    monkeypatch.setattr(runtime.subprocess, "check_output", check_output)
    monkeypatch.setattr(runtime.subprocess, "run", Mock())

    with runtime.InternetMap(tiny_scene, deployment, repetition, tmp_path, tmp_path):
        config = json.loads((tmp_path / "map-config.json").read_text())
        nodes = config["nodes"]
        assert set(nodes) == {*tiny_scene.get_node_ids(), "ut"}
        assert nodes["G2"]["container"] in containers
        assert nodes["G2"]["identity_address"] == ""
        assert nodes["G1"]["identity_address"].startswith("198.18.")
        assert nodes["ut"]["container"] == "ut"
        assert config["zones"]["zone"]["prefix"] == physical.targets["destinations"][0]["prefix"]
        assert config["zones"]["zone"]["ground_stations"] == ["G1"]


def test_map_javascript():
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import pytest

    node = os.environ.get("SKYSEED_TEST_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for packet text mapping")
    for name in ("test_internet_map_packets.mjs", "test_internet_map_inventory.cjs", "test_internet_map_interaction.mjs"):
        subprocess.run([node, str(Path(__file__).with_name(name))], check=True)


def test_zone_service_waits_for_each_receipt_and_repeats():
    first = "2026-01-01T00:00:00+00:00"
    second = "2026-01-01T00:10:00+00:00"
    entry = {"count": 3, "changes": [[0, "zone", True], [2, "zone", False]]}
    config = {"nodes": {"a": {}}, "start": first, "period": 600,
              "routes": {first + "|a": entry, second + "|a": entry}}
    receipts = Receipts(config)
    assert receipts.zones["a"] == set()
    for scheduled in (first, second, "2026-01-01T00:20:00+00:00"):
        def adopt():
            receipts.apply({"kind": "route", "adoptions": [{"scheduled": scheduled,
                            "nodes": ["a"], "instruction_counts": {"a": 1}}]})
        adopt()
        assert receipts.zones["a"] == {"zone"}
        adopt()
        assert receipts.zones["a"] == {"zone"}
        adopt()
        assert receipts.zones["a"] == set()


def test_links_require_both_live_endpoints_and_follow_ut_handover():
    def interface(name, index, peer):
        return {"name": name, "index": index, "peer_index": peer, "addresses": [], "up": True}
    nodes = {"a": {"interfaces": [interface("ssa", 21, 32), interface("ssut", 42, 51)]},
             "b": {"interfaces": [interface("ssb", 32, 21)]},
             "ut": {"interfaces": [interface("ssut", 51, 42)]}}
    config = {"attachment": {"node": "ut", "interface": "ssut", "peer_interface": "ssut"},
              "links": {"ab": {"link_id": ["a", "b"], "endpoints": [
        {"node_id": "a", "interface": "ssa"}, {"node_id": "b", "interface": "ssb"}]}}}
    assert len(connected_links(config, nodes, "a", {})) == 2
    nodes["a"]["interfaces"].pop()
    assert len(connected_links(config, nodes, "b", {})) == 1
    nodes["b"]["interfaces"].append(interface("ssut", 62, 71))
    nodes["ut"]["interfaces"] = [interface("ssut", 71, 62)]
    links = connected_links(config, nodes, "b", {})
    assert links[-1]["ends"][1]["node"] == "b"
    nodes["b"]["interfaces"][0]["peer_index"] = 99
    assert len(connected_links(config, nodes, "b", {})) == 1


def test_current_namespace_inventory_reads_loopback_without_mutation():
    channel = RouteSocket()
    try:
        loopback, = [item for item in interfaces(channel) if item["name"] == "lo"]
        assert "127.0.0.1/8" in loopback["addresses"]
        assert loopback["index"] == 1
    finally:
        channel.close()


def test_zone_service_requires_receipt_and_live_address_through_handover():
    zone = {"zone": {"prefix": "198.19.0.1/32", "ground_stations": ["gs"]}}
    def satellite(receipts, addresses, running=True):
        return {"zones": receipts, "running": running,
                "interfaces": [{"name": "ssidentity", "addresses": addresses}]}
    nodes = {"old": satellite(["zone"], ["198.19.0.1/32"]),
             "new": satellite([], ["198.19.0.1/32"])}
    assert current_zones(zone, nodes)["zone"]["serving"] == ["old"]
    nodes["new"]["zones"] = ["zone"]
    assert current_zones(zone, nodes)["zone"]["serving"] == ["old", "new"]
    nodes["old"]["interfaces"][0]["addresses"] = []
    assert current_zones(zone, nodes)["zone"]["serving"] == ["new"]
    nodes["new"]["running"] = False
    assert current_zones(zone, nodes)["zone"]["serving"] == []
    assert zone["zone"] == {"prefix": "198.19.0.1/32", "ground_stations": ["gs"]}


def test_layout_separates_regions_for_shell_presets_and_multiple_shells():
    import math
    from examples.s4_handover.scene import build_scene
    from examples.s4_handover.experiment.internet_map.layout import display_nodes

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
