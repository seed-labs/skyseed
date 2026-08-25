from dataclasses import MISSING, FrozenInstanceError, fields
from datetime import datetime, timezone

import pytest

from skyseed_v2.configuration.emulator import (
    EbgpPeeringConfig,
    EbgpRouterReference,
    GeneratedOrbitLayout,
    GroundStationConfig,
    InOrbitPhaseDistribution,
    OrbitConfig,
    RightAscensionDistribution,
    SatelliteConfig,
    ShellConfig,
    SkyseedAutonomousSystemConfig,
    StaticEmulatorConfig,
)


def test_generated_shell_config_is_immutable_and_static_only() -> None:
    config = ShellConfig(
        shell_id="shell-a",
        altitude_km=560.0,
        inclination_deg=97.6,
        epoch_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        generated_orbit_layout=GeneratedOrbitLayout(
            orbit_count=6,
            satellites_per_orbit=58,
            max_simultaneous_ground_station_accesses=2,
            right_ascension_distribution=RightAscensionDistribution(
                0.0, 60.0
            ),
            in_orbit_phase_distribution=InOrbitPhaseDistribution(
                0.0, 360.0 / 58, 180.0 / 58
            ),
        ),
    )

    assert config.generated_orbit_layout is not None
    assert config.generated_orbit_layout.orbit_count == 6
    assert config.generated_orbit_layout.satellites_per_orbit == 58
    assert (
        config.generated_orbit_layout.max_simultaneous_ground_station_accesses
        == 2
    )
    assert {field.name for field in fields(config)} == {
        "shell_id",
        "altitude_km",
        "inclination_deg",
        "epoch_utc",
        "generated_orbit_layout",
        "explicit_orbits",
    }
    assert not any(
        "position" in field.name
        or "visibility" in field.name
        for field in fields(config)
    )
    with pytest.raises(FrozenInstanceError):
        config.altitude_km = 550.0


def test_phase_distribution_supports_distinct_alternating_and_linear_policies() -> None:
    alternating = InOrbitPhaseDistribution(1.0, 2.0, 3.0)
    linear = InOrbitPhaseDistribution(
        start_deg=1.0,
        step_deg=2.0,
        orbit_phase_step_deg=4.0,
    )

    assert alternating.alternating_orbit_offset_deg == 3.0
    assert alternating.orbit_phase_step_deg is None
    assert linear.alternating_orbit_offset_deg == 0.0
    assert linear.orbit_phase_step_deg == 4.0
    with pytest.raises(ValueError, match="requires.*to be zero"):
        InOrbitPhaseDistribution(
            alternating_orbit_offset_deg=3.0,
            orbit_phase_step_deg=4.0,
        )


def test_explicit_shell_normalizes_orbit_and_satellite_input_order() -> None:
    later = SatelliteConfig(
        "satellite-b",
        1,
        180.0,
        max_simultaneous_ground_station_accesses=2,
    )
    earlier = SatelliteConfig("satellite-a", 0, 0.0)
    orbit_b = OrbitConfig("orbit-b", 180.0, (later, earlier))
    orbit_a = OrbitConfig("orbit-a", 0.0, (later, earlier))
    config = ShellConfig(
        shell_id="shell-explicit",
        altitude_km=550.0,
        inclination_deg=53.0,
        epoch_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        explicit_orbits=(orbit_b, orbit_a),
    )

    assert tuple(orbit.orbit_id for orbit in config.explicit_orbits) == (
        "orbit-a",
        "orbit-b",
    )
    assert tuple(
        satellite.satellite_id
        for satellite in config.explicit_orbits[0].satellites
    ) == ("satellite-a", "satellite-b")
    assert (
        config.explicit_orbits[0]
        .satellites[1]
        .max_simultaneous_ground_station_accesses
        == 2
    )


@pytest.mark.parametrize("capability", (0, -1, True))
def test_satellite_configs_require_positive_optional_access_capability(
    capability: int,
) -> None:
    with pytest.raises(
        ValueError, match="max_simultaneous_ground_station_accesses"
    ):
        GeneratedOrbitLayout(
            1,
            1,
            max_simultaneous_ground_station_accesses=capability,
        )
    with pytest.raises(
        ValueError, match="max_simultaneous_ground_station_accesses"
    ):
        SatelliteConfig(
            "satellite",
            0,
            0.0,
            max_simultaneous_ground_station_accesses=capability,
        )


