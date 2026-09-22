"""Explicit Satellite identity demands through planning and detached backends."""
from contextlib import closing
from dataclasses import replace

import struct
import pytest
from skyseed_v2 import simulator as api
from skyseed_v2.backend import compile_plan
from skyseed_v2.backend.bpf.dataplane.materials import _MaterialCursor
from skyseed_v2.backend.execution_materials.codec import _open_runner_artifact
from skyseed_v2.shared import NodeId
from skyseed_v2.simulator.planner.values import _route_demand_key
from templates.dataplane.threshold import scene
from templates.dataplane.threshold.scene import A, B, G, START
from tests.unit.test_linux_runtime import produce, initialize
from tests.unit.test_linux_routes import runtime, candidate

TARGET = api.SatelliteRouteDemandTarget(B)
DEMAND = api.RouteDemand(G, TARGET)


def produce_routes(tmp_path, monkeypatch, model, *, backend='linux', demands=(DEMAND,), ends=(5, 15, 20)):
    create = scene.create_scene
    def factory(*args, **kwargs):
        simulation = create(*args, **kwargs)
        simulation.get_planner().set_route_demands(demands)
        return simulation
    monkeypatch.setattr(scene, 'create_scene', factory)
    return produce(tmp_path, backend=backend, model=model, ends=ends)


class Snapshots:
    def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
        offset = (timestamp - START).total_seconds()
        if offset == 10:
            return api.RoutePlanningResult()
        if offset == 5:
            return api.RoutePlanningResult((api.LocalRouteOutcome(DEMAND,
                candidates=(candidate(connections, G, B),)),))
        if offset == 20:
            return api.RoutePlanningResult((api.UnreachableRouteOutcome(DEMAND,
                api.RouteUnreachableReason.NO_ELIGIBLE_PATH),))
        return api.RoutePlanningResult((api.ReachableRouteOutcome(DEMAND, A, (G, A, B)),))


@pytest.mark.parametrize('backend', ['linux', 'bpf'])
def test_paths_local_omission_and_continuation_use_existing_identity(tmp_path, monkeypatch, backend):
    simulation, plans, paths = produce_routes(tmp_path, monkeypatch, Snapshots(), backend=backend)
    assert all(api.plan_from_data(api.plan_to_data(p)) == p for p in plans)
    assert plans[2].transitions[0].route_outcome_transitions[0].current is None
    assert isinstance(plans[1].transitions[0].route_outcome_transitions[0].current, api.LocalRouteOutcome)
    cursor = None
    adapter = None
    try:
        ordinal = 0
        for path in paths:
            with closing(_open_runner_artifact(path)) as artifact:
                addresses = {n: next(r['node_address'] for r in artifact.dataplane['nodes'] if r['key'] == n.value) for n in (A,B,G)}
                if backend == 'linux':
                    if adapter is None:
                        kernel, adapter = runtime(monkeypatch, artifact)
                        initialize(adapter, artifact)
                    prepared = adapter.prepare_all(artifact.boundaries())
                    for boundary, ready in zip(artifact.boundaries(), prepared):
                        adapter.activate(ready)
                        expected = ({G, A}, {G}, set(), {G, A}, set())[ordinal]
                        assert {n for n in (A, B, G) if kernel.fib[n.value]} == expected
                        for source in expected:
                            route = next(iter(kernel.fib[source.value].values()))
                            assert route['destination'] == addresses[B]
                            assert route['prefsrc'] == addresses[source]
                            active = kernel.kernel.connections
                            peer = B if source == A or ordinal == 1 else A
                            connection = next(c for c in active.values() if {c['node_a'],c['node_b']} == {source.value,peer.value})
                            side = 'a' if connection['node_a'] == peer.value else 'b'
                            assert route['gateway'] == connection['address_'+side].split('/')[0]
                        ordinal += 1
                else:
                    if cursor is None:
                        cursor = _MaterialCursor(artifact.physical_world_projection.node_ids, artifact.dataplane)
                    for boundary in artifact.boundaries():
                        draft = cursor.apply(boundary)
                        expected = ({G, A}, {G}, set(), {G, A}, set())[ordinal]
                        assert {n for n in (A, G) if (0, addresses[B]) in draft.generation_by_node[n].forwarding} == expected
                        for source in expected:
                            forwarding = draft.generation_by_node[source].forwarding
                            entry = forwarding[0, addresses[B]]
                            assert struct.unpack_from("<I", entry[1], 4)[0] not in (0, 0xfffffffd, 0xfffffffe)
                        ordinal += 1
        assert ordinal == 5
    finally:
        if adapter is not None:
            adapter.cleanup()
            kernel.assert_clean()
        simulation.shutdown()


