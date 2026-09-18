"""Small hand-checkable graphs for the documented OSPF computation subset."""
from tests._support import MODEL_TIMESTAMP
from dataclasses import replace
from datetime import timedelta

import pytest

from examples.Sky03_s3_ospf.ospf_model import OspfInterfaceConfiguration, OspfRoutePlanningModel
from skyseed_v2.backend.bpf import compile_bpf
from skyseed_v2.simulator.planner import (
    RouteOutcomeTransition,
    EffectiveConnectionParameters,
    GatewayHopCountRoutePlanningModel, OperationalConnection, OperationalConnectionId,
    OperationalConnectionKind, RouteDemand, GroundStationRouteDemandTarget,
    ZoneRouteDemandTarget, ZoneServiceAssignment, ReachableRouteOutcome,
    UnreachableRouteOutcome, PlanTransition, RouteUnreachableReason, create_plan,
)
from tests.unit.test_network_planner_semantic_core import _world, _satellite, _station, EPOCH


def scene(count=6, stations=2):
    world, sats, gs = _world(satellites=tuple(_satellite(i) for i in range(count)),
                            stations=tuple(_station(i) for i in range(stations)))
    return world, [s.node_id for s in sats], [g.node_id for g in gs]


def link(a, b, ab=1, ba=1):
    kind = (OperationalConnectionKind.INTER_SATELLITE if a.entity_kind == b.entity_kind
            else OperationalConnectionKind.GROUND_STATION_ACCESS)
    identifier = OperationalConnectionId(kind, a, b)
    if identifier.endpoint_a != a:
        ab, ba = ba, ab
    return OperationalConnection(identifier, EffectiveConnectionParameters(1000000, ab, 0),
                                 EffectiveConnectionParameters(1000000, ba, 0))


def configured(connection, area, ab=None, ba=None):
    return OspfInterfaceConfiguration(connection.connection_id, area, ab, ba)


def solve(model, world, links, demands, *, terminal=None, previous=()):
    zones = () if terminal is None else (ZoneServiceAssignment('zone-a', terminal),)
    return model.plan_route_outcomes(world, tuple(links), zones, tuple(demands), previous, timestamp=MODEL_TIMESTAMP)


def test_directed_costs_parameter_change_ties_and_cache():
    w, s, g = scene(4)
    edges = [link(s[0], s[1], 1, 9), link(s[1], s[3]),
             link(s[0], s[2]), link(s[2], s[3]), link(s[3], g[0])]
    demand = RouteDemand(s[0], GroundStationRouteDemandTarget(g[0]))
    m = OspfRoutePlanningModel(cost_metric='delay_us')
    first = solve(m, w, edges, [demand])
    assert first.outcomes[0].path == (s[0], s[1], s[3], g[0])
    same = solve(m, w, reversed(edges), [demand], previous=first.outcomes)
    assert all(a is b for a, b in zip(same.outcomes, first.outcomes))
    assert m.route_work_statistics()['route_destination_jobs_reused'] == 1
    changed = [link(s[0], s[1], 10, 9), *edges[1:]]
    second = solve(m, w, changed, [demand], previous=first.outcomes)
    assert second.outcomes[0].path == (s[0], s[2], s[3], g[0])
    assert second.outcomes != first.outcomes
    assert m.route_work_statistics()['route_destination_jobs'] == 1
    # Direction is not symmetrized, including explicit overrides.
    reverse = RouteDemand(g[0], ZoneRouteDemandTarget('zone-a'))
    assert solve(m, w, edges, [reverse], terminal=s[0]).outcomes[0].path == (g[0], s[3], s[2], s[0])
    override = OspfRoutePlanningModel(interfaces=(configured(edges[0], 0, 7, 1),))
    assert solve(override, w, edges, [demand]).outcomes[0].path == (s[0], s[2], s[3], g[0])


