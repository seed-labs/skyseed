"""Small 32-satellite regression fixture retained independently of examples."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_ROOT = Path(__file__).with_suffix("")
DEFAULT_SCENARIO = DATA_ROOT / "scenario.yaml"
ZONE_LAYOUT_FILE = DATA_ROOT / "zones.yaml"

from skyseed import configuration, emulator, adapters, simulator

DEFAULT_START_TIME = datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
DEFAULT_OBSERVATION_INTERVAL = timedelta(seconds=10)
DEFAULT_DURATION = timedelta(seconds=630)  # 64 timestamps, two 32-frame Plans.


def build_emulator(scenario_path=DEFAULT_SCENARIO):
    """Build the SEED network authored in the selected local scenario YAML."""
    # Edit the four-by-eight constellation, three GSs, IX110 and AS65110 there.
    # The shared loader expands orbit records; SEED integration supplies management.
    scene = configuration.emulator.load_static_emulator_config(scenario_path)
    return emulator.create_static_emulator(scene)


def create_simulator(network, *, zone_layout_path=ZONE_LAYOUT_FILE, duration=DEFAULT_DURATION):
    # External adaptation keeps SEED objects out of the Simulator core.
    systems = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer("Base"))
    definition = simulator.SimulationDefinition(
        static_input=adapters.create_simulator_static_input(systems),
        timeline=simulator.SimulationTimeline(
            start_time=DEFAULT_START_TIME,
            end_time=DEFAULT_START_TIME + duration,
            observation_interval=DEFAULT_OBSERVATION_INTERVAL,
        ),
    )
    settings = simulator.DefaultSimulatorConfiguration(
        # Five local Zones and a 10° satellite visibility threshold for Zone service.
        # None is the programmatic option for disabling Zones.
        zone_layout_definition=(
            None if zone_layout_path is None
            else configuration.simulator.load_zone_layout_definition(zone_layout_path)
        ),
        zone_coverage_minimum_elevation_deg=10.0,
        # GSs have unspecified device capacity: use two accesses per GS. None
        # queries all eligible satellites; it does not remove the capacity limit.
        default_ground_station_limit=2,
        initial_candidate_count=None,
        # Configure the default gateway hop-count Model to consider one gateway.
        gateway_candidate_count=1,
        # K3 broadens the plane search; None removes candidate-count/span bounds.
        # Keep the explicit 2+2 target and 5 s preparation / 180 s service pins.
        inter_satellite_configuration=simulator.planner.RetainFirstInterSatelliteConfiguration(
            desired_same_plane_links=2,
            desired_cross_plane_links=2,
            maximum_cross_plane_separation=3,
            same_plane_neighbor_span=None,
            cross_plane_neighbor_count=None,
            minimum_service_duration=timedelta(seconds=180),
        ),
    )
    # Other physical limits, GS admission and connection parameters use defaults.
    return simulator.create_default_simulator(definition, configuration=settings)
