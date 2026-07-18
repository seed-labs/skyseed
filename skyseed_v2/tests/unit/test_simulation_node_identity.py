from dataclasses import FrozenInstanceError, fields
from inspect import signature
from typing import get_type_hints

import pytest

import skyseed_v2
from skyseed_v2.simulator_input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulationNodeId,
)


def test_simulation_node_id_is_stable_hashable_and_backend_independent() -> None:
    identity = SimulationNodeId(65001, "satellite", "shared-id")

    assert skyseed_v2.SimulationNodeId is SimulationNodeId
    assert identity.value == "65001/satellite/shared-id"
    assert hash(identity) == hash(SimulationNodeId(65001, "satellite", "shared-id"))
    assert {field.name for field in fields(identity)} == {
        "asn",
        "entity_kind",
        "entity_id",
    }
    for forbidden in (
        "registry_type",
        "node_name",
        "container_name",
        "namespace",
        "compose_service",
    ):
        assert not hasattr(identity, forbidden)
    with pytest.raises(FrozenInstanceError):
        identity.entity_id = "changed"


def test_simulation_node_id_kind_and_asn_participate_in_identity() -> None:
    satellite = SimulationNodeId(65001, "satellite", "shared-id")
    station = SimulationNodeId(65001, "ground-station", "shared-id")
    other_as = SimulationNodeId(65002, "satellite", "shared-id")

    assert satellite != station
    assert satellite != other_as
    assert len({satellite, station, other_as}) == 3


@pytest.mark.parametrize("asn", [0, -1, True, 1.5, "65001"])
def test_simulation_node_id_rejects_invalid_asn(asn) -> None:
    with pytest.raises(ValueError, match="asn"):
        SimulationNodeId(asn, "satellite", "satellite-0")


@pytest.mark.parametrize("kind", ["", "satnode", "rnode", "router", None, []])
def test_simulation_node_id_rejects_unapproved_kind(kind) -> None:
    with pytest.raises(ValueError, match="entity_kind"):
        SimulationNodeId(65001, kind, "shared-id")


@pytest.mark.parametrize("entity_id", ["", " invalid", "bad/id", None])
def test_simulation_node_id_rejects_invalid_entity_id(entity_id) -> None:
    with pytest.raises(ValueError, match="entity_id"):
        SimulationNodeId(65001, "satellite", entity_id)


def test_simulation_inputs_use_the_shared_logical_identity() -> None:
    satellite_hints = get_type_hints(SatelliteSimulationInput)
    ground_station_hints = get_type_hints(GroundStationSimulationInput)

    assert satellite_hints["node_id"] is SimulationNodeId
    assert ground_station_hints["node_id"] is SimulationNodeId


def test_simulation_inputs_reject_mismatched_logical_identities() -> None:
    satellite_values = {
        "satellite_id": "satellite-0",
        "shell_id": "shell-1",
        "orbit_id": "orbit-1",
        "orbit_index": 0,
        "altitude_km": 550.0,
        "inclination_deg": 53.0,
        "right_ascension_deg": 15.0,
        "mean_anomaly_deg": 0.0,
    }
    ground_station_values = {
        "ground_station_id": "gs-1",
        "latitude_deg": 40.0,
        "longitude_deg": -74.0,
        "elevation_m": None,
        "station_type": "gateway",
        "operator": "Skyseed",
    }

    with pytest.raises(ValueError, match="satellite"):
        SatelliteSimulationInput(
            node_id=SimulationNodeId(65001, "ground-station", "satellite-0"),
            **satellite_values,
        )
    with pytest.raises(ValueError, match="satellite_id"):
        SatelliteSimulationInput(
            node_id=SimulationNodeId(65001, "satellite", "satellite-1"),
            **satellite_values,
        )
    with pytest.raises(ValueError, match="ground-station"):
        GroundStationSimulationInput(
            node_id=SimulationNodeId(65001, "satellite", "gs-1"),
            **ground_station_values,
        )
    with pytest.raises(ValueError, match="ground_station_id"):
        GroundStationSimulationInput(
            node_id=SimulationNodeId(65001, "ground-station", "gs-2"),
            **ground_station_values,
        )


def test_input_constructors_accept_only_the_unique_entity() -> None:
    assert tuple(
        signature(SatelliteSimulationInput.from_satellite).parameters
    ) == (
        "satellite",
    )
    assert tuple(
        signature(GroundStationSimulationInput.from_ground_station).parameters
    ) == ("ground_station",)
