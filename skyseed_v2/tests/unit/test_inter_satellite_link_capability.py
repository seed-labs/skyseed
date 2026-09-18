from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from seedemu.layers import Base

from skyseed_v2.configuration.emulator import (
    GeneratedOrbitLayout,
    SatelliteConfig,
    ShellConfig,
    SkyseedAutonomousSystemConfig,
    StaticEmulatorConfig,
    load_static_emulator_config,
)
from skyseed_v2.emulator import create_static_emulator
from skyseed_v2.emulator.seedemu import get_skyseed_autonomous_systems
from skyseed_v2.adapters.emulator_to_simulator import create_simulator_static_input
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    InterSatelliteLinkCapability,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
)
from skyseed_v2.simulator.input import (
    SatelliteSimulationInput,
    SimulatorStaticInput,
)
import skyseed_v2.simulator.physical as physical_world_package


_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _UnusedPositionModel:
    def compute_positions(self, physical_world, timestamp):
        raise AssertionError("static capability tests do not project positions")


def _physical_world(static_input: SimulatorStaticInput):
    definition = SimulationDefinition(
        static_input=static_input,
        timeline=SimulationTimeline(
            start_time=_EPOCH,
            end_time=_EPOCH + timedelta(seconds=10),
            observation_interval=timedelta(seconds=10),
        ),
    )
    return Simulator(definition).create_physical_world(
        position_model=_UnusedPositionModel()
    )


