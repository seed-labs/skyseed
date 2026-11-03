from pathlib import Path
from datetime import datetime, timezone
import subprocess
import sys
import shlex

import pytest
import yaml
from seedemu import Base, Binding, Docker, Emulator, Filter, Ibgp, Ospf, Routing, WebService
from seedemu.mergers import DefaultBaseMerger
from skyseed.emulator import SkyBase, SkyEmulator, configure_native_software
from skyseed.scene import Scene

ROOT = Path(__file__).resolve().parents[2]


def assembly(scene, *, node_ids=None):
    emulator = SkyEmulator()
    base = SkyBase()
    autonomous_system = base.createSkyAutonomousSystem(150)
    autonomous_system.loadScene(scene, node_ids=node_ids)
    emulator.addLayer(base)
    emulator.addLayer(Routing())
    return emulator, base, autonomous_system


def test_scene_save_seed_dump_render_and_compile_preserve_identity_and_software(tiny_scene, tmp_path):
    scene_path = tmp_path / "input.scene"
    tiny_scene.dump(scene_path)
    restored_scene = Scene.load(scene_path)
    emulator, base, autonomous_system = assembly(restored_scene)
    satellite_id = restored_scene.nodes[0].node_id
    satellite = emulator.getNode(satellite_id)
    satellite.setFile("/etc/research.conf", "example=1\n")
    satellite.appendStartCommand("cat /etc/research.conf")
    autonomous_system.createNetwork("lan", "10.150.0.0/24")
    station = emulator.getNode("G1")
    station.joinNetwork("lan", "10.150.0.1")
    host = autonomous_system.createHost("client")
    host.joinNetwork("lan", "10.150.0.10")
    description = tmp_path / "construction.bin"
    emulator.dump(str(description))
    loaded = SkyEmulator()
    loaded.load(str(description))
    loaded.render()
    loaded.compile(Docker(internetMapEnabled=False), str(tmp_path / "deployment"))
    compose = yaml.safe_load((tmp_path / "deployment/docker-compose.yml").read_text())
    services = {
        service["labels"]["org.seedsecuritylabs.seedemu.meta.skyseed.scene_id"]: service
        for service in compose["services"].values()
        if "org.seedsecuritylabs.seedemu.meta.skyseed.scene_id" in service.get("labels", {})
    }
    assert set(services) == {node.node_id for node in tiny_scene.nodes}
    assert services[satellite_id]["network_mode"] == "none"
    assert services["G1"]["networks"]["net_150_lan"]["ipv4_address"] == "10.150.0.1"
    files = dict(file.get() for file in loaded.getNode(satellite_id).getFiles())
    assert files["/etc/research.conf"] == "example=1\n"
    build_path = tmp_path / "deployment" / services[satellite_id]["build"]
    copies = [shlex.split(line) for line in (build_path / "Dockerfile").read_text().splitlines()
              if line.startswith("COPY ")]
    source_file = next(parts[1] for parts in copies if parts[-1] == "/etc/research.conf")
    # SEED's text-file writer appends a newline to the supplied content.
    assert (build_path / source_file).read_text().rstrip("\n") == "example=1"
    assert loaded.getNode("G1") is loaded.getLayer("Base").getAutonomousSystem(150).getRouter(station.getName())
    with pytest.raises(RuntimeError, match="after SEED"):
        loaded.getLayer("Base").getAutonomousSystem(150).loadScene(tiny_scene)


def test_gs_really_participates_in_seed_ibgp(tiny_scene):
    emulator, base, autonomous_system = assembly(tiny_scene)
    autonomous_system.createNetwork("lan", "10.150.0.0/24")
    station = emulator.getNode("G1")
    station.joinNetwork("lan", "10.150.0.1")
    router = autonomous_system.createRouter("peer")
    router.joinNetwork("lan", "10.150.0.2")
    emulator.addLayer(Ospf())
    emulator.addLayer(Ibgp())
    emulator.render()
    assert autonomous_system.getIbgpParticipants() == {station.getName(), "peer"}
    for node in (station, router):
        files = dict(file.get() for file in node.getFiles())
        assert "protocol bgp" in files["/etc/bird/bird.conf"]
    assert emulator.getRegistry().get("150", "rnode", station.getName()) is station


