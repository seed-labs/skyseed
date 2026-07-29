from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from inspect import signature
from typing import get_type_hints

import pytest

import skyseed_v2
from skyseed_v2.emulator.entities import (
    GroundStation,
    GroundStationLocation,
    Orbit,
    OrbitParameters,
    Satellite,
)
from skyseed_v2.emulator_to_simulator import (
    create_ground_station_simulation_input,
    create_satellite_simulation_input,
)
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator.input import (
    GroundStationSimulationInput,
    SatelliteSimulationInput,
    SimulatorStaticInput,
)
from skyseed_v2.emulator.seedemu import (
    SkyseedAutonomousSystem,
)
from seedemu.layers import Base


def test_node_id_is_stable_hashable_and_backend_independent() -> None:
    identity = NodeId(65001, "satellite", "shared-id")

    assert not hasattr(skyseed_v2, "NodeId")
    assert not hasattr(skyseed_v2, "SimulationNodeId")
    assert identity.value == "65001/satellite/shared-id"
    assert hash(identity) == hash(NodeId(65001, "satellite", "shared-id"))
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
        "resolve",
        "get_entity",
        "get_container",
    ):
        assert not hasattr(identity, forbidden)
    with pytest.raises(FrozenInstanceError):
        identity.entity_id = "changed"


def test_node_id_kind_and_asn_participate_in_identity() -> None:
    satellite = NodeId(65001, "satellite", "shared-id")
    station = NodeId(65001, "ground-station", "shared-id")
    other_as = NodeId(65002, "satellite", "shared-id")

    assert satellite != station
    assert satellite != other_as
    assert len({satellite, station, other_as}) == 3


@pytest.mark.parametrize("asn", [0, -1, True, 1.5, "65001"])
def test_node_id_rejects_invalid_asn(asn) -> None:
    with pytest.raises(ValueError, match="asn"):
        NodeId(asn, "satellite", "satellite-0")


@pytest.mark.parametrize("kind", ["", "satnode", "rnode", "router", None, []])
def test_node_id_rejects_unapproved_kind(kind) -> None:
    with pytest.raises(ValueError, match="entity_kind"):
        NodeId(65001, kind, "shared-id")


@pytest.mark.parametrize("entity_id", ["", " invalid", "bad/id", None])
def test_node_id_rejects_invalid_entity_id(entity_id) -> None:
    with pytest.raises(ValueError, match="entity_id"):
        NodeId(65001, "satellite", entity_id)


def test_simulation_inputs_use_the_shared_logical_identity() -> None:
    satellite_hints = get_type_hints(SatelliteSimulationInput)
    ground_station_hints = get_type_hints(GroundStationSimulationInput)

    assert satellite_hints["node_id"] is NodeId
    assert ground_station_hints["node_id"] is NodeId


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
        "epoch_utc": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    ground_station_values = {
        "ground_station_id": "gs-1",
        "latitude_deg": 40.0,
        "longitude_deg": -74.0,
        "elevation_m": 0.0,
        "station_type": "gateway",
        "operator": "Skyseed",
    }

    with pytest.raises(ValueError, match="satellite"):
        SatelliteSimulationInput(
            node_id=NodeId(65001, "ground-station", "satellite-0"),
            **satellite_values,
        )
    with pytest.raises(ValueError, match="satellite_id"):
        SatelliteSimulationInput(
            node_id=NodeId(65001, "satellite", "satellite-1"),
            **satellite_values,
        )
    with pytest.raises(ValueError, match="ground-station"):
        GroundStationSimulationInput(
            node_id=NodeId(65001, "satellite", "gs-1"),
            **ground_station_values,
        )
    with pytest.raises(ValueError, match="ground_station_id"):
        GroundStationSimulationInput(
            node_id=NodeId(65001, "ground-station", "gs-2"),
            **ground_station_values,
        )


def test_input_generators_accept_only_the_data_needed_for_each_snapshot() -> None:
    assert tuple(
        signature(create_satellite_simulation_input).parameters
    ) == (
        "satellite",
        "orbit_parameters",
    )
    assert tuple(
        signature(create_ground_station_simulation_input).parameters
    ) == ("ground_station",)
    assert not hasattr(SatelliteSimulationInput, "from_satellite")
    assert not hasattr(GroundStationSimulationInput, "from_ground_station")
    assert not hasattr(SimulatorStaticInput, "from_autonomous_systems")


def test_emulator_entities_do_not_hold_or_accept_node_id() -> None:
    assert not hasattr(Satellite, "node_id")
    assert not hasattr(GroundStation, "node_id")
    assert "node_id" not in signature(Satellite).parameters
    assert "node_id" not in signature(GroundStation).parameters
    assert "node_id" not in signature(Orbit.create_satellite).parameters
    assert "node_id" not in signature(
        SkyseedAutonomousSystem.create_ground_station
    ).parameters


def test_existing_owners_provide_narrow_lookup_without_a_global_index() -> None:
    base = Base()
    autonomous_system = SkyseedAutonomousSystem(65001)
    base.setAutonomousSystem(autonomous_system)
    shell = autonomous_system.create_shell("shell-1")
    orbit = shell.create_orbit(
        "orbit-1",
        parameters=OrbitParameters(
            550.0, 53.0, 15.0, datetime(2026, 1, 1, tzinfo=timezone.utc)
        ),
    )
    satellite = orbit.create_satellite(
        "shared",
        orbit_index=0,
        mean_anomaly_deg=0.0,
    )
    ground_station = autonomous_system.create_ground_station(
        "shared",
        location=GroundStationLocation(40.0, -74.0),
        station_type="gateway",
        operator="Skyseed",
    )

    assert base.getAutonomousSystem(65001) is autonomous_system
    assert autonomous_system.get_shell("shell-1") is shell
    assert shell.get_orbit("orbit-1") is orbit
    assert orbit.get_satellite("shared") is satellite
    assert autonomous_system.get_satellite("shared") is satellite
    assert autonomous_system.get_ground_station("shared") is ground_station

    with pytest.raises(AssertionError, match="does not exist"):
        base.getAutonomousSystem(65002)
    with pytest.raises(KeyError, match="does not exist"):
        autonomous_system.get_shell("missing")
    with pytest.raises(KeyError, match="does not exist"):
        autonomous_system.get_satellite("missing")
    with pytest.raises(KeyError, match="does not exist"):
        autonomous_system.get_ground_station("missing")
    with pytest.raises(KeyError, match="does not exist"):
        shell.get_orbit("missing")
    with pytest.raises(KeyError, match="does not exist"):
        orbit.get_satellite("missing")

    for owner in (base, autonomous_system, shell, orbit):
        for forbidden_lookup in (
            "get_node",
            "resolve_node",
            "resolve_resource",
            "find_resource",
        ):
            assert not hasattr(owner, forbidden_lookup)
