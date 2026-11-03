from dataclasses import asdict
import pytest
from seedemu.core import AutonomousSystem, ExtensionNode, Router
from skyseed.emulator import SkyAS, SkyBase, SkyEmulator


def test_standard_and_sky_factories_share_one_base_and_reject_asn_collision():
    base = SkyBase()
    ordinary = base.createAutonomousSystem(151)
    satellite_as = base.createSkyAutonomousSystem(150)
    assert type(ordinary) is AutonomousSystem and isinstance(satellite_as, SkyAS)
    assert base.getAsns() == [151, 150]
    with pytest.raises(ValueError):
        base.createSkyAutonomousSystem(151)
    with pytest.raises(AssertionError):
        base.createAutonomousSystem(150)
    assert base.getAutonomousSystem(150) is satellite_as


def test_load_preserves_scene_and_uses_native_router_inventory(tiny_scene):
    original = [asdict(node) for node in tiny_scene.nodes]
    base = SkyBase()
    autonomous_system = base.createSkyAutonomousSystem(150)
    autonomous_system.loadScene(tiny_scene)
    station = autonomous_system.getNode("G1")
    satellite = autonomous_system.getNode(tiny_scene.nodes[0].node_id)
    assert isinstance(station, Router) and isinstance(satellite, ExtensionNode)
    assert autonomous_system.getRouter(station.getName()) is station
    assert station.getName() in autonomous_system.getIbgpParticipants()
    assert autonomous_system.getExtensionNode(satellite.getName()) is satellite
    assert [asdict(node) for node in tiny_scene.nodes] == original


def test_selection_and_conflicts_fail_before_adding_nodes(tiny_scene):
    autonomous_system = SkyAS(150)
    autonomous_system.loadScene(tiny_scene, node_ids=["G1"])
    before = autonomous_system.getSceneNodes()
    for identities in ([tiny_scene.nodes[0].node_id, "G1"], ["absent"], ["G1", "G1"]):
        with pytest.raises((ValueError, KeyError)):
            autonomous_system.loadScene(tiny_scene, node_ids=identities)
        assert autonomous_system.getSceneNodes() == before
    with pytest.raises(KeyError):
        autonomous_system.getNode("unknown")


@pytest.mark.parametrize("edit", ["data", "position"])
def test_edited_scene_input_fails_before_seed_construction(tiny_scene, edit):
    if edit == "data":
        tiny_scene.get_node("G1").data["antenna"] = object()
        expected = "node 'G1'.data['antenna']"
    else:
        satellite = tiny_scene.create_satellite("independent", position=tiny_scene.get_satellites()[0].position)
        satellite.position_source.data["altitude_m"] = -1
        expected = "satellite 'independent': altitude_m"
    autonomous_system = SkyAS(150)
    with pytest.raises(ValueError) as failure:
        autonomous_system.loadScene(tiny_scene)
    assert expected in str(failure.value)
    assert autonomous_system.getSceneNodes() == {}
    assert autonomous_system.getRouters() == []
    assert autonomous_system.getExtensionNodes() == []


def test_one_scene_can_be_partitioned_across_as_without_rewriting_identity(tiny_scene):
    emulator = SkyEmulator()
    base = SkyBase()
    satellites = base.createSkyAutonomousSystem(150)
    ground = base.createSkyAutonomousSystem(151)
    satellites.loadScene(tiny_scene, node_ids=[node.node_id for node in tiny_scene.nodes if node.kind == "satellite"])
    ground.loadScene(tiny_scene, node_ids=["G1"])
    emulator.addLayer(base)
    assert emulator.getNode("G1") is ground.getNode("G1")
    assert set(emulator.getSceneNodes()) == {node.node_id for node in tiny_scene.nodes}


def test_cross_as_ambiguity_is_rejected_before_render(tiny_scene):
    emulator = SkyEmulator()
    base = SkyBase()
    for asn in (150, 151):
        base.createSkyAutonomousSystem(asn).loadScene(tiny_scene, node_ids=["G1"])
    emulator.addLayer(base)
    with pytest.raises(ValueError, match="multiple ASs"):
        emulator.render()
    assert not emulator.getRegistry().has("150", "rnode", base.getAutonomousSystem(150).getNode("G1").getName())


@pytest.mark.parametrize("label", ["skyseed.scene_id", "skyseed.scene_kind"])
def test_changed_reserved_metadata_cannot_be_saved_as_valid_description(tiny_scene, tmp_path, label):
    emulator = SkyEmulator()
    base = SkyBase()
    autonomous_system = base.createSkyAutonomousSystem(150)
    autonomous_system.loadScene(tiny_scene)
    emulator.addLayer(base)
    emulator.getNode("G1").setLabel(label, "wrong")
    with pytest.raises(ValueError, match="reserved Scene"):
        emulator.dump(str(tmp_path / "bad.bin"))
    assert not (tmp_path / "bad.bin").exists()
