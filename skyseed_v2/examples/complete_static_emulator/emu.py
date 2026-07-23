"""Construct the Emulator used by the complete static example."""

from __future__ import annotations

from datetime import datetime, timezone

from seedemu.core import Emulator
from seedemu.layers import Ebgp, Ibgp, Ospf, PeerRelationship, Routing

from skyseed_v2 import GroundStationLocation, OrbitParameters
from skyseed_v2.seedemu_integration import (
    SatelliteBase,
    SkyseedAutonomousSystem,
)


def build_emulator() -> Emulator:
    """Construct and return the complete example's upstream Emulator."""

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
        parameters=OrbitParameters(
            550.0,
            53.0,
            245.0,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    )
    orbit.create_satellite(
        "satellite-0",
        orbit_index=0,
        mean_anomaly_deg=126.0,
    )
    orbit.create_satellite(
        "satellite-1",
        orbit_index=1,
        mean_anomaly_deg=146.0,
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

    return emulator


__all__ = ["build_emulator"]