def test_intra_area_preference_backbone_loss_and_recovery():
    w, s, g = scene(5)
    edges = [link(s[0], s[1], 50, 50), link(s[1], s[2], 1, 1),
             link(s[0], s[3]), link(s[3], s[2]), link(s[2], s[4]), link(s[4], g[0])]
    config = tuple(configured(e, a) for e, a in zip(edges[:5], [1, 1, 0, 0, 2]))
    m = OspfRoutePlanningModel(interfaces=config, cost_metric='delay_us')
    zone = RouteDemand(g[1], ZoneRouteDemandTarget('zone-a'))
    access = link(g[1], s[0])
    # Destination on ABR belongs to all its areas. Intra beats the cheap backbone.
    r = solve(m, w, [*edges, access], [zone], terminal=s[2])
    assert r.outcomes[0].path == (g[1], s[0], s[3], s[2])
    # Both routes intra at s0: area0 wins lower cost. Use destination inside area1.
    r = solve(m, w, [*edges, access], [zone], terminal=s[1])
    assert r.outcomes[0].path == (g[1], s[0], s[1])
    d = RouteDemand(s[1], GroundStationRouteDemandTarget(g[0]))
    first = solve(m, w, edges, [d])
    assert first.outcomes[0].path == (s[1], s[2], s[4], g[0])  # ABR has intra in area2
    # Distinct ordinary areas connected only through backbone: no area1 shortcut.
    extra = link(s[0], g[1])
    d2 = RouteDemand(g[1], GroundStationRouteDemandTarget(g[0]))
    r = solve(m, w, [*edges, extra], [d2])
    assert r.outcomes[0].path == (g[1], s[0], s[3], s[2], s[4], g[0])
    lost = solve(m, w, [e for e in edges if e != edges[2]] + [extra], [d2], previous=r.outcomes)
    assert isinstance(lost.outcomes[0], UnreachableRouteOutcome)
    recovered = solve(m, w, [*edges, extra], [d2], previous=lost.outcomes)
    assert recovered.outcomes == r.outcomes
    assert recovered.outcomes != lost.outcomes


def test_ordinary_area_spf_can_cross_an_abr_without_replacing_its_forwarding():
    w, (c, a, x, b, d, e), (g,) = scene(6, 1)
    edges = [link(c, a), link(a, x), link(x, b), link(c, b, 10, 10),
             link(a, e, 50, 50), link(e, b, 50, 50), link(b, d), link(d, g)]
    model = OspfRoutePlanningModel(
        interfaces=tuple(configured(edge, area) for edge, area in
                         zip(edges, (1, 1, 1, 1, 0, 0, 2, 2))),
        default_area=None, cost_metric='delay_us',
    )
    demands = [RouteDemand(n, GroundStationRouteDemandTarget(g)) for n in (c, a, x, b, d, e)]
    result = solve(model, w, edges, demands)
    # B advertises 2; A advertises 102. C reaches B within Area 1 via
    # C-A-X-B (3), so 3+2 beats C-A (1) + 102: C's next hop is A.
    # A keeps its own backbone next hop E; the actual path follows that
    # decision, not the SPF continuation A-X-B used in C's calculation.
    paths = {o.demand.source: o.path for o in result.outcomes}
    assert paths == {
        c: (c, a, e, b, d, g), a: (a, e, b, d, g), x: (x, b, d, g),
        b: (b, d, g), d: (d, g), e: (e, b, d, g),
    }
    from skyseed_v2.simulator.planner import ZoneServiceTransition
    # The shared world fixture also has one Zone, explicitly unserved here.
    transition = PlanTransition(timestamp=EPOCH, established_connections=tuple(edges),
        zone_service_transitions=(ZoneServiceTransition('zone-a', None, None),),
        route_outcome_transitions=tuple(RouteOutcomeTransition(o.demand, None, o) for o in result.outcomes))
    plan = create_plan(None, previous_plan_id=None, start_timestamp=EPOCH,
        physical_world_fingerprint=w.semantic_fingerprint(),
        observation_interval=timedelta(seconds=5), transitions=(transition,))
    compiled = compile_bpf(plan, w, None)
    assert compiled.source_plan_id == plan.plan_id


def test_shared_destination_paths_compile_with_multihomed_gs_and_zone():
    w, s, g = scene(6)
    edges = [link(s[0], s[1]), link(s[1], s[2]), link(s[2], s[3]),
             link(s[3], s[4]), link(s[1], s[5]), link(s[5], s[3]),
             link(g[0], s[0]), link(g[0], s[4]), link(g[1], s[2])]
    config = tuple(configured(e, a) for e, a in zip(edges[:6], [1, 0, 0, 2, 1, 2]))
    m = OspfRoutePlanningModel(interfaces=config)
    demands = [RouteDemand(n, GroundStationRouteDemandTarget(g[0])) for n in [*s, g[1]]]
    demands += [RouteDemand(n, ZoneRouteDemandTarget('zone-a')) for n in [g[0], g[1], s[0]]]
    result = solve(m, w, edges, demands, terminal=s[3])
    assert all(isinstance(o, ReachableRouteOutcome) for o in result.outcomes)
    hops = {}
    for o in result.outcomes:
        assert all(n.entity_kind == 'satellite' for n in o.path[1:-1])
        for a, b in zip(o.path, o.path[1:]):
            key = a, o.demand.target
            assert hops.setdefault(key, b) == b
    t = PlanTransition(timestamp=EPOCH, established_connections=tuple(edges),
        zone_service_transitions=(), route_outcome_transitions=tuple(RouteOutcomeTransition(o.demand, None, o) for o in result.outcomes))
    # Compiler also needs the Zone assignment, in exactly the normal transition.
    from skyseed_v2.simulator.planner import ZoneServiceTransition
    t = replace(t, zone_service_transitions=(ZoneServiceTransition('zone-a', None, s[3]),), semantic_event_precedences=None)
    plan = create_plan(None, previous_plan_id=None, start_timestamp=EPOCH, physical_world_fingerprint=w.semantic_fingerprint(),
                       observation_interval=timedelta(seconds=5), transitions=(t,))
    compiled = compile_bpf(plan, w, None)
    assert compiled.source_plan_id == plan.plan_id


