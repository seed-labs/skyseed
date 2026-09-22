"""Generate frozen S5 with three local Models; optionally execute its materials."""
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
import tools
from examples.Sky01_s5_custom_models.model import (
    CurrentGeometryModel, GreedyInterSatelliteModel, GreedyGroundStationModel,
)

ZONE_FILE = EXAMPLE_ROOT / "zones.yaml"


def build_emulator() -> seed.Emulator:
    """Build the editable SEED scene from the prepared S5 and filtered GroundStation records."""
    shells = configuration.emulator.load_shell_configs(SHELLS_FILE)
    stations = configuration.emulator.load_ground_station_configs(GROUND_STATIONS_FILE)
    s5, = (shell for shell in shells if shell.shell_id == "S5")

    new_zealand = tuple(s for s in stations
                        if -48 <= s.latitude_deg <= -34 and 165 <= s.longitude_deg <= 179)
    southern_chile = tuple(s for s in stations
                           if -55 <= s.latitude_deg <= -35 and -76 <= s.longitude_deg <= -68)
    if (len(new_zealand), len(southern_chile)) != (6, 4):
        raise ValueError(f"frozen GS inventory differs: NZ={len(new_zealand)}, Chile={len(southern_chile)}")
    stations = new_zealand + southern_chile

    # The builder preserves stored orbit epochs/phases, payloads and capacities.
    network = emulator.create_static_emulator(
        configuration.emulator.StaticEmulatorConfig(skyseed_autonomous_systems=(
            configuration.emulator.SkyseedAutonomousSystemConfig(
                asn=65040, shells=(s5,), ground_stations=stations),
        )),
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


# Direct Python defaults; CLI options override these experiment choices.
DEFAULT_DURATION = timedelta(seconds=600)
DEFAULT_OPERATION = "serve"
DEFAULT_BATCH_DURATION = timedelta(seconds=300)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    experiment.ExperimentController.add_arguments(parser, default_operation=DEFAULT_OPERATION, batch_duration=DEFAULT_BATCH_DURATION)
    parser.add_argument("--execute", action="store_true", help="also deploy, run and clean up generated materials")
    parser.add_argument("--duration-seconds", type=float, default=DEFAULT_DURATION.total_seconds())
    args = parser.parse_args(argv)

    if args.execute and args.through != "compiler":
        parser.error("--execute requires --through compiler")
    if args.execute:
        args.operation = "generate"
    network = build_emulator()
    systems = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer("Base"))
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    definition = simulator.SimulationDefinition(
        static_input=adapters.create_simulator_static_input(systems),
        timeline=simulator.SimulationTimeline(
            start_time=start,
            end_time=start + timedelta(seconds=args.duration_seconds),
            observation_interval=timedelta(seconds=5),
        ),
    )
    # Edit the actual algorithms in model.py; other roles keep their defaults.
    settings = simulator.DefaultSimulatorConfiguration(
        zone_layout_definition=configuration.simulator.load_zone_layout_definition(ZONE_FILE),
    )
    simulation = simulator.create_default_simulator(
        definition, configuration=settings,
        physical_link_model=CurrentGeometryModel(),
        inter_satellite_model=GreedyInterSatelliteModel(),
        ground_station_model=GreedyGroundStationModel(),
    )

    controller = experiment.ExperimentController(
        network,
        simulation,
        args.output or EXAMPLE_ROOT / "output",
        entry_path=Path(__file__),
        input_files=(SHELLS_FILE, GROUND_STATIONS_FILE, ZONE_FILE, EXAMPLE_ROOT / "model.py"),
        through=args.through, internet_map_enabled=not args.no_internet_map,
        deploy_parallel=args.deploy_parallel, runtime_workers=args.runtime_workers,
        route_workers=args.route_workers,
        replace=args.override, traffic_enabled=not args.no_traffic, traffic_telemetry_enabled=not args.no_traffic_telemetry,
    )
    def present(output, publication=None):
        return tools.write_experiment_tools(output, physical_world=simulation.get_physical_world(),
                                            publication=publication)

    if args.operation == "generate":
        output = controller.generate()
        if not args.no_tools:
            present(output)
        if args.execute:
            experiment.run_generated_experiment(output)
    else:
        controller.serve(delivery_duration=timedelta(seconds=args.batch_seconds),
                         run=args.operation == "run",
                         on_completed=None if args.no_tools else present)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
