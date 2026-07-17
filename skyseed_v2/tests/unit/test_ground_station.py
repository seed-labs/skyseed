from dataclasses import FrozenInstanceError, fields

import pytest
from seedemu.core import Router

import skyseed_v2
from skyseed_v2.contracts import GroundStationDescriptor, SimulationNodeId
from skyseed_v2.domain import GroundStation, GroundStationLocation
from skyseed_v2.projection import GroundStationDescriptorProjector
from skyseed_v2.seed import SkyseedAutonomousSystem


def create_station() -> GroundStation:
    return GroundStation(
        ground_station_id="gs-1",
        asn=151,
        location=GroundStationLocation(40.0, -74.0, 12.5),
        station_type="gateway",
        operator="Skyseed",
        display_name="Ground Station One",
        service_config={"profile": "minimal"},
    )


def test_ground_station_is_the_unique_router_object_with_static_fields() -> None:
    station = create_station()

    assert isinstance(station, Router)
    assert station.ground_station_id == "gs-1"
    assert station.location == GroundStationLocation(40.0, -74.0, 12.5)
    assert station.station_type == "gateway"
    assert station.operator == "Skyseed"
    assert station.getDisplayName() == "Ground Station One"
    assert dict(station.service_config) == {"profile": "minimal"}
    with pytest.raises(TypeError):
        station.service_config["profile"] = "changed"
    assert not hasattr(skyseed_v2, "GroundStationNode")


def test_ground_station_as_rejects_duplicate_stable_ids() -> None:
    station_as = SkyseedAutonomousSystem(151)
    location = GroundStationLocation(40.0, -74.0)
    created = station_as.create_ground_station(
        "gs-1",
        location=location,
        station_type="gateway",
        operator="Skyseed",
    )

    assert station_as.get_ground_station("gs-1") is created
    assert station_as.getRouter("gs-1") is created
    with pytest.raises(ValueError, match="already exists"):
        station_as.create_ground_station(
            "gs-1",
            location=location,
            station_type="gateway",
            operator="Skyseed",
        )
    with pytest.raises(ValueError, match="already exists"):
        station_as.createRouter("gs-1")


def test_ground_station_descriptor_is_immutable_and_restricted() -> None:
    station = create_station()
    descriptor = GroundStationDescriptorProjector().project(station)

    assert descriptor == GroundStationDescriptor(
        node_id=SimulationNodeId(151, "ground-station", "gs-1"),
        ground_station_id="gs-1",
        latitude_deg=40.0,
        longitude_deg=-74.0,
        elevation_m=12.5,
        station_type="gateway",
        operator="Skyseed",
    )
    with pytest.raises(FrozenInstanceError):
        descriptor.operator = "changed"
    assert not isinstance(descriptor.node_id, Router)
    assert descriptor.node_id.entity_id == descriptor.ground_station_id
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
    assert {field.name for field in fields(descriptor)} == {
        "node_id",
        "ground_station_id",
        "latitude_deg",
        "longitude_deg",
        "elevation_m",
        "station_type",
        "operator",
    }


def test_ground_station_has_no_simulator_dynamic_responsibilities() -> None:
    station = create_station()
    for responsibility in (
        "compute_visibility",
        "select_service_satellite",
        "assign_zone",
        "build_topology_slice",
        "plan_routes",
        "plan_bpf_updates",
        "run_backend_plan",
    ):
        assert not hasattr(station, responsibility)