def test_demand_construction_matches_hop_count_and_withdrawal_lifecycle():
    w, s, g = scene(3)
    assignments = (ZoneServiceAssignment('zone-a', s[2]),)
    old = GatewayHopCountRoutePlanningModel(gateway_candidate_count=2)
    m = OspfRoutePlanningModel(gateway_candidate_count=2)
    assert m.create_route_demands(w, assignments, timestamp=MODEL_TIMESTAMP) == old.create_route_demands(w, assignments, timestamp=MODEL_TIMESTAMP)
    edges = [link(g[0], s[0]), link(s[0], s[2]), link(g[1], s[2])]
    demands = m.create_route_demands(w, assignments, timestamp=MODEL_TIMESTAMP)
    first = m.plan_route_outcomes(w, tuple(edges), assignments, demands, (), timestamp=MODEL_TIMESTAMP)
    second = solve(m, w, edges, [], previous=first.outcomes)
    assert not second.outcomes
    assert len(first.outcomes) == len(demands)
    unserved = solve(m, w, edges, [RouteDemand(g[0], ZoneRouteDemandTarget('zone-a'))])
    assert unserved.outcomes[0].reason is RouteUnreachableReason.ZONE_UNSERVED
    assert not m._destinations['']
    m.shutdown(); m.shutdown(wait=False); old.shutdown()
    with pytest.raises(RuntimeError, match='shut down'):
        solve(m, w, edges, demands)


def test_scope_isolation_shared_gs_and_explicit_exclusion():
    w, s, g = scene(4)
    scopes = {n: 'a' if i < 2 else 'b' for i, n in enumerate(s)}
    edges = [link(s[0], s[1]), link(s[2], s[3]), link(g[0], s[1]), link(g[0], s[2]), link(g[1], s[3])]
    d = RouteDemand(s[0], GroundStationRouteDemandTarget(g[1], 'a'))
    m = OspfRoutePlanningModel(satellite_scopes=scopes)
    assert isinstance(solve(m, w, edges, [d]).outcomes[0], UnreachableRouteOutcome)
    with pytest.raises(ValueError, match='scope'):
        solve(m, w, edges, [RouteDemand(s[0], GroundStationRouteDemandTarget(g[0], 'b'))])
    excluded = OspfRoutePlanningModel(interfaces=(configured(edges[0], None),))
    assert isinstance(solve(excluded, w, edges, [RouteDemand(s[0], GroundStationRouteDemandTarget(g[0]))]).outcomes[0], UnreachableRouteOutcome)


@pytest.mark.parametrize('kwargs', [{'area_id': -1}, {'cost_a_to_b': 0}, {'cost_b_to_a': True}])
def test_malformed_configuration_rejected(kwargs):
    _, s, _ = scene(2)
    with pytest.raises(ValueError):
        OspfInterfaceConfiguration(link(*s).connection_id, **kwargs)


def test_single_area_matches_independent_directed_shortest_paths():
    from heapq import heappush, heappop
    from random import Random
    w, s, g = scene(8)
    rng = Random(1828)
    edges = [link(a, b, rng.randint(1, 9), rng.randint(1, 9))
             for i, a in enumerate(s) for b in s[i+1:] if rng.random() < .4]
    edges += [link(g[0], s[0], 4, 3), link(g[0], s[6], 1, 5), link(g[1], s[7], 6, 2)]
    demands = [RouteDemand(n, GroundStationRouteDemandTarget(g[0])) for n in [*s, g[1]]]
    m = OspfRoutePlanningModel(cost_metric='delay_us')
    result = solve(m, w, edges, demands)
    directed = {}
    for e in edges:
        a, b = e.connection_id.endpoint_a, e.connection_id.endpoint_b
        directed[a, b] = e.endpoint_a_to_b.delay_us
        directed[b, a] = e.endpoint_b_to_a.delay_us
    for outcome in result.outcomes:
        source = outcome.demand.source
        queue, distances = [(0, source.value, source)], {source: 0}
        while queue:
            cost, _, n = heappop(queue)
            if cost != distances[n] or n.entity_kind == 'ground-station' and n != source:
                continue
            for (a, b), edge in directed.items():
                if a == n and cost + edge < distances.get(b, float('inf')):
                    distances[b] = cost + edge
                    heappush(queue, (cost+edge, b.value, b))
        if g[0] in distances:
            assert sum(directed[a, b] for a, b in zip(outcome.path, outcome.path[1:])) == distances[g[0]]
        else:
            assert isinstance(outcome, UnreachableRouteOutcome)


