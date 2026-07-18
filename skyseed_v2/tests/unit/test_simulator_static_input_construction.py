import pytest
from seedemu.core import AutonomousSystem

from skyseed_v2.entities import GroundStationLocation, OrbitParameters
from skyseed_v2.seedemu_integration import SkyseedAutonomousSystem
from skyseed_v2.simulator_input import SimulatorStaticInput


def add_satellite(
    autonomous_system: SkyseedAutonomousSystem,
    entity_id: str,
):
    shell = autonomous_system.create_shell(f"shell-{autonomous_system.getAsn()}")
    orbit = shell.create_orbit(
        f"orbit-{autonomous_system.getAsn()}",
        parameters=OrbitParameters(550.0, 53.0, 15.0),
    )
    satellite = orbit.create_satellite(
        entity_id,
        orbit_index=0,
        mean_anomaly_deg=0.0,
    )
    return orbit, satellite


def add_ground_station(
    autonomous_system: SkyseedAutonomousSystem,
    entity_id: str,
):
    return autonomous_system.create_ground_station(
        entity_id,
        location=GroundStationLocation(40.0, -74.0, 10.0),
        station_type="gateway",
        operator="Skyseed",
    )


def multi_as_inputs():
    mixed_as = SkyseedAutonomousSystem(65001)
    add_satellite(mixed_as, "shared")
    add_ground_station(mixed_as, "shared")

    ground_station_as = SkyseedAutonomousSystem(65002)
    add_ground_station(ground_station_as, "shared")

    satellite_as = SkyseedAutonomousSystem(65003)
    add_satellite(satellite_as, "shared")
    return mixed_as, ground_station_as, satellite_as


def test_constructor_collects_mixed_entity_kinds_from_multiple_as() -> None:
    mixed_as, ground_station_as, satellite_as = multi_as_inputs()

    simulator_input = SimulatorStaticInput.from_autonomous_systems(
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
    assert simulator_input == SimulatorStaticInput.from_autonomous_systems(
        (mixed_as, ground_station_as, satellite_as)
    )


@pytest.mark.parametrize("entity_kind", ["satellite", "ground-station"])
def test_constructor_accepts_one_entity_kind_and_one_as(entity_kind: str) -> None:
    autonomous_system = SkyseedAutonomousSystem(65001)
    if entity_kind == "satellite":
        add_satellite(autonomous_system, "entity-1")
    else:
        add_ground_station(autonomous_system, "entity-1")

    simulator_input = SimulatorStaticInput.from_autonomous_systems(
        autonomous_system
    )

    assert len(simulator_input.list_nodes()) == 1
    assert simulator_input.list_nodes()[0].node_id.entity_kind == entity_kind


def test_constructor_ignores_ordinary_as_and_rejects_unknown_inputs() -> None:
    mixed_as, _, _ = multi_as_inputs()
    ordinary_as = AutonomousSystem(65004)

    simulator_input = SimulatorStaticInput.from_autonomous_systems(
        (ordinary_as, mixed_as)
    )

    assert len(simulator_input.list_nodes()) == 2
    assert all(
        node.node_id.asn == 65001 for node in simulator_input.list_nodes()
    )
    with pytest.raises(TypeError, match="AutonomousSystem"):
        SimulatorStaticInput.from_autonomous_systems((mixed_as, object()))


def test_static_input_is_a_snapshot_of_later_as_changes() -> None:
    autonomous_system = SkyseedAutonomousSystem(65001)
    orbit, original_satellite = add_satellite(autonomous_system, "satellite-0")
    simulator_input = SimulatorStaticInput.from_autonomous_systems(
        (autonomous_system,)
    )

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


def test_constructor_fails_closed_when_the_same_as_is_collected_twice() -> None:
    autonomous_system = SkyseedAutonomousSystem(65001)
    add_satellite(autonomous_system, "satellite-0")

    with pytest.raises(ValueError, match="duplicate SimulationNodeId"):
        SimulatorStaticInput.from_autonomous_systems(
            (autonomous_system, autonomous_system)
        )
