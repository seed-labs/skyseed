"""Generate five isolated Shell scopes sharing one physical GS inventory."""
import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
SHELLS_FILE = PROJECT_ROOT / "data/starlink/shells.yaml"
GROUND_STATIONS_FILE = PROJECT_ROOT / "data/starlink/ground_stations.yaml"
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
sys.path.insert(0, str(PROJECT_ROOT))

from skyseed_v2 import configuration, emulator, adapters, experiment, simulator
import tools

SHELLS = ("S1", "S2", "S3", "S4", "S5")
# Allocation policy: two slots per Shell. Physical GS capacity is ten for this scene.
GROUND_STATION_SLOTS_PER_SHELL = 2
GROUND_STATION_TOTAL_CAPACITY = len(SHELLS) * GROUND_STATION_SLOTS_PER_SHELL
# Simulation clock origin; source orbit epochs/phases are not regenerated.
REFERENCE_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
OBSERVATION_INTERVAL = timedelta(seconds=5)
DEFAULT_DURATION = timedelta(seconds=600)
# Experiment delivery length, independent of physical capacities and Model quotas.
DELIVERED_COVERAGE = timedelta(seconds=300)


def build_emulator():
    """Build all five prepared Shells and one shared physical GroundStation inventory."""
    # Shared 4408-satellite inventory and 355 physical GSs.
    # The SEED boundary supplies management. Only GS capability differs here.
    shells = configuration.emulator.load_shell_configs(SHELLS_FILE)
    ground_stations = configuration.emulator.load_ground_station_configs(GROUND_STATIONS_FILE)
    if tuple(shell.shell_id for shell in shells) != SHELLS:
        raise ValueError("five-shell example requires S1–S5")
    # Emu device capability: ten simultaneous accesses at each shared GS.
    # Simulator scope quotas below independently govern who may reserve those ports.
    stations = tuple(
        replace(station, max_simultaneous_satellite_accesses=GROUND_STATION_TOTAL_CAPACITY)
        for station in ground_stations
    )
    network = emulator.create_static_emulator(
        configuration.emulator.StaticEmulatorConfig(skyseed_autonomous_systems=(
            configuration.emulator.SkyseedAutonomousSystemConfig(
                asn=65040, shells=shells, ground_stations=stations),
        ))
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


def create_simulator(network, *, duration=DEFAULT_DURATION):
    systems = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer("Base"))
    static_input = adapters.create_simulator_static_input(systems)
    # Assign every satellite to its Shell's network scope in the GS, Zone and Route
    # Models. Each Zone gets a service per scope; Routes cannot cross those scopes.
    scopes = {sat.node_id: sat.shell_id for sat in static_input.list_satellites()}
    settings = simulator.DefaultSimulatorConfiguration(
        default_ground_station_limit=GROUND_STATION_TOTAL_CAPACITY,
        satellite_scopes=scopes,
        # Two preparing-or-operational GS slots per Shell, with no borrowing.
        ground_station_scope_limits={
            shell: GROUND_STATION_SLOTS_PER_SHELL for shell in SHELLS
        },
    )
    definition = simulator.SimulationDefinition(
        static_input=static_input,
        timeline=simulator.SimulationTimeline(
            start_time=REFERENCE_EPOCH,
            end_time=REFERENCE_EPOCH + duration,
            observation_interval=OBSERVATION_INTERVAL,
        ),
    )
    # Keep the default 732 Zones, within-Shell ISLs, admission, parameters and
    # three-gateway routing. Scope membership changes allocation, not geometry.
    return simulator.create_default_simulator(definition, configuration=settings)


# Direct Python defaults; CLI options override these experiment choices.
DEFAULT_OPERATION = "serve"
DEFAULT_BATCH_DURATION = timedelta(seconds=300)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    experiment.ExperimentController.add_arguments(parser, default_operation=DEFAULT_OPERATION, batch_duration=DEFAULT_BATCH_DURATION)
    parser.add_argument(
        "--duration-seconds", type=float, default=DEFAULT_DURATION.total_seconds(),
        help="600 normally; short offline validation may bound coverage",
    )
    args = parser.parse_args(argv)

    network = build_emulator()
    simulation = create_simulator(network, duration=timedelta(seconds=args.duration_seconds))
    timeline = simulation.get_timeline()
    # Experiment schedule: seal [0,300), then [300,600] during generation.
    # The first endpoint is 295 s at this cadence; there is one B0 across the seam.
    delivery_ends = None
    if timeline.end_time > REFERENCE_EPOCH + DELIVERED_COVERAGE:
        delivery_ends = (
            REFERENCE_EPOCH + DELIVERED_COVERAGE - OBSERVATION_INTERVAL,
            timeline.end_time,
        )
    # This command generates both deliveries and Tools; controls execute them later.
    controller = experiment.ExperimentController(
        network,
        simulation,
        args.output or DEFAULT_OUTPUT,
        entry_path=Path(__file__),
        input_files=(SHELLS_FILE, GROUND_STATIONS_FILE,),
        scenario_parameters={
            "shells": SHELLS,
            "ground_station_slots_per_shell": GROUND_STATION_SLOTS_PER_SHELL,
            "ground_station_total_capacity": GROUND_STATION_TOTAL_CAPACITY,
            "duration_seconds": args.duration_seconds,
        },
        through=args.through, traffic_enabled=not args.no_traffic, traffic_telemetry_enabled=not args.no_traffic_telemetry,
        replace=args.override,
        internet_map_enabled=not args.no_internet_map,
        delivery_ends=delivery_ends,
        # Runner controls: start preparing the successor at Main's start, and
        # reserve a fixed envelope with factor two from the first batch's demand.
        batch_lead_fraction=1.0,
        deploy_parallel=args.deploy_parallel,
        runtime_workers=args.runtime_workers,
        route_workers=args.route_workers,
    )
    def present(output, publication=None):
        return tools.write_experiment_tools(output, physical_world=simulation.get_physical_world(),
                                            publication=publication)

    if args.operation == "generate":
        output = controller.generate()
        if not args.no_tools:
            present(output)
    else:
        controller.serve(delivery_duration=timedelta(seconds=args.batch_seconds),
                         run=args.operation == "run",
                         on_completed=None if args.no_tools else present)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
