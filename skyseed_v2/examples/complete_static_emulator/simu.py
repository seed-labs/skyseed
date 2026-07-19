"""Create the initial static Skyseed v2 Simulator topology."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import islice

from skyseed_v2.node_id import NodeId
from skyseed_v2.seedemu_integration import SatelliteBase
from skyseed_v2.simulator import (
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
)
from skyseed_v2.simulator_input_generation import create_simulator_static_input

from .emu import build_emulator


# User-adjustable deterministic simulation parameters.
DEFAULT_START_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
DEFAULT_END_TIME = datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
DEFAULT_SLICE_INTERVAL = timedelta(seconds=10)
UNBOUNDED_TIMESTAMPS_TO_DISPLAY = 4


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
    topology = simulator.create_topology()

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


if __name__ == "__main__":
    main()
