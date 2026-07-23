from datetime import datetime, timezone

from seedemu.core import Emulator

from skyseed_v2.entities import GroundStationLocation, OrbitParameters
from skyseed_v2.seedemu_integration import SatelliteBase, SkyseedAutonomousSystem


def test_one_as_owns_satellite_ground_station_and_standard_seed_objects() -> None:
    emulator = Emulator()
    base = SatelliteBase()
    skyseed_as = SkyseedAutonomousSystem(150)
    base.setAutonomousSystem(skyseed_as)

    network = skyseed_as.createNetwork("local-net", "10.150.0.0/24")
    router = skyseed_as.createRouter("router-1").joinNetwork("local-net")
    host = skyseed_as.createHost("host-1").joinNetwork("local-net")
    shell = skyseed_as.create_shell("shell-1")
    orbit = shell.create_orbit(
        "orbit-1",
        parameters=OrbitParameters(
            550.0, 53.0, 15.0, datetime(2026, 1, 1, tzinfo=timezone.utc)
        ),
    )
    satellite = orbit.create_satellite(
        "shared-id", orbit_index=0, mean_anomaly_deg=0.0
    )
    station = skyseed_as.create_ground_station(
        "shared-id",
        location=GroundStationLocation(40.0, -74.0),
        station_type="gateway",
        operator="Skyseed",
    ).joinNetwork("local-net")

    emulator.addLayer(base)
    emulator.render()
    registry = emulator.getRegistry()

    assert skyseed_as.get_satellite("shared-id") is satellite
    assert skyseed_as.get_ground_station("shared-id") is station
    assert skyseed_as.getRouter("shared-id") is station
    assert skyseed_as.getRouter("router-1") is router
    assert skyseed_as.getHost("host-1") is host
    assert skyseed_as.getNetwork("local-net") is network
    assert registry.get("150", "satnode", "shared-id") is satellite
    assert registry.get("150", "rnode", "shared-id") is station
    assert registry.get("150", "rnode", "router-1") is router
    assert registry.get("150", "hnode", "host-1") is host
    assert shell not in registry.getAll().values()
    assert orbit not in registry.getAll().values()


def test_multiple_unified_as_instances_are_independent() -> None:
    first = SkyseedAutonomousSystem(150)
    second = SkyseedAutonomousSystem(151)

    first.create_shell("shell-1")
    second.create_ground_station(
        "gs-1",
        location=GroundStationLocation(41.0, -73.0),
        station_type="gateway",
        operator="Skyseed",
    )

    assert first.getAsn() == 150
    assert second.getAsn() == 151
    assert len(first.shells) == 1
    assert first.ground_stations() == ()
    assert second.shells == ()
    assert len(second.ground_stations()) == 1


def test_satellite_only_and_ground_station_only_as_render() -> None:
    emulator = Emulator()
    base = SatelliteBase()

    satellite_as = SkyseedAutonomousSystem(150)
    orbit = satellite_as.create_shell("shell-1").create_orbit(
        "orbit-1",
        parameters=OrbitParameters(
            550.0, 53.0, 15.0, datetime(2026, 1, 1, tzinfo=timezone.utc)
        ),
    )
    satellite = orbit.create_satellite(
        "satellite-0", orbit_index=0, mean_anomaly_deg=0.0
    )
    base.setAutonomousSystem(satellite_as)

    station_as = SkyseedAutonomousSystem(151)
    station_as.createNetwork("ground-net", "10.151.0.0/24")
    station = station_as.create_ground_station(
        "gs-1",
        location=GroundStationLocation(42.0, -72.0),
        station_type="gateway",
        operator="Skyseed",
    ).joinNetwork("ground-net")
    base.setAutonomousSystem(station_as)

    emulator.addLayer(base)
    emulator.render()

    assert emulator.getRegistry().get("150", "satnode", "satellite-0") is satellite
    assert emulator.getRegistry().get("151", "rnode", "gs-1") is station
