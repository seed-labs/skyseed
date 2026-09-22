"""Generate the tiny satellite network, ordinary AS, and offline inspection."""
import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shlex
import sys

EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
ZONE_LAYOUT_FILE = EXAMPLE_ROOT / "zones.yaml"
HELLO_SCRIPT_FILE = EXAMPLE_ROOT / "hello.sh"
sys.path.insert(0, str(PROJECT_ROOT))

import seedemu as seed
import skyseed_v2 as sky
import tools

DEFAULT_START_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
DEFAULT_END_TIME = DEFAULT_START_TIME + timedelta(seconds=30)
DEFAULT_OBSERVATION_INTERVAL = timedelta(seconds=10)


def build_emulator() -> seed.Emulator:
    """Edit this SEED scene: one orbit, New York GS, IX100 and a ground AS."""

    emulator = seed.Emulator()
    base = seed.Base()
    base.createInternetExchange(
        100,
        prefix="10.100.0.0/24",
        create_rs=False,
    )

    skyseed_as = sky.emulator.seedemu.EmulatorAutonomousSystem(65001)
    skyseed_as.setIbgpMode("disabled")
    base.setAutonomousSystem(skyseed_as)

    shell = skyseed_as.create_shell("shell-1")
    orbit = shell.create_orbit(
        "orbit-1",
        parameters=sky.emulator.entities.OrbitParameters(
            altitude_km=550.0,
            inclination_deg=53.0,
            right_ascension_deg=245.0,
            epoch_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    )
    # Each satellite has four physical ISL ports; Tiny's policy below selects one.
    orbit.create_satellite(
        "satellite-0",
        orbit_index=0,
        mean_anomaly_deg=126.0,
        maximum_simultaneous_inter_satellite_links=4,
    )
    orbit.create_satellite(
        "satellite-1",
        orbit_index=1,
        mean_anomaly_deg=146.0,
        maximum_simultaneous_inter_satellite_links=4,
    )

    ground_station = skyseed_as.create_ground_station(
        "gs-new-york",
        display_name="New York Ground Station",
        location=sky.emulator.entities.GroundStationLocation(
            latitude_deg=40.7128,
            longitude_deg=-74.0060,
            elevation_m=10.0,
        ),
        operator="Skyseed",
        station_type="gateway",
    )
    ground_station.joinNetwork("ix100", "10.100.0.101")

    ground_as = base.createAutonomousSystem(65002)
    ground_as.createNetwork("ground-net", "10.202.0.0/24")
    ground_router = ground_as.createRouter("ground-router")
    ground_router.joinNetwork("ground-net", "10.202.0.254")
    ground_router.joinNetwork("ix100", "10.100.0.102")

    # These are the actual SEED nodes; run the script once per container startup.
    for node in (*skyseed_as.satellites(), ground_station, ground_router):
        node.importFile(str(HELLO_SCRIPT_FILE), "/hello.sh")
        node.appendStartCommand(shlex.join(["/bin/sh", "/hello.sh", node.getName()]))

    # SEED routing connects the ordinary ground network and GS through IX100.
    # These layers do not supply the Simulator's satellite Route Model.
    ospf = seed.Ospf().maskAsn(65001)
    ibgp = seed.Ibgp().maskAsn(65001)
    ebgp = seed.Ebgp().addPrivatePeeringByRouters(
        100,
        65001,
        "gs-new-york",
        65002,
        "ground-router",
        seed.PeerRelationship.Unfiltered,
    )
    for layer in (base, seed.Routing(), ospf, ibgp, ebgp):
        emulator.addLayer(layer)

    return emulator


def simulation_configuration(zone_layout_path=ZONE_LAYOUT_FILE):
    """Configure Tiny's built-in Models; omitted roles/settings keep their defaults."""
    configuration = sky.simulator.DefaultSimulatorConfiguration(
        # Two local overlapping Zones; an explicit None disables Zone service.
        zone_layout_definition=(
            None if zone_layout_path is None
            else sky.configuration.simulator.load_zone_layout_definition(zone_layout_path)
        ),
        # Physical feasibility: 5,000 km ISLs and horizon-visible GS access.
        # None removes the GS distance cap; Zone visibility still requires 30°.
        maximum_isl_distance_m=5_000_000.0,
        ground_station_minimum_elevation_deg=0.0,
        ground_station_maximum_distance_m=None,
        zone_coverage_minimum_elevation_deg=30.0,
        # One same-plane link, no cross-plane links. This constructor's defaults
        # keep search unrestricted and minimum service zero for the short scene.
        inter_satellite_configuration=sky.simulator.planner.RetainFirstInterSatelliteConfiguration(
            desired_same_plane_links=1,
            desired_cross_plane_links=0,
            maximum_cross_plane_separation=0,
        ),
        # The GS has no explicit device limit: use one access, query all candidates,
        # and admit immediately without requiring a future minimum service span.
        default_ground_station_limit=1,
        initial_candidate_count=None,
        ground_station_minimum_service_duration=timedelta(0),
    )
    # Inherit bandwidth, loss and delay deadband; disable only periodic refresh.
    # Threshold-driven delay updates remain enabled for both connection kinds.
    return replace(
        configuration,
        parameter_configuration={
            kind: replace(parameters, max_staleness=None)
            for kind, parameters in configuration.parameter_configuration.items()
        },
    )


def create_simulator(
    emulator,
    *,
    zone_layout_path=ZONE_LAYOUT_FILE,
    duration=timedelta(seconds=30),
    observation_interval=DEFAULT_OBSERVATION_INTERVAL,
):
    # Adapt pure static input outside Simulator. The SEED/Skyseed boundary supplies
    # management networking; the IX and ordinary AS above remain explicit.
    systems = sky.emulator.seedemu.get_skyseed_autonomous_systems(emulator.getLayer("Base"))
    definition = sky.simulator.SimulationDefinition(
        static_input=sky.adapters.create_simulator_static_input(systems),
        timeline=sky.simulator.SimulationTimeline(
            start_time=DEFAULT_START_TIME,
            end_time=DEFAULT_START_TIME + duration,
            observation_interval=observation_interval,
        ),
    )
    return sky.simulator.create_default_simulator(
        definition,
        configuration=simulation_configuration(zone_layout_path),
        planner_configuration=sky.simulator.planner.PlannerConfiguration(
            inter_satellite_acquisition_duration=timedelta(0),
            ground_station_acquisition_duration=timedelta(0)),
        # Supply a configured instance for the Route role: the existing gateway
        # hop-count algorithm with one gateway, not a new routing algorithm.
        route_model=sky.simulator.planner.GatewayHopCountRoutePlanningModel(
            gateway_candidate_count=1,
        ),
    )


# Direct Python defaults; CLI options override these experiment choices.
DEFAULT_DURATION = timedelta(seconds=30)
DEFAULT_OPERATION = "serve"
DEFAULT_BATCH_DURATION = timedelta(seconds=300)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sky.experiment.ExperimentController.add_arguments(parser, default_operation=DEFAULT_OPERATION, batch_duration=DEFAULT_BATCH_DURATION)
    parser.add_argument("--duration-seconds", type=float, default=DEFAULT_DURATION.total_seconds())
    args = parser.parse_args(argv)

    # Build the editable SEED scene, then adapt it and declare the initial Timeline.
    emulator = build_emulator()
    simulator = create_simulator(
        emulator,
        duration=timedelta(seconds=args.duration_seconds),
    )
    # Experiment settings select material cutoff, output and execution resources.
    # The default retains calculation; deployment and execution are separate controls.
    controller = sky.experiment.ExperimentController(
        emulator,
        simulator,
        args.output or DEFAULT_OUTPUT,
        entry_path=Path(__file__),
        forwarding_backend="bpf",
        input_files=(ZONE_LAYOUT_FILE, HELLO_SCRIPT_FILE),
        scenario_parameters={"duration_seconds": args.duration_seconds},
        through=args.through, traffic_enabled=not args.no_traffic, traffic_telemetry_enabled=not args.no_traffic_telemetry,
        replace=args.override,
        docker_compiler=seed.Docker(
            namingScheme="as{asn}{role}-{displayName}",
            internetMapEnabled=not args.no_internet_map,
        ),
        deploy_parallel=args.deploy_parallel,
        runtime_workers=args.runtime_workers,
        route_workers=args.route_workers,
    )
    def present(output, publication=None):
        return tools.write_experiment_tools(output, physical_world=simulator.get_physical_world(),
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