def _one_satellite_config(
    maximum_simultaneous_links: int | None,
) -> StaticEmulatorConfig:
    return StaticEmulatorConfig(
        skyseed_autonomous_systems=(
            SkyseedAutonomousSystemConfig(
                asn=65001,
                shells=(
                    ShellConfig(
                        shell_id="shell-1",
                        altitude_km=550.0,
                        inclination_deg=53.0,
                        epoch_utc=_EPOCH,
                        generated_orbit_layout=GeneratedOrbitLayout(
                            orbit_count=1,
                            satellites_per_orbit=1,
                            maximum_simultaneous_inter_satellite_links=(
                                maximum_simultaneous_links
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def _project_config(config: StaticEmulatorConfig):
    emulator = create_static_emulator(config)
    base = emulator.getLayer("Base")
    assert isinstance(base, Base)
    autonomous_systems = get_skyseed_autonomous_systems(base)
    static_input = create_simulator_static_input(autonomous_systems)
    return autonomous_systems, static_input, _physical_world(static_input)


def test_capability_is_public_immutable_and_contains_only_the_total_limit() -> None:
    capability = InterSatelliteLinkCapability(maximum_simultaneous_links=4)

    assert capability.maximum_simultaneous_links == 4
    assert {field.name for field in fields(capability)} == {
        "maximum_simultaneous_links"
    }
    assert "InterSatelliteLinkCapability" in physical_world_package.__all__
    assert physical_world_package.InterSatelliteLinkCapability is capability.__class__
    for unsupported_hardware_fact in (
        "front",
        "back",
        "left",
        "right",
        "terminals",
        "steering_cone",
        "angular_rate_envelope",
    ):
        assert not hasattr(capability, unsupported_hardware_fact)
    with pytest.raises(FrozenInstanceError):
        capability.maximum_simultaneous_links = 3


@pytest.mark.parametrize("invalid", (0, -1, True, 1.5, "4"))
def test_capability_and_upstream_values_require_a_positive_integer(
    invalid,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        InterSatelliteLinkCapability(invalid)
    with pytest.raises(ValueError, match="inter_satellite"):
        GeneratedOrbitLayout(
            orbit_count=1,
            satellites_per_orbit=1,
            maximum_simultaneous_inter_satellite_links=invalid,
        )
    with pytest.raises(ValueError, match="inter_satellite"):
        SatelliteConfig(
            satellite_id="satellite-0",
            index_in_orbit=0,
            mean_anomaly_deg=0.0,
            maximum_simultaneous_inter_satellite_links=invalid,
        )


def test_capability_materializes_through_the_complete_input_chain() -> None:
    autonomous_systems, static_input, physical_world = _project_config(
        _one_satellite_config(4)
    )

    emulator_satellite = autonomous_systems[0].shells[0].orbits[0].satellites[0]
    simulation_input = static_input.list_satellites()[0]
    physical_world_satellite = physical_world.get_satellite(simulation_input.node_id)

    assert emulator_satellite.maximum_simultaneous_inter_satellite_links == 4
    assert simulation_input.maximum_simultaneous_inter_satellite_links == 4
    assert physical_world_satellite.inter_satellite_link_capability == (
        InterSatelliteLinkCapability(maximum_simultaneous_links=4)
    )
    assert physical_world_satellite.inter_satellite_link_capability is not None
    assert (
        physical_world_satellite.inter_satellite_link_capability.maximum_simultaneous_links
        == 4
    )


def test_absent_capability_remains_unavailable_and_is_not_unlimited() -> None:
    autonomous_systems, static_input, physical_world = _project_config(
        _one_satellite_config(None)
    )

    emulator_satellite = autonomous_systems[0].shells[0].orbits[0].satellites[0]
    simulation_input = static_input.list_satellites()[0]
    physical_world_satellite = physical_world.get_satellite(simulation_input.node_id)

    assert emulator_satellite.maximum_simultaneous_inter_satellite_links is None
    assert simulation_input.maximum_simultaneous_inter_satellite_links is None
    assert physical_world_satellite.inter_satellite_link_capability is None
    assert not hasattr(physical_world_satellite, "maximum_simultaneous_links")


def test_static_input_rejects_invalid_isl_capability_before_physical_world() -> None:
    common = {
        "node_id": NodeId(65001, "satellite", "satellite-0"),
        "satellite_id": "satellite-0",
        "shell_id": "shell-1",
        "orbit_id": "orbit-1",
        "orbit_index": 0,
        "altitude_km": 550.0,
        "inclination_deg": 53.0,
        "right_ascension_deg": 0.0,
        "mean_anomaly_deg": 0.0,
        "epoch_utc": _EPOCH,
    }

    with pytest.raises(ValueError, match="positive integer"):
        SatelliteSimulationInput(
            **common,
            maximum_simultaneous_inter_satellite_links=0,
        )


def test_canonical_starlink_s3_materializes_maximum_four() -> None:
    scenario = (
        Path(__file__).resolve().parents[2]
        / "data" / "starlink"
        / "scenario.yaml"
    )
    config = load_static_emulator_config(scenario)
    from dataclasses import replace
    system, = config.skyseed_autonomous_systems
    selected_shell, = (shell for shell in system.shells if shell.shell_id == "S3")
    config = replace(config, skyseed_autonomous_systems=(replace(system, shells=(selected_shell,)),))
    configured_satellites = tuple(
        satellite
        for orbit in selected_shell.explicit_orbits
        for satellite in orbit.satellites
    )

    assert selected_shell.shell_id == "S3"
    assert len(configured_satellites) == 720
    assert {
        satellite.maximum_simultaneous_inter_satellite_links
        for satellite in configured_satellites
    } == {4}

    _, static_input, physical_world = _project_config(config)
    assert len(static_input.list_satellites()) == 720
    physical_world_satellites = tuple(
        satellite
        for autonomous_system in physical_world.autonomous_systems
        for shell in autonomous_system.shells
        for orbit in shell.orbits
        for satellite in orbit.satellites
    )
    assert {
        satellite.inter_satellite_link_capability.maximum_simultaneous_links
        for satellite in physical_world_satellites
        if satellite.inter_satellite_link_capability is not None
    } == {4}
    assert all(
        satellite.inter_satellite_link_capability is not None
        for satellite in physical_world_satellites
    )
