from dataclasses import FrozenInstanceError
from inspect import signature
from types import ModuleType
from typing import get_type_hints

import pytest

from skyseed_v2.contracts import (
    GroundStationDescriptor,
    RuntimeNodeReference,
    SatelliteDescriptor,
)
from skyseed_v2.contracts import ground_station, runtime, satellite
from skyseed_v2.projection import (
    GroundStationDescriptorProjector,
    SatelliteDescriptorProjector,
)


def test_runtime_node_reference_is_defined_only_by_shared_contract() -> None:
    assert RuntimeNodeReference is runtime.RuntimeNodeReference
    assert RuntimeNodeReference.__module__ == "skyseed_v2.contracts.runtime"
    assert "RuntimeNodeReference" not in satellite.__dict__
    assert "RuntimeNodeReference" not in ground_station.__dict__


def test_descriptors_use_the_same_shared_runtime_type() -> None:
    satellite_hints = get_type_hints(SatelliteDescriptor)
    ground_station_hints = get_type_hints(GroundStationDescriptor)

    assert satellite_hints["runtime_node"] is RuntimeNodeReference
    assert ground_station_hints["runtime_node"] is RuntimeNodeReference


def test_contract_modules_do_not_depend_on_each_other() -> None:
    satellite_dependencies = {
        value.__name__
        for value in satellite.__dict__.values()
        if isinstance(value, ModuleType)
    }
    ground_station_dependencies = {
        value.__name__
        for value in ground_station.__dict__.values()
        if isinstance(value, ModuleType)
    }
    assert "skyseed_v2.contracts.ground_station" not in satellite_dependencies
    assert "skyseed_v2.contracts.satellite" not in ground_station_dependencies
    assert "ground_station" not in satellite.__dict__
    assert "satellite" not in ground_station.__dict__


def test_shared_reference_and_both_descriptors_remain_immutable() -> None:
    runtime_node = RuntimeNodeReference(151, "rnode", "gs-1")
    satellite_descriptor = SatelliteDescriptor(
        satellite_id="satellite-0",
        shell_id="shell-1",
        orbit_id="orbit-1",
        orbit_index=0,
        altitude_km=550.0,
        inclination_deg=53.0,
        right_ascension_deg=15.0,
        mean_anomaly_deg=0.0,
        runtime_node=runtime_node,
    )
    ground_station_descriptor = GroundStationDescriptor(
        ground_station_id="gs-1",
        latitude_deg=40.0,
        longitude_deg=-74.0,
        elevation_m=None,
        station_type="gateway",
        operator="Skyseed",
        runtime_node=runtime_node,
    )

    with pytest.raises(FrozenInstanceError):
        runtime_node.node_name = "changed"
    with pytest.raises(FrozenInstanceError):
        satellite_descriptor.satellite_id = "changed"
    with pytest.raises(FrozenInstanceError):
        ground_station_descriptor.ground_station_id = "changed"


def test_projectors_accept_only_the_unique_entity() -> None:
    assert tuple(signature(SatelliteDescriptorProjector.project).parameters) == (
        "self",
        "satellite",
    )
    assert tuple(
        signature(GroundStationDescriptorProjector.project).parameters
    ) == ("self", "ground_station")