def test_saved_scene_groups_partition_into_real_as_deployments(tmp_path):
    scene = Scene()
    for name in ("alpha", "beta"):
        scene.create_constellation(name, planes=1, satellites_per_plane=1,
                                   altitude_km=550, inclination_deg=53,
                                   epoch=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                   data={"operator": name})
        scene.create_ground_station_group(f"{name}-ground", data={"operator": name})
        scene.create_ground_station(f"{name}-gs", latitude_deg=40, longitude_deg=116,
                                    group=f"{name}-ground")
    scene.create_ground_station("unselected", latitude_deg=0, longitude_deg=0)
    path = tmp_path / "shared.scene"
    scene.dump(path)
    original = path.read_bytes()
    restored = Scene.load(path)
    emulator = SkyEmulator()
    base = SkyBase()
    expected = {
        "alpha/shell-0/plane-0/sat-0": 150, "alpha-gs": 150,
        "beta/shell-0/plane-0/sat-0": 151, "beta-gs": 151,
    }
    for name, asn in (("alpha", 150), ("beta", 151)):
        autonomous_system = base.createSkyAutonomousSystem(asn)
        # Selecting a constellation and its shell must not duplicate satellites.
        ids = restored.get_node_ids(groups=[name, f"{name}/shell-0", f"{name}-ground"])
        autonomous_system.loadScene(restored, node_ids=ids)
        autonomous_system.createNetwork("lan", f"10.{asn}.0.0/24")
        autonomous_system.getNode(f"{name}-gs").joinNetwork("lan", f"10.{asn}.0.1")
    emulator.addLayer(base)
    emulator.addLayer(Routing())
    emulator.dump(str(tmp_path / "construction.bin"))
    loaded = SkyEmulator()
    loaded.load(str(tmp_path / "construction.bin"))
    assert {identity: node.getAsn() for identity, node in loaded.getSceneNodes().items()} == expected
    loaded.render()
    loaded.compile(Docker(internetMapEnabled=False), str(tmp_path / "deployment"))
    compose = yaml.safe_load((tmp_path / "deployment/docker-compose.yml").read_text())
    scene_label = "org.seedsecuritylabs.seedemu.meta.skyseed.scene_id"
    services = {service["labels"][scene_label]: service
                for service in compose["services"].values()
                if scene_label in service.get("labels", {})}
    assert set(services) == set(expected)
    for name, asn in (("alpha", 150), ("beta", 151)):
        assert set(services[f"{name}-gs"]["networks"]) == {f"net_{asn}_lan"}
        assert services[f"{name}/shell-0/plane-0/sat-0"]["network_mode"] == "none"
    restored.dump(path)
    assert path.read_bytes() == original


def test_separate_scene_files_can_supply_one_as_without_merging_scene_ownership(tmp_path):
    base = SkyBase()
    autonomous_system = base.createSkyAutonomousSystem(150)
    inputs = []
    for identity in ("G1", "G2"):
        scene = Scene(data={"author": identity})
        scene.create_ground_station_group("local-group")
        scene.create_ground_station(identity, latitude_deg=40, longitude_deg=116,
                                    group="local-group")
        path = tmp_path / f"{identity}.scene"
        scene.dump(path)
        restored = Scene.load(path)
        autonomous_system.loadScene(restored)
        inputs.append(restored)
    assert set(autonomous_system.getSceneNodes()) == {"G1", "G2"}
    assert [scene.get_node_ids() for scene in inputs] == [["G1"], ["G2"]]
    assert inputs[0].get_group("local-group") is not inputs[1].get_group("local-group")
    autonomous_system.getNode("G1").setFile("/etc/local", "SEED construction edit")
    assert inputs[0].get_node("G1").data == {}


