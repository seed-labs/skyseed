"""Generate Starlink S4 (348 satellites), or explicit S3/S1, from shared inputs."""
import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
DATA_ROOT = PROJECT_ROOT / "data/starlink"
SHELLS_FILE = DATA_ROOT / "shells.yaml"
GROUND_STATIONS_FILE = DATA_ROOT / "ground_stations.yaml"
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
sys.path.insert(0, str(PROJECT_ROOT))

from skyseed import configuration, emulator, adapters, experiment, simulator
import tools

# Simulation clock origin; prepared orbit epochs remain unchanged in shared data.
REFERENCE_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
DEFAULT_OBSERVATION_INTERVAL = timedelta(seconds=5)
DEFAULT_DURATION = timedelta(seconds=600)


def build_emulator(shell="S4"):
    """Select S4/S3/S1 from shared prepared inputs and construct that SEED scene."""
    # Preserve explicit orbit epochs/phases and the same 355-GS inventory.
    # The ordinary business LAN below is separate from SEED management.
    shells = configuration.emulator.load_shell_configs(SHELLS_FILE)
    ground_stations = configuration.emulator.load_ground_station_configs(GROUND_STATIONS_FILE)
    selected = tuple(value for value in shells if value.shell_id == shell)
    if len(selected) != 1:
        raise ValueError(f"unknown Starlink Shell: {shell}")
    network = emulator.create_static_emulator(
        configuration.emulator.StaticEmulatorConfig(skyseed_autonomous_systems=(
            configuration.emulator.SkyseedAutonomousSystemConfig(
                asn=65040, shells=selected, ground_stations=ground_stations),
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


def create_simulator(
    network,
    *,
    observation_interval=DEFAULT_OBSERVATION_INTERVAL,
    duration=DEFAULT_DURATION,
    configuration=None,
):
    # As in default S4, adapt SEED entities externally and declare finite time here.
    systems = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer("Base"))
    definition = simulator.SimulationDefinition(
        static_input=adapters.create_simulator_static_input(systems),
        timeline=simulator.SimulationTimeline(
            start_time=REFERENCE_EPOCH,
            end_time=REFERENCE_EPOCH + duration,
            observation_interval=observation_interval,
        ),
    )
    # Only requested configuration changes are supplied. Omitted Model roles keep
    # the complete default composition; a per-role keyword here can inject a Model.
    return simulator.create_default_simulator(definition, configuration=configuration)


# Direct Python defaults; CLI options override these experiment choices.
DEFAULT_OPERATION = "serve"
DEFAULT_BATCH_DURATION = timedelta(seconds=300)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    experiment.ExperimentController.add_arguments(parser, default_operation=DEFAULT_OPERATION, batch_duration=DEFAULT_BATCH_DURATION)
    parser.add_argument("--shell", choices=("S4", "S3", "S1"), default="S4")
    parser.add_argument("--observation-interval-seconds", type=float, default=5)
    parser.add_argument("--duration-seconds", type=float, default=DEFAULT_DURATION.total_seconds())
    parser.add_argument("--batch-lead-fraction", type=float, default=1.0,
                        help="successor preparation lead as a fraction of its duration")
    parser.add_argument("--batch-capacity-factor", type=int, default=2,
                        help="fixed Runner reservation factor from the first batch")
    zones = parser.add_mutually_exclusive_group()
    zones.add_argument("--zones", type=Path, help="Zone YAML; omission generates the global grid")
    zones.add_argument("--no-zones", action="store_true", help="explicitly disable Zones")
    args = parser.parse_args(argv)

    # Choose Simulator geography: generated global grid, authored file, or disabled.
    if args.no_zones:
        settings = simulator.DefaultSimulatorConfiguration(zone_layout_definition=None)
    elif args.zones:
        settings = simulator.DefaultSimulatorConfiguration(
            zone_layout_definition=configuration.simulator.load_zone_layout_definition(args.zones)
        )
    else:
        settings = simulator.DefaultSimulatorConfiguration()
    # Optional parameter edit for a built-in Model (not enabled in this example):
    # settings = replace(settings, gateway_candidate_count=1)
    # Supplying route_model=... at the factory is a separate Model-role choice;
    # Tiny demonstrates that with a configured instance of the existing algorithm.

    network = build_emulator(args.shell)
    simulation = create_simulator(
        network,
        configuration=settings,
        observation_interval=timedelta(seconds=args.observation_interval_seconds),
        duration=timedelta(seconds=args.duration_seconds),
    )
    # Experiment controls: material cutoff/output, deployment, runtime preparation
    # and generation route workers. Generation is the default operation.
    controller = experiment.ExperimentController(
        network,
        simulation,
        args.output or DEFAULT_OUTPUT,
        entry_path=Path(__file__),
        input_files=(SHELLS_FILE, GROUND_STATIONS_FILE, args.zones) if args.zones else (SHELLS_FILE, GROUND_STATIONS_FILE),
        scenario_parameters={
            "shell": args.shell,
            "duration_seconds": args.duration_seconds,
            "zones": "disabled" if args.no_zones else (
                str(args.zones) if args.zones else "generated-global-24x48"
            ),
        },
        through=args.through, traffic_enabled=not args.no_traffic, traffic_telemetry_enabled=not args.no_traffic_telemetry,
        replace=args.override,
        internet_map_enabled=not args.no_internet_map,
        deploy_parallel=args.deploy_parallel,
        runtime_workers=args.runtime_workers,
        route_workers=args.route_workers,
        batch_lead_fraction=args.batch_lead_fraction,
        batch_capacity_factor=args.batch_capacity_factor,
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
