"""Create the initial static Skyseed v2 Simulator topology."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
import sys


EXAMPLE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_ROOT.parents[1]

if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from examples._direct_execution import prepare_direct_file_execution

prepare_direct_file_execution(__file__)

from examples.complete_static_emulator.emu import build_emulator
from skyseed_v2.configuration import load_zone_layout_definition
from skyseed_v2.node_id import NodeId
from skyseed_v2.seedemu_integration import SatelliteBase
from skyseed_v2.simulator import (
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
)
from skyseed_v2.simulator_input_generation import create_simulator_static_input

# User-adjustable deterministic simulation parameters.
DEFAULT_START_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
DEFAULT_END_TIME = datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
DEFAULT_SLICE_INTERVAL = timedelta(seconds=10)
UNBOUNDED_TIMESTAMPS_TO_DISPLAY = 4
ZONE_LAYOUT_FILE = EXAMPLE_ROOT / "zones.yaml"
ZONE_QUERY_POINTS = (
    ("single", 34.0522, -118.2437),
    ("overlap", 40.7128, -74.0060),
)


def main() -> None:
    emulator = build_emulator()

    # Emulator-to-Simulator input adaptation in this external assembly entrypoint.
    base = emulator.getLayer("Base")
    if not isinstance(base, SatelliteBase):
        raise TypeError("complete example Base layer must be a SatelliteBase")
    skyseed_autonomous_systems = base.get_skyseed_autonomous_systems()
    simulator_input = create_simulator_static_input(skyseed_autonomous_systems)

    # Simulator construction and static topology materialization.
    timeline = SimulationTimeline(
        start_time=DEFAULT_START_TIME,
        end_time=DEFAULT_END_TIME,
        slice_interval=DEFAULT_SLICE_INTERVAL,
    )
    definition = SimulationDefinition(
        static_input=simulator_input,
        timeline=timeline,
    )
    simulator = Simulator(definition)
    # YAML is the standard path for layouts of this size. The same immutable
    # ZoneLayoutDefinition may also be assembled programmatically for small uses.
    zone_layout_definition = load_zone_layout_definition(ZONE_LAYOUT_FILE)
    topology = simulator.create_topology(
        zone_layout_definition=zone_layout_definition,
    )

    satellite_id = NodeId(65001, "satellite", "satellite-0")
    ground_station_id = NodeId(65001, "ground-station", "gs-new-york")
    autonomous_system = topology.get_autonomous_system(65001)
    shell = topology.get_shell(65001, "shell-1")
    orbit = topology.get_orbit(65001, "shell-1", "orbit-1")
    satellite = orbit.get_satellite(satellite_id)
    ground_station = autonomous_system.get_ground_station(ground_station_id)

    assert topology.get_satellite(satellite_id) is satellite
    assert topology.get_ground_station(ground_station_id) is ground_station
    print(
        "created Simulator static topology: "
        f"AS{autonomous_system.asn}/{shell.shell_id}/{orbit.orbit_id}/"
        f"{satellite.satellite_id}, {ground_station.ground_station_id}"
    )
    timestamps = timeline.iter_timestamps()
    if not timeline.is_bounded:
        timestamps = islice(timestamps, UNBOUNDED_TIMESTAMPS_TO_DISPLAY)
    print(
        "timeline timestamps: "
        + ", ".join(timestamp.isoformat() for timestamp in timestamps)
    )
    for label, latitude_deg, longitude_deg in ZONE_QUERY_POINTS:
        matches = topology.find_zones(latitude_deg, longitude_deg)
        authoritative = topology.resolve_zone(latitude_deg, longitude_deg)
        print(
            f"{label} physical matches: "
            f"{', '.join(zone.zone_id for zone in matches)}; "
            f"authoritative Zone: {authoritative.zone_id}"
        )


if __name__ == "__main__":
    main()
