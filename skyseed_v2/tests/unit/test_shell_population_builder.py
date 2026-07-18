import pytest

from skyseed_v2 import (
    ShellPopulationBuilder,
    InOrbitPhaseDistributionSpecification,
    OrbitGenerationSpecification,
    OrbitSpecification,
    RaanDistributionSpecification,
    SatelliteSpecification,
    ShellSpecification,
)
from skyseed_v2.seed import SkyseedAutonomousSystem


def generated_shell(
    shell_id: str,
    *,
    orbit_count: int = 2,
    satellites_per_orbit: int = 3,
) -> ShellSpecification:
    return ShellSpecification(
        shell_id=shell_id,
        altitude_km=560.0,
        inclination_deg=97.6,
        generation=OrbitGenerationSpecification(
            orbit_count=orbit_count,
            satellites_per_orbit=satellites_per_orbit,
            phase=InOrbitPhaseDistributionSpecification(
                alternating_orbit_offset_deg=5.0
            ),
        ),
    )


@pytest.mark.parametrize(
    ("orbit_count", "satellites_per_orbit"),
    ((1, 1), (2, 3), (4, 7)),
)
def test_builder_supports_arbitrary_reasonable_generated_scales(
    orbit_count: int,
    satellites_per_orbit: int,
) -> None:
    result = ShellPopulationBuilder().build(
        SkyseedAutonomousSystem(65000),
        generated_shell(
            "scale-shell",
            orbit_count=orbit_count,
            satellites_per_orbit=satellites_per_orbit,
        ),
    )

    assert len(result.shells) == 1
    assert len(result.orbits) == orbit_count
    assert len(result.satellites) == orbit_count * satellites_per_orbit
    assert len({satellite.satellite_id for satellite in result.satellites}) == len(
        result.satellites
    )


def test_generated_ids_and_angles_are_stable_and_not_input_order_dependent() -> None:
    specifications = (generated_shell("shell-b"), generated_shell("shell-a"))
    first = ShellPopulationBuilder().build(
        SkyseedAutonomousSystem(65000), specifications
    )
    second = ShellPopulationBuilder().build(
        SkyseedAutonomousSystem(65000), reversed(specifications)
    )

    assert tuple(shell.shell_id for shell in first.shells) == ("shell-a", "shell-b")
    assert tuple(orbit.orbit_id for orbit in first.orbits) == tuple(
        orbit.orbit_id for orbit in second.orbits
    )
    assert tuple(satellite.satellite_id for satellite in first.satellites) == tuple(
        satellite.satellite_id for satellite in second.satellites
    )
    assert tuple(orbit.parameters.right_ascension_deg for orbit in first.orbits[:2]) == (
        0.0,
        180.0,
    )
    assert tuple(
        satellite.mean_anomaly_deg for satellite in first.orbits[1].satellites
    ) == (5.0, 125.0, 245.0)


def test_builder_supports_explicit_orbits_and_multiple_as_instances() -> None:
    explicit = ShellSpecification(
        shell_id="explicit-shell",
        altitude_km=550.0,
        inclination_deg=53.0,
        orbits=(
            OrbitSpecification(
                "explicit-orbit",
                15.0,
                (
                    SatelliteSpecification("explicit-sat-b", 1, 180.0),
                    SatelliteSpecification("explicit-sat-a", 0, 0.0),
                ),
            ),
        ),
    )
    first = ShellPopulationBuilder().build(
        SkyseedAutonomousSystem(65000), explicit
    )
    second = ShellPopulationBuilder().build(
        SkyseedAutonomousSystem(65001), explicit
    )

    assert tuple(satellite.satellite_id for satellite in first.satellites) == (
        "explicit-sat-a",
        "explicit-sat-b",
    )
    assert tuple(satellite.satellite_id for satellite in second.satellites) == (
        "explicit-sat-a",
        "explicit-sat-b",
    )
    assert {satellite.getAsn() for satellite in first.satellites} == {65000}
    assert {satellite.getAsn() for satellite in second.satellites} == {65001}


def test_preflight_rejects_collisions_before_mutating_the_as() -> None:
    autonomous_system = SkyseedAutonomousSystem(65000)
    autonomous_system.create_shell("existing-shell")

    with pytest.raises(ValueError, match="already exists"):
        ShellPopulationBuilder().build(
            autonomous_system,
            (generated_shell("new-shell"), generated_shell("existing-shell")),
        )

    assert tuple(shell.shell_id for shell in autonomous_system.shells) == (
        "existing-shell",
    )


def test_generated_distribution_rejects_duplicate_angles_without_mutation() -> None:
    autonomous_system = SkyseedAutonomousSystem(65000)
    specification = ShellSpecification(
        shell_id="invalid-shell",
        altitude_km=550.0,
        inclination_deg=53.0,
        generation=OrbitGenerationSpecification(
            orbit_count=2,
            satellites_per_orbit=2,
            raan=RaanDistributionSpecification(0.0, 360.0),
        ),
    )

    with pytest.raises(ValueError, match="duplicate Orbits"):
        ShellPopulationBuilder().build(autonomous_system, specification)
    assert autonomous_system.shells == ()
