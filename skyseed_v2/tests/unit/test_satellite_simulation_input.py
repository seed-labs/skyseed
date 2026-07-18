from dataclasses import FrozenInstanceError, fields

import pytest
from seedemu.core import Node

from skyseed_v2.application import create_satellite_simulation_input
from skyseed_v2.entities import OrbitParameters, Satellite, Shell
from skyseed_v2.simulator_input import SatelliteSimulationInput, SimulationNodeId


def satellite() -> Satellite:
    orbit = Shell("shell-1", asn=65000).create_orbit(
        "orbit-1",
        parameters=OrbitParameters(550.0, 53.0, 15.0),
    )
    return orbit.create_satellite(
        "satellite-0",
        orbit_index=0,
        mean_anomaly_deg=12.5,
    )


def test_input_reads_stable_identity_from_the_unique_satellite() -> None:
    entity = satellite()

    simulation_input = create_satellite_simulation_input(entity)

    assert isinstance(simulation_input, SatelliteSimulationInput)
    assert simulation_input.satellite_id == entity.satellite_id
    assert simulation_input.node_id == SimulationNodeId(
        65000, "satellite", entity.satellite_id
    )
    assert simulation_input.node_id.entity_id == simulation_input.satellite_id
    assert simulation_input.altitude_km == 550.0
    with pytest.raises(FrozenInstanceError):
        simulation_input.satellite_id = "changed"


def test_input_contains_only_static_values_not_seed_or_container_objects() -> None:
    entity = satellite()
    simulation_input = create_satellite_simulation_input(entity)

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
