"""Generate the small opt-in live validation scene, without running Docker."""
import argparse
from pathlib import Path
from skyseed import adapters, emulator
from skyseed.experiment import ExperimentController
from skyseed.backend.linux.environment import NativeRoutingEnvironment
from tests.fixtures.native_config import FixtureConfigRenderer
from tests.fixtures.native_satellite_edges import EdgeRoutes
from tests.fixtures.native_ospf import build_small, create_small_simulator, A, C


def generate(output, daemon):
    network = build_small()
    system, = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer('Base'))
    static = adapters.create_simulator_static_input((system,))
    environment = NativeRoutingEnvironment(n.node_id for n in static.list_nodes())
    policy = FixtureConfigRenderer(daemon)
    for satellite in static.list_satellites():
        emulator.configure_native_software(system.get_satellite(satellite.satellite_id),
            daemon=daemon, configuration=policy(satellite.node_id, environment))
    simulation = create_small_simulator(static, EdgeRoutes((A, C)), duration=390, service=True)
    return ExperimentController(network, simulation, output, entry_path=Path(__file__),
        forwarding_backend='linux', traffic_enabled=False, internet_map_enabled=False,
        deploy_parallel=2, runtime_workers=2,
        scenario_parameters={'daemon': daemon,
            'stable_addresses': {n.value: a for n, a in environment.node_addresses.items()}}).generate()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--daemon', required=True, choices=('frr', 'bird'))
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    generate(args.output, args.daemon)
