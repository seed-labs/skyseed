"""Build the tiny network's deployment files for Docker Compose."""
from pathlib import Path

from seedemu import Docker, Ebgp, Ospf, PeerRelationship, Routing
from skyseed.emulator import SkyBase, SkyEmulator
from skyseed.scene import Scene
from scene import build_scene


def build_emulator(scene):
    emulator = SkyEmulator()
    base = SkyBase()
    routing = Routing()
    ospf = Ospf()
    ospf.maskAsn(150)
    ebgp = Ebgp()

    satellite_as = base.createSkyAutonomousSystem(150)
    satellite_as.loadScene(scene)

    ground_as = base.createAutonomousSystem(151)
    ground_as.createNetwork("access", "10.151.0.0/24")
    edge = ground_as.createRouter("edge")
    edge.joinNetwork("access", "10.151.0.254")
    host = ground_as.createHost("client")
    host.joinNetwork("access", "10.151.0.10")

    base.createInternetExchange(100)
    station = satellite_as.getNode("G1")
    station.joinNetwork("ix100")
    edge.joinNetwork("ix100")
    ebgp.addPrivatePeering(100, 150, 151, PeerRelationship.Peer)

    emulator.addLayer(base)
    emulator.addLayer(routing)
    emulator.addLayer(ospf)
    emulator.addLayer(ebgp)

    for satellite in scene.get_satellites():
        node = emulator.getNode(satellite.node_id)
        node.setFile("/etc/skyseed-scene-id", satellite.node_id + "\n")
        node.appendStartCommand("cat /etc/skyseed-scene-id")
    return emulator


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, help="Read a saved scene instead of building it")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "output")
    parser.add_argument("--description", type=Path, help="Also save the unrendered SEED description")
    args = parser.parse_args()
    scene = build_scene() if args.scene is None else Scene.load(args.scene)
    emulator = build_emulator(scene)
    if args.description is not None:
        emulator.dump(str(args.description))
    emulator.render()
    compiler = Docker(internetMapEnabled=False)
    emulator.compile(compiler, str(args.output))
    print(f"Deployment files written to {args.output.resolve()}")


if __name__ == "__main__":
    main()
