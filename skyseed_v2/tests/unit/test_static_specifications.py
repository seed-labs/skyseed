from dataclasses import FrozenInstanceError, fields

import pytest

from skyseed_v2.specifications import (
    GroundStationSpecification,
    InOrbitPhaseDistributionSpecification,
    OrbitGenerationSpecification,
    OrbitSpecification,
    RaanDistributionSpecification,
    SatelliteSpecification,
    ShellSpecification,
)


def test_generated_shell_specification_is_immutable_and_static_only() -> None:
    specification = ShellSpecification(
        shell_id="shell-a",
        altitude_km=560.0,
        inclination_deg=97.6,
        generation=OrbitGenerationSpecification(
            orbit_count=6,
            satellites_per_orbit=58,
            raan=RaanDistributionSpecification(0.0, 60.0),
            phase=InOrbitPhaseDistributionSpecification(
                0.0, 360.0 / 58, 180.0 / 58
            ),
        ),
    )

    assert specification.generation.orbit_count == 6
    assert specification.generation.satellites_per_orbit == 58
    assert {field.name for field in fields(specification)} == {
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
        for field in fields(specification)
    )
    with pytest.raises(FrozenInstanceError):
        specification.altitude_km = 550.0


def test_explicit_shell_normalizes_orbit_and_satellite_input_order() -> None:
    later = SatelliteSpecification("satellite-b", 1, 180.0)
    earlier = SatelliteSpecification("satellite-a", 0, 0.0)
    orbit_b = OrbitSpecification("orbit-b", 180.0, (later, earlier))
    orbit_a = OrbitSpecification("orbit-a", 0.0, (later, earlier))
    specification = ShellSpecification(
        shell_id="shell-explicit",
        altitude_km=550.0,
        inclination_deg=53.0,
        orbits=(orbit_b, orbit_a),
    )

    assert tuple(orbit.orbit_id for orbit in specification.orbits) == (
        "orbit-a",
        "orbit-b",
    )
    assert tuple(
        satellite.satellite_id for satellite in specification.orbits[0].satellites
    ) == ("satellite-a", "satellite-b")


@pytest.mark.parametrize(
    "kwargs",
    (
        {},
        {
            "generation": OrbitGenerationSpecification(1, 1),
            "orbits": (
                OrbitSpecification(
                    "orbit-a",
                    0.0,
                    (SatelliteSpecification("sat-a", 0, 0.0),),
                ),
            ),
        },
    ),
)
def test_shell_requires_exactly_one_construction_mode(kwargs) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ShellSpecification(
            shell_id="shell-a",
            altitude_km=550.0,
            inclination_deg=53.0,
            **kwargs,
        )


def test_ground_station_specification_has_descriptive_operator_without_service() -> None:
    specification = GroundStationSpecification(
        display_name="Gateway One",
        latitude_deg=40.0,
        longitude_deg=-74.0,
        elevation_m=10.0,
        operator="Starlink",
        station_type="gateway",
        identity_key="starlink|gateway-one|40|-74",
    )

    assert specification.operator == "Starlink"
    assert not hasattr(specification, "service_config")
    assert not hasattr(specification, "service_address")
    assert not hasattr(specification, "epoch")


def test_invalid_and_duplicate_explicit_values_fail_closed() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        OrbitSpecification(
            "orbit-a",
            0.0,
            (
                SatelliteSpecification("sat-a", 0, 0.0),
                SatelliteSpecification("sat-a", 1, 180.0),
            ),
        )
    with pytest.raises(ValueError, match="positive integer"):
        OrbitGenerationSpecification(0, 1)
    with pytest.raises(ValueError, match="exactly one"):
        GroundStationSpecification(
            display_name="Gateway",
            latitude_deg=0.0,
            longitude_deg=0.0,
            elevation_m=0.0,
            operator="Starlink",
            station_type="gateway",
            ground_station_id="gs-a",
            identity_key="also-specified",
        )


def test_generated_distributions_are_valid_before_application() -> None:
    with pytest.raises(ValueError, match="duplicate Orbits"):
        OrbitGenerationSpecification(
            orbit_count=2,
            satellites_per_orbit=2,
            raan=RaanDistributionSpecification(0.0, 360.0),
        )
    with pytest.raises(ValueError, match="duplicate Satellites"):
        OrbitGenerationSpecification(
            orbit_count=2,
            satellites_per_orbit=2,
            phase=InOrbitPhaseDistributionSpecification(0.0, 360.0),
        )
