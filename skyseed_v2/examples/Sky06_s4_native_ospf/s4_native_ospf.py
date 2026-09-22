"""Native S4: BIRD OSPF area 0, with explicit GS edge routes."""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
SHELLS_FILE = PROJECT_ROOT / "data/starlink/shells.yaml"
GROUND_STATIONS_FILE = PROJECT_ROOT / "data/starlink/ground_stations.yaml"
sys.path.insert(0, str(PROJECT_ROOT))
import seedemu as seed
from skyseed_v2 import configuration, emulator, adapters, experiment, simulator
from skyseed_v2.backend.linux.environment import NativeRoutingEnvironment
import tools
from examples.Sky06_s4_native_ospf.edge_routes import EdgeRoutes
from examples.Sky06_s4_native_ospf.config_helpers import BirdConfigRenderer, CONFIG_DIRECTORY, describe_node_identities
from examples.Sky06_s4_native_ospf.readiness import prepare


def build_emulator() -> seed.Emulator:
    """Build the editable SEED scene from the prepared satellite and GroundStation records."""
    shells = configuration.emulator.load_shell_configs(SHELLS_FILE)
    stations = configuration.emulator.load_ground_station_configs(GROUND_STATIONS_FILE)
    selected, = (shell for shell in shells if shell.shell_id == "S4")

    # The builder preserves stored orbit epochs/phases, payloads and capacities.
    network = emulator.create_static_emulator(
        configuration.emulator.StaticEmulatorConfig(skyseed_autonomous_systems=(
            configuration.emulator.SkyseedAutonomousSystemConfig(
                asn=65040, shells=(selected,), ground_stations=stations),
        )), forwarding_backend=FORWARDING_BACKEND,
    )
    satellite_as, = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer("Base"))

    # Ordinary SEED business LAN; synthetic addresses are separate from node /32s,
    # Zones, management and all native dynamic-link pools. No satellite routes.
    ground_lan = satellite_as.createNetwork("ground-lan", "203.0.0.0/16")
    # Leave the first host address for the Docker bridge gateway.
    for index, station in enumerate(sorted(satellite_as.ground_stations(),
                                         key=lambda value: value.ground_station_id), start=2):
        station.joinNetwork(ground_lan.getName(), str(ground_lan.getPrefix()[index]))

    return network


# Ordinary author choices; CLI overrides duration for a fresh generation.
FORWARDING_BACKEND = "linux"
DEFAULT_DURATION = timedelta(seconds=600)
CADENCE = timedelta(seconds=5)
DEFAULT_OPERATION = "serve"
DEFAULT_BATCH_DURATION = timedelta(seconds=300)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    experiment.ExperimentController.add_arguments(
        parser, default_operation=DEFAULT_OPERATION, batch_duration=DEFAULT_BATCH_DURATION)
    parser.add_argument("--duration-seconds", type=float, default=DEFAULT_DURATION.total_seconds())
    args = parser.parse_args(argv)

    network = build_emulator()
    system, = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer("Base"))
    static_input = adapters.create_simulator_static_input((system,))
    environment = NativeRoutingEnvironment(n.node_id for n in static_input.list_nodes())
    describe_node_identities(system, static_input, environment)
    policy = BirdConfigRenderer(CONFIG_DIRECTORY)
    for satellite in static_input.list_satellites():
        emulator.configure_native_software(system.get_satellite(satellite.satellite_id),
            daemon="bird", configuration=policy(satellite.node_id, environment))
    addresses = {node.value: address for node, address in environment.node_addresses.items()}
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    definition = simulator.SimulationDefinition(
        static_input=static_input,
        timeline=simulator.SimulationTimeline(
            start, start + timedelta(seconds=args.duration_seconds), CADENCE),
    )
    # Replace this ordinary Model to change edge policy; OSPF supplies transit.
    simulation = simulator.create_default_simulator(definition, forwarding_backend=FORWARDING_BACKEND, route_model=EdgeRoutes())
    controller = experiment.ExperimentController(network, simulation,
        args.output or EXAMPLE_ROOT / 'output', entry_path=Path(__file__),
        traffic_enabled=True,
        preparation=prepare,
        traffic_options={"maximum_persistent_probes": 8, "maximum_activation_window_probes": 0,
                         "persistent_interval_seconds": 5.0, "maximum_concurrent_probes": 8,
                         "maximum_echo_requests": 1500},
        input_files=(SHELLS_FILE, GROUND_STATIONS_FILE, EXAMPLE_ROOT / 'edge_routes.py',
                     EXAMPLE_ROOT / 'config_helpers.py', EXAMPLE_ROOT / 'readiness.py', *sorted(CONFIG_DIRECTORY.glob('*.template'))),
        replace=args.override, through=args.through, internet_map_enabled=not args.no_internet_map,
        deploy_parallel=args.deploy_parallel, runtime_workers=args.runtime_workers,
        route_workers=args.route_workers,
        scenario_parameters={'shell': 'S4', 'daemon': 'bird',
                             'node_labels': {station.node_id.value: system.get_ground_station(station.ground_station_id).getDisplayName()
                                             for station in static_input.list_ground_stations()},
                             'stable_addresses': addresses})
    def present(output, publication=None):
        return tools.write_experiment_tools(output, physical_world=simulation.get_physical_world(), publication=publication)
    if args.operation == 'generate':
        output = controller.generate()
        if not args.no_tools:
            present(output)
    else:
        controller.serve(delivery_duration=timedelta(seconds=args.batch_seconds),
                         run=args.operation == 'run', on_completed=None if args.no_tools else present)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
