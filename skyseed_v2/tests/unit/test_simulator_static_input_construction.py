from datetime import datetime, timezone

import pytest
from seedemu.core import AutonomousSystem

from skyseed_v2.emulator.entities import GroundStationLocation, OrbitParameters
from skyseed_v2.emulator.seedemu import EmulatorAutonomousSystem
from skyseed_v2.adapters.emulator_to_simulator import create_simulator_static_input


def add_satellite(
    autonomous_system: EmulatorAutonomousSystem,
    entity_id: str,
):
    shell = autonomous_system.create_shell(f"shell-{autonomous_system.getAsn()}")
    orbit = shell.create_orbit(
        f"orbit-{autonomous_system.getAsn()}",
        parameters=OrbitParameters(
            550.0, 53.0, 15.0, datetime(2026, 1, 1, tzinfo=timezone.utc)
        ),
    )
    satellite = orbit.create_satellite(
        entity_id,
        orbit_index=0,
        mean_anomaly_deg=0.0,
    )
    return orbit, satellite


def add_ground_station(
    autonomous_system: EmulatorAutonomousSystem,
    entity_id: str,
):
    return autonomous_system.create_ground_station(
        entity_id,
        location=GroundStationLocation(40.0, -74.0, 10.0),
        station_type="gateway",
        operator="Skyseed",
    )


def multi_as_inputs():
    mixed_as = EmulatorAutonomousSystem(65001)
    add_satellite(mixed_as, "shared")
    add_ground_station(mixed_as, "shared")

    ground_station_as = EmulatorAutonomousSystem(65002)
    add_ground_station(ground_station_as, "shared")

    satellite_as = EmulatorAutonomousSystem(65003)
    add_satellite(satellite_as, "shared")
    return mixed_as, ground_station_as, satellite_as


def test_static_input_generation_collects_mixed_kinds_from_multiple_as() -> None:
    mixed_as, ground_station_as, satellite_as = multi_as_inputs()

    simulator_input = create_simulator_static_input(
        (satellite_as, mixed_as, ground_station_as)
    )

    assert tuple(node.node_id.value for node in simulator_input.list_nodes()) == (
        "65001/ground-station/shared",
        "65001/satellite/shared",
        "65002/ground-station/shared",
        "65003/satellite/shared",
    )
    assert len(simulator_input.list_satellites()) == 2
    assert len(simulator_input.list_ground_stations()) == 2
    assert simulator_input == create_simulator_static_input(
        (mixed_as, ground_station_as, satellite_as)
    )


@pytest.mark.parametrize("entity_kind", ["satellite", "ground-station"])
def test_static_input_generation_accepts_one_entity_kind_and_one_as(
    entity_kind: str,
) -> None:
    autonomous_system = EmulatorAutonomousSystem(65001)
    if entity_kind == "satellite":
        add_satellite(autonomous_system, "entity-1")
    else:
        add_ground_station(autonomous_system, "entity-1")

    simulator_input = create_simulator_static_input((autonomous_system,))

    assert len(simulator_input.list_nodes()) == 1
    assert simulator_input.list_nodes()[0].node_id.entity_kind == entity_kind


def test_static_input_generation_rejects_ordinary_as_and_unknown_inputs() -> None:
    mixed_as, _, _ = multi_as_inputs()
    ordinary_as = AutonomousSystem(65004)

    with pytest.raises(TypeError, match="only EmulatorAutonomousSystem"):
        create_simulator_static_input((ordinary_as, mixed_as))
    with pytest.raises(TypeError, match="only EmulatorAutonomousSystem"):
        create_simulator_static_input((mixed_as, object()))
    with pytest.raises(TypeError, match="iterable"):
        create_simulator_static_input(mixed_as)


def test_static_input_is_a_snapshot_of_later_as_changes() -> None:
    autonomous_system = EmulatorAutonomousSystem(65001)
    orbit, original_satellite = add_satellite(autonomous_system, "satellite-0")
    simulator_input = create_simulator_static_input((autonomous_system,))

    orbit.create_satellite(
        "satellite-1",
        orbit_index=1,
        mean_anomaly_deg=90.0,
    )
    add_ground_station(autonomous_system, "gs-1")

    assert tuple(
        item.satellite_id for item in simulator_input.list_satellites()
    ) == (original_satellite.satellite_id,)
    assert simulator_input.list_ground_stations() == ()


def test_aggregate_fails_closed_when_the_same_as_is_collected_twice() -> None:
    autonomous_system = EmulatorAutonomousSystem(65001)
    add_satellite(autonomous_system, "satellite-0")

    with pytest.raises(ValueError, match="duplicate NodeId"):
        create_simulator_static_input((autonomous_system, autonomous_system))


def test_generation_fails_closed_on_an_unsupported_owned_object() -> None:
    autonomous_system = EmulatorAutonomousSystem(65001)
    autonomous_system._shells["unsupported"] = object()

    with pytest.raises(TypeError, match="unsupported EmulatorShell"):
        create_simulator_static_input((autonomous_system,))


def test_satellite_lookup_short_circuits_the_actual_owner(monkeypatch):
    system = EmulatorAutonomousSystem(65001)
    _, satellite = add_satellite(system, "first")

    def forbidden(*args):
        raise AssertionError("lookup materialized the AS satellite tuple")

    monkeypatch.setattr(system, "satellites", forbidden)
    assert system.get_satellite("first") is satellite
    with pytest.raises(KeyError, match="missing"):
        system.get_satellite("missing")


def test_projection_rejects_a_satellite_under_the_wrong_parent():
    system = EmulatorAutonomousSystem(65001)
    orbit, _ = add_satellite(system, "first")
    foreign = EmulatorAutonomousSystem(65002)
    _, satellite = add_satellite(foreign, "foreign")
    orbit._satellites[satellite.satellite_id] = satellite
    with pytest.raises(ValueError, match="owning AS/Shell/Orbit"):
        create_simulator_static_input((system,))