def test_load_existing_seed_description_and_merge_keep_ground_nodes(tiny_scene, tmp_path):
    ground = Emulator()
    ground_base = Base()
    ground_as = ground_base.createAutonomousSystem(151)
    host = ground_as.createHost("existing")
    host.setFile("/etc/existing", "keep")
    ground.addLayer(ground_base)
    path = tmp_path / "ground.bin"
    ground.dump(str(path))
    restored = SkyEmulator()
    restored.load(str(path))
    sky = SkyEmulator()
    sky_base = SkyBase()
    sky_as = sky_base.createSkyAutonomousSystem(150)
    sky_as.loadScene(tiny_scene)
    sky.addLayer(sky_base)
    merged = restored.merge(sky, [DefaultBaseMerger()])
    assert isinstance(merged, SkyEmulator)
    assert set(merged.getLayer("Base").getAsns()) == {150, 151}
    assert merged.getNode("G1") is sky_as.getNode("G1")
    existing = merged.getLayer("Base").getAutonomousSystem(151).getHost("existing")
    assert dict(file.get() for file in existing.getFiles())["/etc/existing"] == "keep"
    merged.render()


def test_service_only_description_can_be_saved_merged_and_bound(tiny_scene, tmp_path):
    service = WebService()
    service.install("site")
    software = SkyEmulator()
    software.addLayer(service)
    virtual_node = software.getVirtualNode("site")
    virtual_node.setFile("/etc/author-marker", "software component")
    software.addBinding(Binding("site", filter=Filter(asn=150, nodeName="client")))
    assert software.getSceneNodes() == {}
    with pytest.raises(KeyError):
        software.getNode("G1")
    software.dump(str(tmp_path / "software.bin"))
    restored = SkyEmulator()
    restored.load(str(tmp_path / "software.bin"))

    network, base, autonomous_system = assembly(tiny_scene)
    autonomous_system.createNetwork("lan", "10.150.0.0/24")
    autonomous_system.getNode("G1").joinNetwork("lan", "10.150.0.1")
    client = autonomous_system.createHost("client")
    client.joinNetwork("lan", "10.150.0.10")
    merged = network.merge(restored)
    merged.render()
    assert "nginx-light" in client.getSoftware()
    assert dict(file.get() for file in client.getFiles())["/etc/author-marker"] == "software component"
    assert merged.getNode("G1") is autonomous_system.getNode("G1")


@pytest.mark.parametrize("daemon,configuration,config_path", [
    ("bird", "router id 192.0.2.1;", "/etc/bird/bird.conf"),
    ("frr", {"zebra": "hostname satellite", "ospfd": "router ospf"}, "/etc/frr/ospfd.conf"),
])
def test_native_software_adapter_compiles_on_real_satellite(tiny_scene, tmp_path, daemon, configuration, config_path):
    emulator, base, autonomous_system = assembly(tiny_scene, node_ids=[tiny_scene.nodes[0].node_id])
    node = emulator.getNode(tiny_scene.nodes[0].node_id)
    configure_native_software(node, daemon=daemon, configuration=configuration)
    emulator.render()
    emulator.compile(Docker(internetMapEnabled=False), str(tmp_path / daemon))
    files = dict(file.get() for file in node.getFiles())
    assert config_path in files
    assert ("bird2" if daemon == "bird" else "frr") in node.getSoftware()
    with pytest.raises(ValueError, match="already prepared"):
        configure_native_software(node, daemon=daemon, configuration=configuration)


def test_public_example_in_fresh_process_reads_scene_and_compiles_without_runtime(tmp_path):
    example = ROOT / "new_examples/tiny_network"
    for args in ([str(example / "scene.py"), str(tmp_path / "tiny.scene")],
                 [str(example / "emulator.py"), "--scene", str(tmp_path / "tiny.scene"),
                  "--output", str(tmp_path / "deployment"), "--description", str(tmp_path / "emu.bin")]):
        result = subprocess.run([sys.executable, *args], cwd=tmp_path, text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
    compose = yaml.safe_load((tmp_path / "deployment/docker-compose.yml").read_text())
    labels = [service.get("labels", {}) for service in compose["services"].values()]
    nodes = [item for item in labels if "org.seedsecuritylabs.seedemu.meta.nodename" in item]
    assert len(nodes) == 6  # Exclude SEED's auxiliary image-build services.
    assert sum("org.seedsecuritylabs.seedemu.meta.skyseed.scene_id" in item for item in labels) == 3
    # Docker reserves the subnet's .1 address for its bridge gateway.
    edge_address = compose["services"]["brdnode_151_edge"]["networks"]["net_151_access"]["ipv4_address"]
    assert edge_address != "10.151.0.1"
