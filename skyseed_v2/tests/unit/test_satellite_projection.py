from dataclasses import FrozenInstanceError, fields

import pytest
from seedemu.core import Node

from skyseed_v2.contracts import SatelliteDescriptor, SimulationNodeId
from skyseed_v2.domain import OrbitParameters, Satellite, Shell
from skyseed_v2.projection import SatelliteDescriptorProjector


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


def test_projector_reads_stable_identity_from_the_unique_satellite() -> None:
    entity = satellite()

    descriptor = SatelliteDescriptorProjector().project(entity)

    assert isinstance(descriptor, SatelliteDescriptor)
    assert descriptor.satellite_id == entity.satellite_id
    assert descriptor.node_id == SimulationNodeId(
        65000, "satellite", entity.satellite_id
    )
    assert descriptor.node_id.entity_id == descriptor.satellite_id
    assert descriptor.altitude_km == 550.0
    with pytest.raises(FrozenInstanceError):
        descriptor.satellite_id = "changed"


def test_descriptor_contains_contract_values_not_seed_or_container_objects() -> None:
    entity = satellite()
    descriptor = SatelliteDescriptorProjector().project(entity)

    assert {field.name for field in fields(descriptor)} == {
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
            descriptor.node_id,
            descriptor.satellite_id,
            descriptor.shell_id,
            descriptor.orbit_id,
            descriptor.orbit_index,
            descriptor.altitude_km,
            descriptor.inclination_deg,
            descriptor.right_ascension_deg,
            descriptor.mean_anomaly_deg,
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
        assert not hasattr(descriptor, forbidden_attribute)
    assert descriptor is not entity
