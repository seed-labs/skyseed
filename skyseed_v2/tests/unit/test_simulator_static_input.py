from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timezone

import pytest

from skyseed_v2.shared import NodeId
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)


def satellite_input(
    asn: int,
    entity_id: str,
    *,
    mean_anomaly_deg: float = 0.0,
) -> SatelliteSimulationInput:
    return SatelliteSimulationInput(
        node_id=NodeId(asn, "satellite", entity_id),
        satellite_id=entity_id,
        shell_id=f"shell-{asn}",
        orbit_id=f"orbit-{asn}",
        orbit_index=0,
        altitude_km=550.0,
        inclination_deg=53.0,
        right_ascension_deg=15.0,
        mean_anomaly_deg=mean_anomaly_deg,
        epoch_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def ground_station_input(
    asn: int,
    entity_id: str,
) -> GroundStationSimulationInput:
    return GroundStationSimulationInput(
        node_id=NodeId(asn, "ground-station", entity_id),
        ground_station_id=entity_id,
        latitude_deg=40.0,
        longitude_deg=-74.0,
        elevation_m=10.0,
        station_type="gateway",
        operator="Skyseed",
    )


def test_static_input_is_immutable_and_returns_deterministic_input_tuples() -> None:
    satellite_2 = satellite_input(65003, "satellite-2")
    station = ground_station_input(65001, "gs-1")
    satellite_1 = satellite_input(65001, "satellite-1")
    simulator_input = SimulatorStaticInput(
        (satellite_2, satellite_1),
        (station,),
    )

    assert simulator_input.list_nodes() == (station, satellite_1, satellite_2)
    assert simulator_input.list_satellites() == (satellite_1, satellite_2)
    assert simulator_input.list_ground_stations() == (station,)
    assert simulator_input.get(satellite_1.node_id) is satellite_1
    assert simulator_input.get_satellite(satellite_1.node_id) is satellite_1
    assert simulator_input.get_ground_station(station.node_id) is station

    with pytest.raises(FrozenInstanceError):
        simulator_input._nodes = ()
    with pytest.raises(AttributeError):
        simulator_input.list_nodes().append(station)
    with pytest.raises(TypeError):
        simulator_input._by_node_id[satellite_1.node_id] = station


def test_static_input_queries_require_complete_node_ids_and_fail_closed(
) -> None:
    satellite_1 = satellite_input(65001, "shared")
    satellite_2 = satellite_input(65002, "shared")
    station = ground_station_input(65001, "shared")
    simulator_input = SimulatorStaticInput(
        (satellite_1, satellite_2),
        (station,),
    )

    with pytest.raises(KeyError, match="not found"):
        simulator_input.get(NodeId(65003, "satellite", "missing"))
    with pytest.raises(TypeError, match="NodeId"):
        simulator_input.get("shared")
    with pytest.raises(TypeError, match="not a satellite"):
        simulator_input.get_satellite(station.node_id)
    with pytest.raises(TypeError, match="not a ground-station"):
        simulator_input.get_ground_station(satellite_1.node_id)
    with pytest.raises(TypeError, match="NodeId"):
        simulator_input.get_ground_station("missing")
    with pytest.raises(TypeError, match="NodeId"):
        simulator_input.get_satellite("shared")
    with pytest.raises(TypeError, match="NodeId"):
        simulator_input.get_satellite(65001)


def test_static_input_is_equal_and_ordered_independently_of_input_traversal() -> None:
    satellites = (
        satellite_input(65003, "shared"),
        satellite_input(65001, "shared"),
    )
    stations = (
        ground_station_input(65002, "gs-2"),
        ground_station_input(65001, "shared"),
    )

    forward = SimulatorStaticInput(satellites, stations)
    reversed_input = SimulatorStaticInput(
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


def test_static_input_rejects_every_duplicate_logical_node_id_without_overwrite() -> None:
    original = satellite_input(65001, "satellite-0")
    changed = replace(original, mean_anomaly_deg=90.0)

    with pytest.raises(ValueError, match="duplicate NodeId"):
        SimulatorStaticInput((original, original))
    with pytest.raises(ValueError, match="duplicate NodeId"):
        SimulatorStaticInput((original, changed))


def test_static_input_rejects_non_input_values() -> None:
    with pytest.raises(TypeError, match="SatelliteSimulationInput"):
        SimulatorStaticInput((object(),))
    with pytest.raises(TypeError, match="GroundStationSimulationInput"):
        SimulatorStaticInput(ground_stations=(object(),))


def test_static_input_contains_only_logical_static_values() -> None:
    simulator_input = SimulatorStaticInput(
        (satellite_input(65001, "satellite-0"),),
        (ground_station_input(65001, "gs-1"),),
    )

    assert {field.name for field in fields(simulator_input)} == {
        "_satellites",
        "_ground_stations",
        "_nodes",
        "_by_node_id",
    }
    assert all(
        isinstance(simulation_input, (SatelliteSimulationInput, GroundStationSimulationInput))
        for simulation_input in simulator_input.list_nodes()
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
        assert not hasattr(simulator_input, forbidden_attribute)
