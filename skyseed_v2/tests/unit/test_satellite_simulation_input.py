from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone

import pytest
from seedemu.core import Node

from skyseed_v2.emulator_to_simulator import create_satellite_simulation_input
from skyseed_v2.shared import NodeId
from skyseed_v2.emulator.entities import OrbitParameters, Satellite, Shell
from skyseed_v2.simulator.input import SatelliteSimulationInput


def satellite() -> tuple[Satellite, OrbitParameters]:
    parameters = OrbitParameters(
        550.0, 53.0, 15.0, datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    orbit = Shell("shell-1", asn=65000).create_orbit(
        "orbit-1",
        parameters=parameters,
    )
    return (
        orbit.create_satellite(
            "satellite-0",
            orbit_index=0,
            mean_anomaly_deg=12.5,
        ),
        parameters,
    )


def test_input_reads_stable_identity_from_the_unique_satellite() -> None:
    entity, orbit_parameters = satellite()

    simulation_input = create_satellite_simulation_input(
        entity, orbit_parameters
    )

    assert isinstance(simulation_input, SatelliteSimulationInput)
    assert simulation_input.satellite_id == entity.satellite_id
    assert simulation_input.node_id == NodeId(
        65000, "satellite", entity.satellite_id
    )
    assert simulation_input.node_id.entity_id == simulation_input.satellite_id
    assert simulation_input.altitude_km == 550.0
    assert simulation_input.epoch_utc == orbit_parameters.epoch_utc
    with pytest.raises(FrozenInstanceError):
        simulation_input.satellite_id = "changed"


def test_input_contains_only_static_values_not_seed_or_container_objects() -> None:
    entity, orbit_parameters = satellite()
    simulation_input = create_satellite_simulation_input(
        entity, orbit_parameters
    )

    assert {field.name for field in fields(simulation_input)} == {
        "satellite_id",
        "node_id",
        "shell_id",
        "orbit_id",
        "orbit_index",
        "altitude_km",
        "inclination_deg",
        "right_ascension_deg",
        "mean_anomaly_deg",
        "epoch_utc",
    }
    assert all(
        not isinstance(value, Node)
        for value in (
            simulation_input.node_id,
            simulation_input.satellite_id,
            simulation_input.shell_id,
            simulation_input.orbit_id,
            simulation_input.orbit_index,
            simulation_input.altitude_km,
            simulation_input.inclination_deg,
            simulation_input.right_ascension_deg,
            simulation_input.mean_anomaly_deg,
        )
    )
    for forbidden_attribute in (
        "runtime_node",
        "registry",
        "node",
        "router",
        "compiler",
        "docker",
        "compose",
        "container_config",
        "namespace",
    ):
        assert not hasattr(simulation_input, forbidden_attribute)
    assert simulation_input is not entity
    assert not hasattr(entity, "orbit_parameters")
