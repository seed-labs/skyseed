"""Build SEED artifacts and reusable Docker images; do not start containers."""
from pathlib import Path

from seedemu import Docker, Routing
from skyseed import Scene, SkyBase, SkyEmulator
from tools.deployment.build_images import build_images


if __name__ == "__main__":
    output = Path(__file__).parent / "output"
    scene = Scene.load(output / "scene/scene.scene")
    seed = output / "seed"
    seed.mkdir(parents=True, exist_ok=True)

    emulator = SkyEmulator()
    base = SkyBase()
    satellite_as = base.createSkyAutonomousSystem(150)
    satellite_as.loadScene(scene)

    # Static GS access interfaces belong to SEED. Dynamic satellite links
    # are created later by Runner from the compiled link plan.
    for index, station in enumerate(scene.get_ground_stations()):
        network = f"access{index}"
        satellite_as.createNetwork(network, f"10.150.{index}.0/24")
        satellite_as.getNode(station.node_id).joinNetwork(network, f"10.150.{index}.254")
    emulator.addLayer(base)
    emulator.addLayer(Routing())

    emulator.dump(str(seed / "emulator.bin"))
    emulator.render()
    emulator.compile(Docker(internetMapEnabled=False), str(seed / "deployment"))
    # Repository Tool: build reusable Docker images from SEED output.
    build_images(seed / "deployment")
    print(f"Reusable emulator, Compose and images ready: {seed}")
