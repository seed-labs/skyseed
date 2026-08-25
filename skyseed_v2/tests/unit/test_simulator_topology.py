from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

import pytest

import skyseed_v2
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator import (
    GroundStation,
    Orbit,
    Satellite,
    Shell,
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    SkyseedAutonomousSystem,
    Topology,
)
from skyseed_v2.simulator.input import (
    SimulatorStaticInput,
    GroundStationSimulationInput,
    SatelliteSimulationInput,
)


def satellite_input(
    asn: int,
    satellite_id: str,
    shell_id: str,
    orbit_id: str,
    orbit_index: int,
    *,
    altitude_km: float = 550.0,
    inclination_deg: float = 53.0,
    right_ascension_deg: float = 15.0,
    mean_anomaly_deg: float = 0.0,
) -> SatelliteSimulationInput:
    return SatelliteSimulationInput(
        node_id=NodeId(asn, "satellite", satellite_id),
        satellite_id=satellite_id,
        shell_id=shell_id,
        orbit_id=orbit_id,
        orbit_index=orbit_index,
        altitude_km=altitude_km,
        inclination_deg=inclination_deg,
        right_ascension_deg=right_ascension_deg,
        mean_anomaly_deg=mean_anomaly_deg,
        epoch_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def ground_station_input(
    asn: int,
    station_id: str,
    *,
    operator: str | None = "Skyseed",
    max_simultaneous_satellite_accesses: int | None = None,
) -> GroundStationSimulationInput:
    return GroundStationSimulationInput(
        node_id=NodeId(asn, "ground-station", station_id),
        ground_station_id=station_id,
        latitude_deg=40.0,
        longitude_deg=-74.0,
        elevation_m=10.0,
        station_type="gateway",
        operator=operator,
        max_simultaneous_satellite_accesses=(
            max_simultaneous_satellite_accesses
        ),
    )


def definition_for(simulator_input: SimulatorStaticInput) -> SimulationDefinition:
    return SimulationDefinition(
        static_input=simulator_input,
        timeline=SimulationTimeline(
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(
                2026, 1, 1, 0, 0, 20, tzinfo=timezone.utc
            ),
            slice_interval=timedelta(seconds=10),
        ),
    )


def multi_hierarchy_input() -> SimulatorStaticInput:
    return SimulatorStaticInput(
        satellites=(
            satellite_input(65001, "sat-a0", "shell-a", "orbit-shared", 0),
            satellite_input(
                65001,
                "sat-a1",
                "shell-a",
                "orbit-second",
                0,
                right_ascension_deg=25.0,
            ),
            satellite_input(
                65001,
                "sat-b0",
                "shell-b",
                "orbit-shared",
                0,
                altitude_km=600.0,
                inclination_deg=60.0,
            ),
            satellite_input(65002, "sat-a0", "shell-a", "orbit-shared", 0),
        ),
        ground_stations=(
            ground_station_input(
                65001,
                "gs-shared",
                max_simultaneous_satellite_accesses=2,
            ),
            ground_station_input(
                65002,
                "gs-shared",
                max_simultaneous_satellite_accesses=5,
            ),
        ),
    )


def test_definition_is_immutable_typed_and_retains_exact_inputs() -> None:
    simulator_input = SimulatorStaticInput()
    timeline = SimulationTimeline(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        None,
        timedelta(seconds=10),
    )
    definition = SimulationDefinition(simulator_input, timeline)

    assert definition.static_input is simulator_input
    assert definition.timeline is timeline
    assert not hasattr(definition, "emulator_input")
    with pytest.raises(FrozenInstanceError):
        definition.timeline = timeline
    with pytest.raises(TypeError, match="static_input"):
        SimulationDefinition(object(), timeline)
    with pytest.raises(TypeError, match="unexpected keyword"):
        SimulationDefinition(emulator_input=simulator_input, timeline=timeline)
    with pytest.raises(TypeError, match="timeline"):
        SimulationDefinition(simulator_input, object())


def test_simulator_requires_explicit_single_atomic_topology_creation() -> None:
    definition = definition_for(multi_hierarchy_input())
    simulator = Simulator(definition)

    assert simulator.get_definition() is definition
    assert simulator.get_timeline() is definition.timeline
    with pytest.raises(RuntimeError, match="not been created"):
        simulator.get_topology()

    topology = simulator.create_topology()

    assert simulator.get_topology() is topology
    with pytest.raises(RuntimeError, match="already been created"):
        simulator.create_topology()


def test_topology_materializes_multi_as_shell_and_orbit_hierarchy() -> None:
    simulator_input = multi_hierarchy_input()
    topology = Simulator(definition_for(simulator_input)).create_topology()

    assert isinstance(topology, Topology)
    assert not hasattr(topology, "static_input")
    assert tuple(value.asn for value in topology.autonomous_systems) == (
        65001,
        65002,
    )
    first_as = topology.get_autonomous_system(65001)
    assert isinstance(first_as, SkyseedAutonomousSystem)
    assert tuple(shell.shell_id for shell in first_as.shells) == (
        "shell-a",
        "shell-b",
    )
    first_shell = topology.get_shell(65001, "shell-a")
    second_shell = topology.get_shell(65001, "shell-b")
    assert isinstance(first_shell, Shell)
    assert tuple(orbit.orbit_id for orbit in first_shell.orbits) == (
        "orbit-second",
        "orbit-shared",
    )
    assert first_shell.get_orbit("orbit-shared") is not second_shell.get_orbit(
        "orbit-shared"
    )

    satellite_id = NodeId(65001, "satellite", "sat-a0")
    satellite = topology.get_satellite(satellite_id)
    orbit = topology.get_orbit(65001, "shell-a", "orbit-shared")
    assert isinstance(satellite, Satellite)
    assert isinstance(orbit, Orbit)
    assert orbit.get_satellite(satellite_id) is satellite
    assert topology.get_node(satellite_id) is satellite
    assert orbit.altitude_km == 550.0
    assert orbit.inclination_deg == 53.0
    assert orbit.right_ascension_deg == 15.0
    assert orbit.epoch_utc == datetime(
        2026, 1, 1, tzinfo=timezone.utc
    )
    assert not hasattr(satellite, "altitude_km")
    assert not hasattr(satellite, "shell_id")

    source = simulator_input.get_satellite(satellite_id)
    assert satellite.node_id is source.node_id
    station_id = NodeId(65001, "ground-station", "gs-shared")
    station = topology.get_ground_station(station_id)
    assert isinstance(station, GroundStation)
    assert first_as.get_ground_station(station_id) is station
    assert topology.get_node(station_id) is station
    assert station.node_id is simulator_input.get_ground_station(station_id).node_id
    assert station.latitude_deg == 40.0
    assert station.longitude_deg == -74.0
    assert station.elevation_m == 10.0
    assert station.station_type == "gateway"
    assert station.operator == "Skyseed"
    assert station.max_simultaneous_satellite_accesses == 2
    assert (
        topology.get_ground_station(
            NodeId(65002, "ground-station", "gs-shared")
        ).max_simultaneous_satellite_accesses
        == 5
    )


def test_topology_collections_and_static_entities_are_immutable() -> None:
    topology = Simulator(definition_for(multi_hierarchy_input())).create_topology()
    autonomous_system = topology.get_autonomous_system(65001)
    shell = autonomous_system.get_shell("shell-a")
    orbit = shell.get_orbit("orbit-shared")
    satellite = orbit.satellites[0]
    ground_station = autonomous_system.ground_stations[0]

    assert isinstance(topology.autonomous_systems, tuple)
    assert isinstance(autonomous_system.shells, tuple)
    assert isinstance(autonomous_system.ground_stations, tuple)
    assert isinstance(shell.orbits, tuple)
    assert isinstance(orbit.satellites, tuple)
    immutable_indexes = (
        topology._autonomous_systems_by_asn,
        topology._nodes_by_id,
        autonomous_system._shells_by_id,
        autonomous_system._ground_stations_by_node_id,
        shell._orbits_by_id,
        orbit._satellites_by_node_id,
    )
    assert all(isinstance(index, MappingProxyType) for index in immutable_indexes)
    assert topology._autonomous_systems_by_asn[65001] is autonomous_system
    assert topology._nodes_by_id[satellite.node_id] is satellite
    assert topology._nodes_by_id[ground_station.node_id] is ground_station
    assert autonomous_system._shells_by_id[shell.shell_id] is shell
    assert (
        autonomous_system._ground_stations_by_node_id[ground_station.node_id]
        is ground_station
    )
    assert shell._orbits_by_id[orbit.orbit_id] is orbit
    assert orbit._satellites_by_node_id[satellite.node_id] is satellite
    for index in immutable_indexes:
        with pytest.raises(TypeError):
            index[object()] = object()
    assert {field.name for field in fields(topology)} == {
        "_autonomous_systems",
        "_autonomous_systems_by_asn",
        "_nodes_by_id",
        "_zone_layout",
    }
    for removed_index in (
        "_satellites_by_id",
        "_ground_stations_by_id",
        "_shells_by_key",
        "_orbits_by_key",
        "_shells_by_satellite_id",
        "_orbits_by_satellite_id",
    ):
        assert not hasattr(topology, removed_index)
    with pytest.raises(AttributeError):
        orbit.satellites.append(satellite)
    with pytest.raises(FrozenInstanceError):
        satellite.orbit_index = 9
    with pytest.raises(FrozenInstanceError):
        orbit._satellites = ()
    with pytest.raises(FrozenInstanceError):
        topology._autonomous_systems = ()


def test_topology_preserves_nullable_ground_station_operator() -> None:
    simulation_input = SimulatorStaticInput(
        ground_stations=(
            ground_station_input(65001, "gs-no-known-operator", operator=None),
        )
    )

    station = Simulator(definition_for(simulation_input)).create_topology().get_ground_station(
        NodeId(65001, "ground-station", "gs-no-known-operator")
    )
    assert station.operator is None
    assert station.max_simultaneous_satellite_accesses is None


@pytest.mark.parametrize("capability", (0, -1, True))
def test_topology_ground_station_rejects_invalid_access_capability(
    capability: int,
) -> None:
    with pytest.raises(
        ValueError, match="max_simultaneous_satellite_accesses"
    ):
        GroundStation(
            node_id=NodeId(65001, "ground-station", "invalid-capability"),
            latitude_deg=40.0,
            longitude_deg=-74.0,
            elevation_m=10.0,
            station_type="gateway",
            operator=None,
            max_simultaneous_satellite_accesses=capability,
        )


def test_topology_facade_fails_closed_on_wrong_kinds_types_and_missing_values() -> None:
    topology = Simulator(definition_for(multi_hierarchy_input())).create_topology()
    satellite_id = NodeId(65001, "satellite", "sat-a0")
    station_id = NodeId(65001, "ground-station", "gs-shared")

    with pytest.raises(TypeError, match="NodeId"):
        topology.get_node("sat-a0")
    with pytest.raises(TypeError, match="satellite"):
        topology.get_satellite(station_id)
    with pytest.raises(TypeError, match="ground-station"):
        topology.get_ground_station(satellite_id)
    with pytest.raises(KeyError, match="Node not found"):
        topology.get_node(NodeId(65001, "satellite", "missing"))
    with pytest.raises(KeyError, match="Shell not found"):
        topology.get_shell(65001, "missing")
    with pytest.raises(KeyError, match="Orbit not found"):
        topology.get_orbit(65001, "shell-a", "missing")
    with pytest.raises(ValueError, match="asn"):
        topology.get_autonomous_system(0)


def test_topology_shell_and_orbit_queries_delegate_to_their_owners(
    monkeypatch,
) -> None:
    topology = Simulator(definition_for(multi_hierarchy_input())).create_topology()
    calls: list[tuple[str, str]] = []
    original_get_shell = SkyseedAutonomousSystem.get_shell
    original_get_orbit = Shell.get_orbit

    def tracked_get_shell(
        autonomous_system: SkyseedAutonomousSystem,
        shell_id: str,
    ) -> Shell:
        calls.append(("shell", shell_id))
        return original_get_shell(autonomous_system, shell_id)

    def tracked_get_orbit(shell: Shell, orbit_id: str) -> Orbit:
        calls.append(("orbit", orbit_id))
        return original_get_orbit(shell, orbit_id)

    monkeypatch.setattr(SkyseedAutonomousSystem, "get_shell", tracked_get_shell)
    monkeypatch.setattr(Shell, "get_orbit", tracked_get_orbit)

    assert topology.get_shell(65001, "shell-a").shell_id == "shell-a"
    assert (
        topology.get_orbit(65001, "shell-a", "orbit-shared").orbit_id
        == "orbit-shared"
    )
    assert calls == [
        ("shell", "shell-a"),
        ("shell", "shell-a"),
        ("orbit", "orbit-shared"),
    ]


def test_future_satellite_parent_queries_are_not_public() -> None:
    topology = Simulator(definition_for(multi_hierarchy_input())).create_topology()

    assert not hasattr(topology, "get_shell_for_satellite")
    assert not hasattr(topology, "get_orbit_for_satellite")


def test_conflicting_orbit_parameters_fail_without_publishing_a_topology() -> None:
    simulator_input = SimulatorStaticInput(
        satellites=(
            satellite_input(65001, "sat-0", "shell", "orbit", 0),
            satellite_input(
                65001,
                "sat-1",
                "shell",
                "orbit",
                1,
                altitude_km=551.0,
            ),
        )
    )
    simulator = Simulator(definition_for(simulator_input))

    with pytest.raises(ValueError, match="conflicting Orbit parameters"):
        simulator.create_topology()
    with pytest.raises(RuntimeError, match="not been created"):
        simulator.get_topology()


def test_duplicate_orbit_local_index_fails_atomically() -> None:
    simulator_input = SimulatorStaticInput(
        satellites=(
            satellite_input(65001, "sat-0", "shell", "orbit", 0),
            satellite_input(65001, "sat-1", "shell", "orbit", 0),
        )
    )
    simulator = Simulator(definition_for(simulator_input))

    with pytest.raises(ValueError, match="duplicate orbit_index"):
        simulator.create_topology()
    with pytest.raises(RuntimeError, match="not been created"):
        simulator.get_topology()


def test_boundary_identity_prevents_one_satellite_from_having_two_parents() -> None:
    first = satellite_input(65001, "sat-0", "shell-a", "orbit-a", 0)
    second = replace(first, shell_id="shell-b", orbit_id="orbit-b")

    with pytest.raises(ValueError, match="duplicate NodeId"):
        SimulatorStaticInput(satellites=(first, second))


def test_invalid_parent_identity_fails_without_publishing_a_topology() -> None:
    invalid = satellite_input(65001, "sat-0", "bad/shell", "orbit", 0)
    simulator = Simulator(definition_for(SimulatorStaticInput((invalid,))))

    with pytest.raises(ValueError, match="shell_id"):
        simulator.create_topology()
    with pytest.raises(RuntimeError, match="not been created"):
        simulator.get_topology()


def test_simulator_types_are_not_added_to_the_package_root() -> None:
    for name in (
        "SimulationDefinition",
        "SimulationTimeline",
        "Simulator",
        "SkyseedAutonomousSystem",
        "Topology",
    ):
        assert not hasattr(skyseed_v2, name)


def test_simulator_rejects_an_untyped_definition() -> None:
    with pytest.raises(TypeError, match="SimulationDefinition"):
        Simulator(object())


def test_public_hierarchy_constructors_do_not_create_parallel_owner_trees() -> None:
    for class_type in (Orbit, Shell, SkyseedAutonomousSystem, Topology):
        with pytest.raises(TypeError):
            class_type()
