"""One shared physical inventory, explicit scoped Models and distinct destinations."""
from tests._support import MODEL_TIMESTAMP
from datetime import timedelta
import pytest
from examples.Sky05_five_shells import five_shells
from examples.Sky04_single_shell_continuation import single_shell
from skyseed_v2.backend.bpf import compile_bpf
from skyseed_v2.simulator import PlanTransition, ZoneServiceTransition, create_plan


@pytest.fixture(scope='module')
def simulator():
    value = five_shells.create_simulator(five_shells.build_emulator(), duration=timedelta(seconds=5))
    yield value
    value.get_planner().shutdown()


def test_shared_inventory_and_isolated_model_scopes(simulator):
    world = simulator.get_physical_world()
    system, = world.autonomous_systems
    assert tuple(shell.shell_id for shell in system.shells) == five_shells.SHELLS
    satellites = tuple(sat for shell in system.shells for orbit in shell.orbits for sat in orbit.satellites)
    assert len(satellites) == 4408
    assert len(system.ground_stations) == 355
    assert len(world.get_zone_layout().zones) == 732
    assert {station.max_simultaneous_satellite_accesses for station in system.ground_stations} == {10}
    assert {sat.max_simultaneous_ground_station_accesses for sat in satellites} == {2}
    planner = simulator.get_planner()
    scopes = {sat.node_id: shell.shell_id for shell in system.shells for orbit in shell.orbits for sat in orbit.satellites}
    for model in (planner._ground_station_model, planner._zone_service_model, planner._route_model):
        assert model._satellite_scopes == scopes
    assert planner._ground_station_model._scope_limits == {name: 2 for name in five_shells.SHELLS}
    assert planner._ground_station_model._default_ground_station_limit == 10
    pairs = planner.create_physical_link_request().inter_satellite_pairs
    assert pairs and all(scopes[a] == scopes[b] for a, b in pairs)
    assert planner._route_model._gateway_candidate_count == 3


def test_scoped_zone_services_compile_to_distinct_destinations(simulator):
    world = simulator.get_physical_world()
    timeline = simulator.get_timeline()
    services = simulator.get_planner()._zone_service_model.assign_zone_service(world, (), (), timestamp=MODEL_TIMESTAMP)
    assert len(services.assignments) == 3660
    assert all(value.satellite_id is None for value in services.assignments)
    initial = PlanTransition(timeline.start_time, zone_service_transitions=tuple(
        ZoneServiceTransition(value.zone_id, None, None, network_scope=value.network_scope)
        for value in services.assignments))
    plan = create_plan(None, previous_plan_id=None, physical_world_fingerprint=world.semantic_fingerprint(),
        observation_interval=timeline.observation_interval, start_timestamp=timeline.start_time, transitions=(initial,))
    compiled = compile_bpf(plan, world, None)
    assert len(compiled.zone_prefixes) == 3660
    assert len(set(dict(compiled.zone_prefixes).values())) == 3660
    assert {scope for scope, zone in dict(compiled.zone_prefixes)} == set(five_shells.SHELLS)


def test_ordinary_single_shell_selection_is_s4():
    emulator = single_shell.build_emulator()
    from skyseed_v2.emulator.seedemu import get_skyseed_autonomous_systems
    system, = get_skyseed_autonomous_systems(emulator.getLayer('Base'))
    assert len(tuple(system.satellites())) == 348
    assert tuple(shell.shell_id for shell in system.shells) == ('S4',)
