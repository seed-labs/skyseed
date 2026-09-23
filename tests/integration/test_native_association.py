"""Existing demand association, native edge policy, and independent bounded sampling."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from skyseed import adapters, emulator, simulator
from skyseed.shared import NodeId
from examples.Sky06_s4_native_ospf.edge_routes import EdgeRoutes
from tests.fixtures.native_ospf import build_small, create_small_simulator, A, B, C, G, H, START
from skyseed.backend.execution_materials.actions import _ForwardingBinding, _ForwardingKey


def test_association_reuses_public_default_demands_without_route_computation(monkeypatch):
    network = build_small()
    system, = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer('Base'))
    for name, lon in (('west', -1), ('east', 1), ('distant', 20)):
        system.create_ground_station(name, station_type='gateway', operator=None,
            location=emulator.entities.GroundStationLocation(latitude_deg=0, longitude_deg=lon))
    routes = EdgeRoutes()
    settings = simulator.DefaultSimulatorConfiguration()
    reference = simulator.GatewayHopCountRoutePlanningModel(
        gateway_candidate_count=settings.gateway_candidate_count,
        gateway_candidate_max_distance_m=settings.gateway_candidate_max_distance_m)
    def no_routes(*args, **kwargs):
        pytest.fail('edge policy invoked full satellite route computation')
    monkeypatch.setattr(simulator.GatewayHopCountRoutePlanningModel, 'plan_route_outcomes', no_routes)
    sim = create_small_simulator(adapters.create_simulator_static_input((system,)), routes, service=True)
    world = sim.get_physical_world()
    try:
        assignments = (simulator.ZoneServiceAssignment('service-probe', C),)
        demands = routes.create_route_demands(world, assignments, timestamp=START)
        assert demands == reference.create_route_demands(world, assignments, timestamp=START)
        uplinks = [d for d in demands if isinstance(d.target, simulator.ZoneRouteDemandTarget)]
        assert len(uplinks) == settings.gateway_candidate_count == 3
        assert {d.source.entity_id for d in uplinks} == {'east', 'west', 'g'}
        def connection(gs, sat):
            return SimpleNamespace(connection_id=simulator.OperationalConnectionId(
                simulator.OperationalConnectionKind.GROUND_STATION_ACCESS, gs, sat))
        links = (connection(G, C), connection(G, A), connection(H, B))
        first = routes.plan_route_outcomes(world, links, assignments, demands, (), timestamp=START)
        gs_route, = (v for v in first.outcomes if v.demand.source == G)
        assert gs_route.candidates[0].exit.connection == links[1].connection_id
        assert len(first.outcomes) == 4  # Three actual return edges; one associated GS has access.
        moved = (simulator.ZoneServiceAssignment('service-probe', A),)
        later = routes.create_route_demands(world, moved, timestamp=START + timedelta(seconds=5))
        assert [d for d in later if isinstance(d.target, simulator.ZoneRouteDemandTarget)] == uplinks
        withdrawn = routes.plan_route_outcomes(world, links[-1:], moved, later, first.outcomes, timestamp=START)
        assert len(withdrawn.outcomes) == 1 and withdrawn.outcomes[0].demand.source == B
        assert routes._association.route_resource_statistics()['route_pool_workers'] == 0
        assert routes._association.route_work_statistics() == {}
    finally:
        sim.shutdown()
        reference.shutdown()
    with pytest.raises(RuntimeError, match='shut down'):
        routes.create_route_demands(world, assignments, timestamp=START)
