"""Compile isolated Satellites plus one GroundStation eBGP ground exit."""

from __future__ import annotations

import argparse
from pathlib import Path

from seedemu.core import Emulator
from seedemu.layers import Ebgp, Ibgp, Ospf, PeerRelationship, Routing

from skyseed_v2.domain import GroundStationLocation, OrbitParameters
from skyseed_v2.seed import (
    GroundStationAutonomousSystem,
    SatelliteAutonomousSystem,
    SatelliteBase,
    SatelliteDockerCompiler,
)


SATELLITE_ASN = 65000
GROUND_STATION_ASN = 151
GROUND_INTERNET_ASN = 152
IX_ID = 100
GROUND_PREFIX = "10.152.0.0/24"
GROUND_ROUTER_ADDRESS = "10.152.0.254"


def build_emulator():
    emulator = Emulator()
    base = SatelliteBase()
    base.createInternetExchange(IX_ID, create_rs=False)

    satellite_as = SatelliteAutonomousSystem(SATELLITE_ASN)
    base.setAutonomousSystem(satellite_as)
    orbit = satellite_as.create_shell("shell-1").create_orbit(
        "orbit-1",
        parameters=OrbitParameters(550.0, 53.0, 15.0),
    )
    orbit.create_satellites(2, satellite_id_prefix="satellite")

    ground_station_as = GroundStationAutonomousSystem(GROUND_STATION_ASN)
    base.setAutonomousSystem(ground_station_as)
    ground_station = ground_station_as.create_ground_station(
        "gs-new-york",
        location=GroundStationLocation(
            latitude_deg=40.7128,
            longitude_deg=-74.0060,
            elevation_m=10.0,
        ),
        station_type="gateway",
        operator="Skyseed",
        display_name="New York Ground Station",
        service_config={"profile": "minimal-ground-egress"},
    )
    ground_station.joinNetwork(f"ix{IX_ID}")

    ground_as = base.createAutonomousSystem(GROUND_INTERNET_ASN)
    ground_as.createNetwork("ground-net", GROUND_PREFIX)
    ground_router = ground_as.createRouter("ground-router")
    ground_router.joinNetwork("ground-net", GROUND_ROUTER_ADDRESS)
    ground_router.joinNetwork(f"ix{IX_ID}")

    routing = Routing()
    ospf = Ospf().maskAsn(GROUND_STATION_ASN).maskAsn(GROUND_INTERNET_ASN)
    ibgp = Ibgp().maskAsn(GROUND_STATION_ASN).maskAsn(GROUND_INTERNET_ASN)
    ebgp = Ebgp().addPrivatePeering(
        IX_ID,
        GROUND_STATION_ASN,
        GROUND_INTERNET_ASN,
        PeerRelationship.Unfiltered,
    )

    for layer in (base, routing, ospf, ibgp, ebgp):
        emulator.addLayer(layer)
    return (
        emulator,
        satellite_as,
        ground_station_as,
        ground_as,
        ground_station,
        ground_router,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("output") / "static_ground_station",
    )
    parser.add_argument("--no-internet-map", action="store_true")
    parser.add_argument("--override", action="store_true")
    arguments = parser.parse_args()

    emulator, satellite_as, ground_station_as, _, _, _ = build_emulator()
    emulator.render()
    emulator.compile(
        SatelliteDockerCompiler(
            internet_map_enabled=not arguments.no_internet_map
        ),
        str(arguments.output),
        override=arguments.override,
    )

    print(
        f"compiled {len(satellite_as.satellites())} isolated Satellites and "
        f"{len(ground_station_as.ground_stations())} GroundStation to "
        f"{arguments.output}"
    )
    for descriptor in ground_station_as.ground_station_descriptors():
        print(
            f"- {descriptor.ground_station_id}: "
            f"{descriptor.runtime_node.value}"
        )


if __name__ == "__main__":
    main()