def test_ospf_retained_continuation_is_suffix_only_and_compiler_equivalent(tmp_path):
    from examples.Sky00_tiny_network import tiny_network as tiny
    from skyseed_v2.simulator import create_default_simulator, SimulationDefinition, SimulationTimeline
    from skyseed_v2.adapters.emulator_to_simulator import create_simulator_static_input
    from skyseed_v2.emulator.seedemu import get_skyseed_autonomous_systems
    from skyseed_v2.experiment.producer import Producer
    from skyseed_v2.simulator.planner import plan_transition_fingerprint

    def run(endpoints, retained):
        network = tiny.build_emulator()
        definition = SimulationDefinition(create_simulator_static_input(get_skyseed_autonomous_systems(network.getLayer('Base'))),
            SimulationTimeline(tiny.DEFAULT_START_TIME, tiny.DEFAULT_START_TIME+timedelta(seconds=endpoints[0]), timedelta(seconds=5)))
        m = OspfRoutePlanningModel()
        simulation = create_default_simulator(definition, configuration=tiny.simulation_configuration(), route_model=m)
        frames, fingerprints = [], []
        with Producer(simulation.get_timeline(), simulation.get_physical_world(),
                simulation.get_physical_link_model(), simulation.get_planner(), retain=retained,
                formal_observation_sink=lambda o: frames.append(o.timestamp)) as producer:
            for i, end in enumerate(endpoints):
                if i:
                    producer.extend(tiny.DEFAULT_START_TIME+timedelta(seconds=end))
                while producer.produce_next_realization() is not None:
                    fingerprints.extend(plan_transition_fingerprint(t) for t in producer.latest_plan.transitions)
            if retained:
                assert not m._closed
                producer.finish()
        assert m._closed
        return frames, fingerprints
    whole = run((60,), False)
    staged = run((30, 60), True)
    assert whole == staged
    assert staged[0][7] == tiny.DEFAULT_START_TIME + timedelta(seconds=35)
    assert len(set(staged[0])) == len(staged[0]) == 13


def test_explicit_terminal_stub_area_survives_access_and_backbone_loss():
    w, s, g = scene(3)
    ordinary, backbone, stub = link(s[0], s[1]), link(s[1], s[2]), link(g[0], s[1])
    m = OspfRoutePlanningModel(interfaces=(configured(ordinary, 1), configured(stub, 0)))
    d = RouteDemand(s[0], GroundStationRouteDemandTarget(g[0]))
    initial = solve(m, w, [ordinary, backbone, stub], [d])
    assert initial.outcomes[0].path == (s[0], s[1], g[0])
    # A satellite-side stub keeps its area; disappearance of an unlisted
    # default-area ISL must not turn valid configuration into a malformed one.
    lost_backbone = solve(m, w, [ordinary, stub], [d], previous=initial.outcomes)
    assert lost_backbone.outcomes == initial.outcomes
    lost_access = solve(m, w, [ordinary], [d], previous=lost_backbone.outcomes)
    assert isinstance(lost_access.outcomes[0], UnreachableRouteOutcome)
    recovered = solve(m, w, [ordinary, stub], [d], previous=lost_access.outcomes)
    assert recovered.outcomes == initial.outcomes


def test_terminal_direction_when_canonical_order_places_satellite_before_gs():
    from skyseed_v2.shared import NodeId
    station = replace(_station(), node_id=NodeId(70000, 'ground-station', 'ground-0'))
    w, sats, stations = _world(stations=(station,))
    a, b = (s.node_id for s in sats)
    g = stations[0].node_id
    access = link(g, b, 9, 2)
    assert access.connection_id.endpoint_a == b
    edges = [link(a, b), access, link(g, a)]
    m = OspfRoutePlanningModel(cost_metric='delay_us', interfaces=(configured(access, 0, 2, 9),))
    forward = RouteDemand(a, GroundStationRouteDemandTarget(g))
    reverse = RouteDemand(g, ZoneRouteDemandTarget('zone-a'))
    result = solve(m, w, edges, [forward, reverse], terminal=b)
    outcomes = {o.demand: o for o in result.outcomes}
    assert outcomes[forward].path == (a, g)
    assert outcomes[reverse].path == (g, a, b)