@pytest.mark.parametrize('backend', ['linux', 'bpf'])
def test_partial_local_results_need_no_requested_demand_and_keep_dispositions(tmp_path, monkeypatch, backend):
    class Local:
        def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
            assert not demands
            disposition = {0: api.RouteDisposition.FORWARD, 5: api.RouteDisposition.DROP,
                           10: api.RouteDisposition.PASS}[(timestamp-START).total_seconds()]
            return api.RoutePlanningResult((api.LocalRouteOutcome(DEMAND, disposition,
                (candidate(connections, G, A),) if disposition is api.RouteDisposition.FORWARD else ()),))
    sim, plans, paths = produce_routes(tmp_path, monkeypatch, Local(), backend=backend, demands=(), ends=(10,))
    assert [p.transitions[0].route_outcome_transitions[0].current.disposition for p in plans] == [
        api.RouteDisposition.FORWARD, api.RouteDisposition.DROP, api.RouteDisposition.PASS]
    with closing(_open_runner_artifact(paths[0])) as artifact:
        if backend == 'linux':
            kernel, adapter = runtime(monkeypatch, artifact)
            initialize(adapter, artifact)
            try:
                for ready, kind in zip(adapter.prepare_all(artifact.boundaries()), ('unicast','blackhole',None)):
                    adapter.activate(ready)
                    routes = tuple(kernel.fib[G.value].values())
                    assert (routes[0]['kind'] if routes else None) == kind
                    assert not kernel.fib[A.value] and not kernel.fib[B.value]
            finally:
                adapter.cleanup()
                kernel.assert_clean()
        else:
            cursor = _MaterialCursor(artifact.physical_world_projection.node_ids, artifact.dataplane)
            destination = next(n['node_address'] for n in artifact.dataplane['nodes'] if n['key'] == B.value)
            for index, boundary in enumerate(artifact.boundaries()):
                draft = cursor.apply(boundary)
                entry = draft.generation_by_node[G].forwarding[0,destination]
                port = struct.unpack_from('<I',entry[1],4)[0]
                assert port not in (0,0xfffffffd,0xfffffffe) if index == 0 else port == (0xfffffffe if index == 1 else 0xfffffffd)
                assert (0,destination) not in draft.generation_by_node[A].forwarding
    sim.shutdown()


def test_kind_scope_order_and_exports():
    assert api.planner.SatelliteRouteDemandTarget is api.SatelliteRouteDemandTarget
    with pytest.raises(ValueError, match='satellite'):
        api.SatelliteRouteDemandTarget(G)
    with pytest.raises((TypeError, ValueError)):
        api.SatelliteRouteDemandTarget(B, 42)
    other = api.RouteDemand(G, api.SatelliteRouteDemandTarget(B, 'shell'))
    assert other != DEMAND
    ordered = sorted([other, api.RouteDemand(G, api.ZoneRouteDemandTarget('z')),
                      DEMAND, api.RouteDemand(G, api.GroundStationRouteDemandTarget(G))], key=_route_demand_key)
    assert ordered[1] == DEMAND and ordered[-1] == other


@pytest.mark.parametrize('case', ['foreign', 'wrong-terminal', 'gs-transit'])
def test_planner_rejects_foreign_targets_and_invalid_paths(tmp_path, monkeypatch, case):
    foreign = api.RouteDemand(G, api.SatelliteRouteDemandTarget(NodeId(65001, 'satellite', 'absent')))
    demand = foreign if case == 'foreign' else DEMAND if case == 'wrong-terminal' else api.RouteDemand(A,TARGET)
    class Invalid:
        def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
            if case == 'foreign':
                return api.RoutePlanningResult()  # Even an omitted requested outcome validates membership.
            path = (G,A) if case == 'wrong-terminal' else (A,G,B)
            return api.RoutePlanningResult((api.ReachableRouteOutcome(demand,path[1],path),))
    with pytest.raises((KeyError, ValueError), match='absent|wrong target|transit'):
        produce_routes(tmp_path, monkeypatch, Invalid(), demands=(demand,), ends=(0,))


@pytest.mark.parametrize('backend', ['linux','bpf'])
def test_compiler_independently_checks_target_membership(tmp_path, monkeypatch, backend):
    sim, plans, _ = produce_routes(tmp_path, monkeypatch, Snapshots(), backend=backend, ends=(0,))
    plan = plans[0]
    old = plan.transitions[0]
    foreign = api.RouteDemand(G, api.SatelliteRouteDemandTarget(NodeId(65001, 'satellite', 'absent')))
    local = api.LocalRouteOutcome(foreign,api.RouteDisposition.PASS)
    transition = replace(old,semantic_event_precedences=None,route_outcome_transitions=(api.RouteOutcomeTransition(foreign,None,local),))
    invalid = api.create_plan(None,previous_plan_id=None,physical_world_fingerprint=plan.physical_world_fingerprint,
        observation_interval=plan.observation_interval,start_timestamp=plan.start_timestamp,transitions=(transition,))
    with pytest.raises(ValueError,match='foreign PhysicalWorld Satellite'):
        compile_plan(invalid,sim.get_physical_world(),None,forwarding_backend=backend)
    sim.shutdown()


def test_existing_models_serve_explicit_satellite_demands_without_generating_them():
    from templates.models.routing import SimpleRouteModel
    from examples.Sky03_s3_ospf.ospf_model import OspfRoutePlanningModel
    sim = scene.create_scene()
    world = sim.get_physical_world()
    c = api.OperationalConnectionId(api.OperationalConnectionKind.GROUND_STATION_ACCESS, G, B)
    p = api.EffectiveConnectionParameters(1000,1,0)
    connections = (api.OperationalConnection(c,p,p),)
    model = api.GatewayHopCountRoutePlanningModel()
    try:
        assert all(not isinstance(d.target,api.SatelliteRouteDemandTarget)
                   for d in model.create_route_demands(world,(),timestamp=START))
        for selected in (model,SimpleRouteModel(),OspfRoutePlanningModel()):
            result = selected.plan_route_outcomes(world,connections,(),(DEMAND,),(),timestamp=START)
            assert result.outcomes[0].path == (G,B)
        scoped = api.GatewayHopCountRoutePlanningModel(satellite_scopes={A:'a',B:'b'})
        try:
            with pytest.raises(ValueError,match='target is outside'):
                scoped.plan_route_outcomes(world,connections,(),(api.RouteDemand(G,api.SatelliteRouteDemandTarget(B,'a')),),(),timestamp=START)
        finally:
            scoped.shutdown()
    finally:
        model.shutdown()
        sim.shutdown()
