from dataclasses import FrozenInstanceError, fields

import pytest

from skyseed_v2.configuration import (
    GroundStationConfig,
    InOrbitPhaseDistributionConfig,
    OrbitGenerationConfig,
    OrbitConfig,
    RaanDistributionConfig,
    SatelliteConfig,
    ShellConfig,
)


def test_generated_shell_config_is_immutable_and_static_only() -> None:
    config = ShellConfig(
        shell_id="shell-a",
        altitude_km=560.0,
        inclination_deg=97.6,
        generation=OrbitGenerationConfig(
            orbit_count=6,
            satellites_per_orbit=58,
            raan=RaanDistributionConfig(0.0, 60.0),
            phase=InOrbitPhaseDistributionConfig(
                0.0, 360.0 / 58, 180.0 / 58
            ),
        ),
    )

    assert config.generation.orbit_count == 6
    assert config.generation.satellites_per_orbit == 58
    assert {field.name for field in fields(config)} == {
        "shell_id",
        "altitude_km",
        "inclination_deg",
        "generation",
        "orbits",
    }
    assert not any(
        "epoch" in field.name
        or "position" in field.name
        or "visibility" in field.name
        for field in fields(config)
    )
    with pytest.raises(FrozenInstanceError):
        config.altitude_km = 550.0


def test_explicit_shell_normalizes_orbit_and_satellite_input_order() -> None:
    later = SatelliteConfig("satellite-b", 1, 180.0)
    earlier = SatelliteConfig("satellite-a", 0, 0.0)
    orbit_b = OrbitConfig("orbit-b", 180.0, (later, earlier))
    orbit_a = OrbitConfig("orbit-a", 0.0, (later, earlier))
    config = ShellConfig(
        shell_id="shell-explicit",
        altitude_km=550.0,
        inclination_deg=53.0,
        orbits=(orbit_b, orbit_a),
    )

    assert tuple(orbit.orbit_id for orbit in config.orbits) == (
        "orbit-a",
        "orbit-b",
    )
    assert tuple(
        satellite.satellite_id for satellite in config.orbits[0].satellites
    ) == ("satellite-a", "satellite-b")


@pytest.mark.parametrize(
    "kwargs",
    (
        {},
        {
            "generation": OrbitGenerationConfig(1, 1),
            "orbits": (
                OrbitConfig(
                    "orbit-a",
                    0.0,
                    (SatelliteConfig("sat-a", 0, 0.0),),
                ),
            ),
        },
    ),
)
def test_shell_requires_exactly_one_construction_mode(kwargs) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ShellConfig(
            shell_id="shell-a",
            altitude_km=550.0,
            inclination_deg=53.0,
            **kwargs,
        )


def test_ground_station_config_has_descriptive_operator_without_service() -> None:
    config = GroundStationConfig(
        display_name="Gateway One",
        latitude_deg=40.0,
        longitude_deg=-74.0,
        elevation_m=10.0,
        operator="Starlink",
        station_type="gateway",
        identity_key="starlink|gateway-one|40|-74",
    )

    assert config.operator == "Starlink"
    assert not hasattr(config, "service_config")
    assert not hasattr(config, "service_address")
    assert not hasattr(config, "epoch")


def test_invalid_and_duplicate_explicit_values_fail_closed() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        OrbitConfig(
            "orbit-a",
            0.0,
            (
                SatelliteConfig("sat-a", 0, 0.0),
                SatelliteConfig("sat-a", 1, 180.0),
            ),
        )
    with pytest.raises(ValueError, match="positive integer"):
        OrbitGenerationConfig(0, 1)
    with pytest.raises(ValueError, match="exactly one"):
        GroundStationConfig(
            display_name="Gateway",
            latitude_deg=0.0,
            longitude_deg=0.0,
            elevation_m=0.0,
            operator="Starlink",
            station_type="gateway",
            ground_station_id="gs-a",
            identity_key="also-specified",
        )


def test_generated_distributions_are_valid_before_entity_creation() -> None:
    with pytest.raises(ValueError, match="duplicate Orbits"):
        OrbitGenerationConfig(
            orbit_count=2,
            satellites_per_orbit=2,
            raan=RaanDistributionConfig(0.0, 360.0),
        )
    with pytest.raises(ValueError, match="duplicate Satellites"):
        OrbitGenerationConfig(
            orbit_count=2,
            satellites_per_orbit=2,
            phase=InOrbitPhaseDistributionConfig(0.0, 360.0),
        )