@pytest.mark.parametrize(
    "kwargs",
    (
        {},
        {
            "generated_orbit_layout": GeneratedOrbitLayout(1, 1),
            "explicit_orbits": (
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
            epoch_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
            **kwargs,
        )


def test_ground_station_config_has_explicit_nullable_descriptive_operator() -> None:
    config = GroundStationConfig(
        display_name="Gateway One",
        latitude_deg=40.0,
        longitude_deg=-74.0,
        elevation_m=10.0,
        operator="  Starlink  ",
        station_type="gateway",
        identity_key="starlink|gateway-one|40|-74",
    )

    assert config.operator == "Starlink"
    nullable = GroundStationConfig(
        display_name="Gateway Without Known Operator",
        latitude_deg=41.0,
        longitude_deg=-73.0,
        elevation_m=11.0,
        operator=None,
        station_type="gateway",
        ground_station_id="gs-no-known-operator",
    )
    assert nullable.operator is None
    assert nullable.max_simultaneous_satellite_accesses is None
    assert next(
        field for field in fields(GroundStationConfig) if field.name == "operator"
    ).default is MISSING
    with pytest.raises(ValueError, match="operator"):
        GroundStationConfig(
            display_name="Gateway With Invalid Operator",
            latitude_deg=41.0,
            longitude_deg=-73.0,
            elevation_m=11.0,
            operator="   ",
            station_type="gateway",
            ground_station_id="gs-invalid-operator",
        )
    assert not hasattr(config, "service_config")
    assert not hasattr(config, "service_address")
    assert not hasattr(config, "epoch")


@pytest.mark.parametrize("capability", (0, -1, True))
def test_ground_station_config_requires_positive_optional_access_capability(
    capability: int,
) -> None:
    with pytest.raises(
        ValueError, match="max_simultaneous_satellite_accesses"
    ):
        GroundStationConfig(
            display_name="Gateway",
            latitude_deg=41.0,
            longitude_deg=-73.0,
            elevation_m=11.0,
            operator=None,
            station_type="gateway",
            ground_station_id="gs-invalid-capability",
            max_simultaneous_satellite_accesses=capability,
        )


def test_ground_station_identity_key_is_stable_and_duplicate_safe() -> None:
    first = GroundStationConfig(
        display_name="Gateway One",
        latitude_deg=40.0,
        longitude_deg=-74.0,
        elevation_m=10.0,
        operator="Skyseed",
        station_type="gateway",
        identity_key="skyseed|gateway-one|40|-74",
    )
    second = GroundStationConfig(
        display_name="Renamed display only",
        latitude_deg=41.0,
        longitude_deg=-73.0,
        elevation_m=20.0,
        operator="Skyseed",
        station_type="gateway",
        identity_key="skyseed|gateway-one|40|-74",
    )

    with pytest.raises(ValueError, match="duplicate logical ID"):
        StaticEmulatorConfig(
            skyseed_autonomous_systems=(
                SkyseedAutonomousSystemConfig(
                    65001,
                    ground_stations=(first, second),
                ),
            )
        )


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
        GeneratedOrbitLayout(0, 1)
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
        GeneratedOrbitLayout(
            orbit_count=2,
            satellites_per_orbit=2,
            right_ascension_distribution=RightAscensionDistribution(
                0.0, 360.0
            ),
        )
    with pytest.raises(ValueError, match="duplicate Satellites"):
        GeneratedOrbitLayout(
            orbit_count=2,
            satellites_per_orbit=2,
            in_orbit_phase_distribution=InOrbitPhaseDistribution(0.0, 360.0),
        )


def test_unfiltered_ebgp_peering_uses_one_canonical_symmetric_pair() -> None:
    peering = EbgpPeeringConfig(
        100,
        (
            EbgpRouterReference(65002, "router-b"),
            EbgpRouterReference(65001, "router-a"),
        ),
    )

    assert tuple(peer.asn for peer in peering.peer_routers) == (65001, 65002)
    assert not hasattr(peering, "first")
    assert not hasattr(peering, "second")
