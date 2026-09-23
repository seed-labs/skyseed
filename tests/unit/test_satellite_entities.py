from datetime import datetime, timedelta, timezone

import pytest
from seedemu.core import Node

from skyseed.emulator.entities import OrbitParameters, EmulatorSatellite, EmulatorShell


def parameters() -> OrbitParameters:
    return OrbitParameters(
        altitude_km=550.0,
        inclination_deg=53.0,
        right_ascension_deg=15.0,
        epoch_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_shell_creates_and_queries_owned_orbit() -> None:
    shell = EmulatorShell("shell-1", asn=65000)
    orbit = shell.create_orbit("orbit-1", parameters=parameters())

    assert shell.get_orbit("orbit-1") is orbit
    assert shell.orbits == (orbit,)
    assert orbit.shell_id == shell.shell_id
    assert orbit.parameters == parameters()


def test_shell_rejects_duplicate_orbit_id() -> None:
    shell = EmulatorShell("shell-1", asn=65000)
    shell.create_orbit("orbit-1", parameters=parameters())

    with pytest.raises(ValueError, match="orbit_id"):
        shell.create_orbit("orbit-1", parameters=parameters())


def test_orbit_creates_and_queries_satellite() -> None:
    orbit = EmulatorShell("shell-1", asn=65000).create_orbit(
        "orbit-1", parameters=parameters()
    )
    satellite = orbit.create_satellite(
        "satellite-7",
        orbit_index=7,
        mean_anomaly_deg=45.0,
        payload={"profile": "test"},
        max_simultaneous_ground_station_accesses=2,
        maximum_simultaneous_inter_satellite_links=4,
    )

    assert orbit.get_satellite("satellite-7") is satellite
    assert satellite.shell_id == "shell-1"
    assert satellite.orbit_id == "orbit-1"
    assert satellite.orbit_index == 7
    assert orbit.parameters == parameters()
    assert not hasattr(satellite, "orbit_parameters")
    assert not hasattr(satellite, "_orbit_parameters")
    assert satellite.payload == {"profile": "test"}
    assert satellite.max_simultaneous_ground_station_accesses == 2
    assert satellite.maximum_simultaneous_inter_satellite_links == 4
    assert isinstance(satellite, EmulatorSatellite)
    assert isinstance(satellite, Node)
    assert satellite.getName() == satellite.satellite_id
    assert satellite.getInterfaces() == []

    with pytest.raises(TypeError):
        satellite.payload["profile"] = "changed"

    for index, invalid in enumerate((0, -1, True), start=100):
        with pytest.raises(
            ValueError, match="max_simultaneous_ground_station_accesses"
        ):
            orbit.create_satellite(
                f"invalid-{index}",
                orbit_index=index,
                mean_anomaly_deg=90.0,
                max_simultaneous_ground_station_accesses=invalid,
            )
        with pytest.raises(
            ValueError,
            match="maximum_simultaneous_inter_satellite_links",
        ):
            orbit.create_satellite(
                f"invalid-isl-{index}",
                orbit_index=index,
                mean_anomaly_deg=90.0,
                maximum_simultaneous_inter_satellite_links=invalid,
            )


def test_batch_creation_is_deterministic_and_evenly_spaced() -> None:
    orbit = EmulatorShell("shell-1", asn=65000).create_orbit(
        "orbit-1", parameters=parameters()
    )

    satellites = orbit.create_satellites(
        2,
        satellite_id_prefix="satellite",
        maximum_simultaneous_inter_satellite_links=3,
    )

    assert tuple(satellite.satellite_id for satellite in satellites) == (
        "satellite-0",
        "satellite-1",
    )
    assert tuple(satellite.orbit_index for satellite in satellites) == (0, 1)
    assert tuple(satellite.mean_anomaly_deg for satellite in satellites) == (
        0.0,
        180.0,
    )
    assert {
        satellite.maximum_simultaneous_inter_satellite_links
        for satellite in satellites
    } == {3}


def test_satellite_ids_are_unique_across_orbits_of_one_owner() -> None:
    shell = EmulatorShell("shell-1", asn=65000)
    first = shell.create_orbit("orbit-1", parameters=parameters())
    second = shell.create_orbit("orbit-2", parameters=parameters())
    first.create_satellite(
        "satellite-0", orbit_index=0, mean_anomaly_deg=0.0
    )

    with pytest.raises(ValueError, match="satellite_id"):
        second.create_satellite(
            "satellite-0", orbit_index=0, mean_anomaly_deg=180.0
        )


def test_orbit_rejects_duplicate_index_without_partial_batch() -> None:
    orbit = EmulatorShell("shell-1", asn=65000).create_orbit(
        "orbit-1", parameters=parameters()
    )
    orbit.create_satellite(
        "satellite-0", orbit_index=1, mean_anomaly_deg=0.0
    )

    with pytest.raises(ValueError, match="orbit_index"):
        orbit.create_satellite(
            "satellite-1", orbit_index=1, mean_anomaly_deg=180.0
        )
    assert tuple(satellite.satellite_id for satellite in orbit.satellites) == (
        "satellite-0",
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"altitude_km": 0, "inclination_deg": 53, "right_ascension_deg": 0, "epoch_utc": datetime(2026, 1, 1, tzinfo=timezone.utc)}, "altitude"),
        ({"altitude_km": 550, "inclination_deg": 181, "right_ascension_deg": 0, "epoch_utc": datetime(2026, 1, 1, tzinfo=timezone.utc)}, "inclination"),
        ({"altitude_km": 550, "inclination_deg": 53, "right_ascension_deg": 360, "epoch_utc": datetime(2026, 1, 1, tzinfo=timezone.utc)}, "right_ascension"),
    ],
)
def test_orbit_parameters_reject_invalid_values(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        OrbitParameters(**kwargs)


def test_orbit_epoch_is_required_aware_and_normalized_to_utc() -> None:
    local = timezone(timedelta(hours=8))
    parameters_value = OrbitParameters(
        550.0,
        53.0,
        15.0,
        datetime(2026, 1, 1, 8, tzinfo=local),
    )
    assert parameters_value.epoch_utc.tzinfo is timezone.utc
    assert parameters_value.epoch_utc == datetime(
        2026, 1, 1, tzinfo=timezone.utc
    )

    with pytest.raises(TypeError, match="epoch_utc"):
        OrbitParameters(550.0, 53.0, 15.0)
    with pytest.raises(ValueError, match="timezone-aware"):
        OrbitParameters(550.0, 53.0, 15.0, datetime(2026, 1, 1))
