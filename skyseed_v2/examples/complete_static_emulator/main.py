"""Construct the complete static Emulator with public Skyseed and SEED APIs."""

from __future__ import annotations

import argparse
from pathlib import Path

from seedemu.core import Emulator
from seedemu.layers import Ebgp, Ibgp, Ospf, PeerRelationship, Routing

from skyseed_v2 import (
    GroundStationLocation,
    OrbitParameters,
)
from skyseed_v2.simulator_input_generation import create_simulator_static_input
from skyseed_v2.seedemu_integration import (
    SatelliteBase,
    SkyseedAutonomousSystem,
    SkyseedDockerCompiler,
)
from skyseed_v2.simulator_input import SimulatorStaticInput


EXAMPLE_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = EXAMPLE_ROOT / "output"
COMPOSE_PROJECT_NAME = "skyseed_v2_complete_static_emulator"


def build_emulator() -> tuple[
    Emulator,
    SkyseedAutonomousSystem,
    SimulatorStaticInput,
]:
    """Show how public Skyseed entities compose with ordinary SEED objects."""

    emulator = Emulator()
    base = SatelliteBase()
    base.createInternetExchange(
        100,
        prefix="10.100.0.0/24",
        create_rs=False,
    )

    skyseed_as = SkyseedAutonomousSystem(65001)
    skyseed_as.setIbgpMode("disabled")
    base.setAutonomousSystem(skyseed_as)

    shell = skyseed_as.create_shell("shell-1")
    orbit = shell.create_orbit(
        "orbit-1",
        parameters=OrbitParameters(550.0, 53.0, 15.0),
    )
    orbit.create_satellite(
        "satellite-0",
        orbit_index=0,
        mean_anomaly_deg=0.0,
    )
    orbit.create_satellite(
        "satellite-1",
        orbit_index=1,
        mean_anomaly_deg=180.0,
    )

    ground_station = skyseed_as.create_ground_station(
        "gs-new-york",
        display_name="New York Ground Station",
        location=GroundStationLocation(40.7128, -74.0060, 10.0),
        operator="Skyseed",
        station_type="gateway",
    )
    ground_station.joinNetwork("ix100", "10.100.0.101")

    ground_as = base.createAutonomousSystem(65002)
    ground_as.createNetwork("ground-net", "10.202.0.0/24")
    ground_router = ground_as.createRouter("ground-router")
    ground_router.joinNetwork("ground-net", "10.202.0.254")
    ground_router.joinNetwork("ix100", "10.100.0.102")

    ospf = Ospf().maskAsn(65001)
    ibgp = Ibgp().maskAsn(65001)
    ebgp = Ebgp().addPrivatePeeringByRouters(
        100,
        65001,
        "gs-new-york",
        65002,
        "ground-router",
        PeerRelationship.Unfiltered,
    )
    for layer in (base, Routing(), ospf, ibgp, ebgp):
        emulator.addLayer(layer)

    simulator_input = create_simulator_static_input((skyseed_as,))
    return emulator, skyseed_as, simulator_input


def simulator_input_summary(
    simulator_input: SimulatorStaticInput,
) -> tuple[str, ...]:
    satellites = simulator_input.list_satellites()
    ground_stations = simulator_input.list_ground_stations()
    nodes = simulator_input.list_nodes()
    return (
        "Simulator input summary: "
        f"{len(satellites)} SatelliteSimulationInput, "
        f"{len(ground_stations)} GroundStationSimulationInput, "
        f"{len(nodes)} unique SimulationNodeId",
        *(f"- {simulation_input.node_id.value}" for simulation_input in nodes),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--override", action="store_true")
    parser.add_argument("--no-internet-map", action="store_true")
    arguments = parser.parse_args()

    emulator, _, simulator_input = build_emulator()
    for line in simulator_input_summary(simulator_input):
        print(line)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    emulator.render()
    emulator.compile(
        SkyseedDockerCompiler(
            internet_map_enabled=not arguments.no_internet_map
        ),
        str(arguments.output),
        override=arguments.override,
    )
    print(f"compiled complete static Emulator to {arguments.output}")


if __name__ == "__main__":
    main()
