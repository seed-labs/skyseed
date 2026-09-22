"""Existing demand association, native edge policy, and independent bounded sampling."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from skyseed_v2 import adapters, emulator, simulator
from skyseed_v2.shared import NodeId
from examples.Sky06_s4_native_ospf.edge_routes import EdgeRoutes
from tests.fixtures.native_ospf import build_small, create_small_simulator, A, B, C, G, H, START
from tools.generation.traffic import probe_plan as traffic
from skyseed_v2.backend.execution_materials.actions import _ForwardingBinding, _ForwardingKey


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


def _projection(count=20):
    outcomes, bindings, resources = {}, {}, set()
    grounds = [NodeId(65040, 'ground-station', f'g{i:02}') for i in range(count)]
    for gs in grounds:
        c = simulator.OperationalConnectionId(simulator.OperationalConnectionKind.GROUND_STATION_ACCESS, gs, A)
        for source, target, other in ((gs, simulator.ZoneRouteDemandTarget('zone'), A),
                                      (A, simulator.GroundStationRouteDemandTarget(gs), gs)):
            demand = simulator.RouteDemand(source, target)
            outcomes[demand] = simulator.LocalRouteOutcome(demand, candidates=(
                simulator.RouteCandidate(simulator.LogicalExit(c, source)),))
            key = _ForwardingKey(source, target)
            bindings[key] = _ForwardingBinding(key, other, c, None)
        resources.add(c)
    projection = traffic._BoundaryProjection(START, outcomes, bindings,
        {('', 'zone'): ('198.18.0.1', C)}, resources, [], [], [], {})
    return projection, grounds


def test_native_geography_selects_independent_extremes_with_deterministic_ties():
    projection, grounds = _projection()
    def scan(visit):
        visit(projection)
        return (START,)
    coordinates = {gs: (0, i) for i, gs in enumerate(grounds)}
    def select():
        return traffic._native_candidates_from_scan(scan, (START,),
            ground_coordinates=coordinates, zone_coordinates={'zone': (0, 0)})
    seeds = select()
    assert len(seeds) <= 4 * traffic._NATIVE_CANDIDATE_BUCKET_CAPACITY
    assert [s.ground_station for s in seeds[:8:2]] == [grounds[0], grounds[-1], grounds[1], grounds[-2]]
    assert [s.geographic_separation_class for s in seeds[:8:2]] == ['near', 'far', 'near', 'far']
    assert seeds[2].geographic_separation_m == pytest.approx(2_112_706.524, abs=1)
    projection.route_outcomes = dict(reversed(tuple(projection.route_outcomes.items())))
    assert select() == seeds
    coordinates.update({gs: (0, 1) for gs in grounds})
    equal = select()
    assert {s.geographic_separation_class for s in equal} == {'equal'}
    assert equal[0].ground_station == grounds[0]


@pytest.mark.parametrize('missing', ['zone', 'uplink', 'return-edge', 'all'])
def test_unarranged_native_candidates_are_not_selected(missing):
    projection, grounds = _projection(1)
    if missing == 'zone':
        projection.zone_edges.clear()
    elif missing == 'all':
        projection.resources.clear()
    else:
        source = grounds[0] if missing == 'uplink' else A
        projection.forwarding = {key: value for key, value in projection.forwarding.items() if key.source != source}
    def scan(visit):
        visit(projection)
        return (START,)
    assert traffic._native_candidates_from_scan(scan, (START,)) == ()


def test_service_recovery_without_route_changes_can_supply_a_candidate():
    projection, grounds = _projection(1)
    projection.zone_edges.clear()
    def scan(visit):
        visit(projection)
        projection.nominal_time = START + timedelta(seconds=5)
        projection.zone_edges[('', 'zone')] = ('198.18.0.1', C)
        visit(projection)
        return (START, projection.nominal_time)
    seeds = traffic._native_candidates_from_scan(scan, (START, START + timedelta(seconds=5)))
    assert len(seeds) == 2
    assert {s.geographic_separation_class for s in seeds} == {'unavailable'}
    assert all(s.geographic_separation_m is None for s in seeds)


@pytest.mark.parametrize('count', [1, 20])
def test_missing_geography_preserves_bounded_direction_coverage(count):
    projection, grounds = _projection(count)
    def scan(visit):
        visit(projection)
        return (START,)
    seeds = traffic._native_candidates_from_scan(scan, (START,))
    assert len(seeds) <= 2 * traffic._NATIVE_CANDIDATE_BUCKET_CAPACITY
    assert [s.direction for s in seeds[:2]] == [
        'ground-station-to-zone', 'zone-to-ground-station']
    assert all(s.geographic_separation_m is None and
               s.geographic_separation_class == 'unavailable' for s in seeds)
    if count == 1:
        located = traffic._native_candidates_from_scan(scan, (START,),
            ground_coordinates={grounds[0]: (0, 0)}, zone_coordinates={'zone': (0, 1)})
        assert len(located) == 2
        assert {s.geographic_separation_class for s in located} == {'only-candidate'}
