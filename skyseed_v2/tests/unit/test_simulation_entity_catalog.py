from dataclasses import FrozenInstanceError, fields, replace

import pytest

from skyseed_v2.contracts import (
    GroundStationDescriptor,
    SatelliteDescriptor,
    SimulationEntityCatalog,
    SimulationNodeId,
)


def satellite_descriptor(
    asn: int,
    entity_id: str,
    *,
    mean_anomaly_deg: float = 0.0,
) -> SatelliteDescriptor:
    return SatelliteDescriptor(
        node_id=SimulationNodeId(asn, "satellite", entity_id),
        satellite_id=entity_id,
        shell_id=f"shell-{asn}",
        orbit_id=f"orbit-{asn}",
        orbit_index=0,
        altitude_km=550.0,
        inclination_deg=53.0,
        right_ascension_deg=15.0,
        mean_anomaly_deg=mean_anomaly_deg,
    )


def ground_station_descriptor(
    asn: int,
    entity_id: str,
) -> GroundStationDescriptor:
    return GroundStationDescriptor(
        node_id=SimulationNodeId(asn, "ground-station", entity_id),
        ground_station_id=entity_id,
        latitude_deg=40.0,
        longitude_deg=-74.0,
        elevation_m=10.0,
        station_type="gateway",
        operator="Skyseed",
    )


def test_catalog_is_immutable_and_returns_deterministic_descriptor_tuples() -> None:
    satellite_2 = satellite_descriptor(65003, "satellite-2")
    station = ground_station_descriptor(65001, "gs-1")
    satellite_1 = satellite_descriptor(65001, "satellite-1")
    catalog = SimulationEntityCatalog(
        (satellite_2, satellite_1),
        (station,),
    )

    assert catalog.list_nodes() == (station, satellite_1, satellite_2)
    assert catalog.list_satellites() == (satellite_1, satellite_2)
    assert catalog.list_ground_stations() == (station,)
    assert catalog.get(satellite_1.node_id) is satellite_1
    assert catalog.get_satellite(satellite_1.node_id) is satellite_1
    assert catalog.get_satellite("satellite-1") is satellite_1
    assert catalog.get_ground_station(station.node_id) is station
    assert catalog.get_ground_station("gs-1") is station

    with pytest.raises(FrozenInstanceError):
        catalog._nodes = ()
    with pytest.raises(AttributeError):
        catalog.list_nodes().append(station)
    with pytest.raises(TypeError):
        catalog._by_node_id[satellite_1.node_id] = station


def test_catalog_queries_fail_explicitly_for_missing_mismatched_or_ambiguous_ids(
) -> None:
    satellite_1 = satellite_descriptor(65001, "shared")
    satellite_2 = satellite_descriptor(65002, "shared")
    station = ground_station_descriptor(65001, "shared")
    catalog = SimulationEntityCatalog(
        (satellite_1, satellite_2),
        (station,),
    )

    with pytest.raises(KeyError, match="not found"):
        catalog.get(SimulationNodeId(65003, "satellite", "missing"))
    with pytest.raises(TypeError, match="SimulationNodeId"):
        catalog.get("shared")
    with pytest.raises(TypeError, match="not a satellite"):
        catalog.get_satellite(station.node_id)
    with pytest.raises(TypeError, match="not a ground-station"):
        catalog.get_ground_station(satellite_1.node_id)
    with pytest.raises(KeyError, match="not found"):
        catalog.get_ground_station("missing")
    with pytest.raises(ValueError, match="ambiguous"):
        catalog.get_satellite("shared")
    with pytest.raises(TypeError, match="SimulationNodeId or string"):
        catalog.get_satellite(65001)


def test_catalog_is_equal_and_ordered_independently_of_input_traversal() -> None:
    satellites = (
        satellite_descriptor(65003, "shared"),
        satellite_descriptor(65001, "shared"),
    )
    stations = (
        ground_station_descriptor(65002, "gs-2"),
        ground_station_descriptor(65001, "shared"),
    )

    forward = SimulationEntityCatalog(satellites, stations)
    reversed_input = SimulationEntityCatalog(
        reversed(satellites),
        reversed(stations),
    )

    assert forward == reversed_input
    assert tuple(node.node_id.value for node in forward.list_nodes()) == (
        "65001/ground-station/shared",
        "65001/satellite/shared",
        "65002/ground-station/gs-2",
        "65003/satellite/shared",
    )


def test_catalog_rejects_every_duplicate_logical_node_id_without_overwrite() -> None:
    original = satellite_descriptor(65001, "satellite-0")
    changed = replace(original, mean_anomaly_deg=90.0)

    with pytest.raises(ValueError, match="duplicate SimulationNodeId"):
        SimulationEntityCatalog((original, original))
    with pytest.raises(ValueError, match="duplicate SimulationNodeId"):
        SimulationEntityCatalog((original, changed))


def test_catalog_rejects_non_descriptor_values() -> None:
    with pytest.raises(TypeError, match="SatelliteDescriptor"):
        SimulationEntityCatalog((object(),))
    with pytest.raises(TypeError, match="GroundStationDescriptor"):
        SimulationEntityCatalog(ground_stations=(object(),))


def test_catalog_contains_only_logical_contract_values() -> None:
    catalog = SimulationEntityCatalog(
        (satellite_descriptor(65001, "satellite-0"),),
        (ground_station_descriptor(65001, "gs-1"),),
    )

    assert {field.name for field in fields(catalog)} == {
        "_satellites",
        "_ground_stations",
        "_nodes",
        "_by_node_id",
    }
    assert all(
        isinstance(descriptor, (SatelliteDescriptor, GroundStationDescriptor))
        for descriptor in catalog.list_nodes()
    )
    for forbidden_attribute in (
        "emulator",
        "autonomous_system",
        "registry",
        "compiler",
        "docker",
        "compose",
        "container",
        "namespace",
    ):
        assert not hasattr(catalog, forbidden_attribute)
