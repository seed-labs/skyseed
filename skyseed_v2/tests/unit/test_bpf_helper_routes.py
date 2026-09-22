"""Source forwarding maps and absence of superseded business helper actions."""
from contextlib import closing
from ipaddress import IPv4Address
import json
import struct
from types import SimpleNamespace

import pytest

from skyseed_v2 import simulator as api
from skyseed_v2.backend.bpf.dataplane.materials import _MaterialCursor
from skyseed_v2.backend.bpf.dataplane.runtime import _TcBpfRuntimeRealization
from skyseed_v2.backend.bpf.dataplane.translation import _BpfTranslation
from skyseed_v2.backend.execution_materials.codec import _open_runner_artifact
from templates.dataplane.threshold import scene
from templates.dataplane.threshold.scene import A, B, G, START
from tests.unit.test_linux_routes import candidate
from tests.unit.test_runner_runtime_optimization import _Kernel
from tests.unit.test_satellite_route_targets import DEMAND, Snapshots, produce_routes



def connection(source, peer):
    kind = (api.OperationalConnectionKind.GROUND_STATION_ACCESS if G in (source, peer)
            else api.OperationalConnectionKind.INTER_SATELLITE)
    return api.OperationalConnectionId(kind, source, peer)


SCENARIOS = [
    pytest.param(('via-a', 'local-b', 'empty', 'via-a', 'unreachable'),
                 (True,) * 5, id='reported-snapshots'),
    pytest.param(('empty', 'via-a', 'via-a', 'empty', 'via-a', 'via-b', 'via-a', 'empty'),
                 (True,) * 8, id='different-exits-and-route-switches'),
    pytest.param(('empty', 'via-b', 'via-b', 'empty', 'via-b', 'unreachable'),
                 (True,) * 6, id='identical-exit-install-remove'),
    pytest.param(('via-b', 'empty', 'via-b', 'empty'),
                 (True,) * 4, id='identical-exit-initial-install'),
    pytest.param(('via-a', 'via-a', 'via-a', 'via-a', 'empty', 'via-a', 'empty'),
                 (False, True, False, True, True, True, False), id='access-under-explicit-route'),
    pytest.param(('via-a', 'empty', 'via-a', 'empty', 'empty', 'via-a', 'empty'),
                 (False, True, True, False, True, True, True), id='simultaneous-route-access-changes'),
    pytest.param(('via-a', 'local-a', 'empty'), (True,) * 3, id='local-with-direct-access'),
    pytest.param(('via-a', 'local-a', 'empty'), (False,) * 3, id='local-without-direct-access'),
]


@pytest.mark.parametrize('modes,access_b', SCENARIOS)
@pytest.mark.parametrize('detached', [False, True], ids=['finite', 'detached-continuation'])
def test_source_maps_without_business_helper_actions(tmp_path, monkeypatch, modes, access_b, detached):
    class Routes:
        def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
            mode = modes[int((timestamp - START).total_seconds()) // 5]
            if mode == 'empty':
                return api.RoutePlanningResult()
            if mode == 'unreachable':
                outcome = api.UnreachableRouteOutcome(DEMAND, api.RouteUnreachableReason.NO_ELIGIBLE_PATH)
            elif mode in ('local-a', 'local-b'):
                outcome = api.LocalRouteOutcome(DEMAND,
                    candidates=(candidate(connections, G, A if mode == 'local-a' else B),))
            else:
                path = (G, A, B) if mode == 'via-a' else (G, B)
                outcome = api.ReachableRouteOutcome(DEMAND, path[1], path)
            return api.RoutePlanningResult((outcome,))

    class Access:
        def select_ground_station_accesses(self, world, candidates, state):
            include_b = access_b[int((state.timestamp - START).total_seconds()) // 5]
            return tuple(c for c in candidates if c.satellite_id == A or include_b)

    monkeypatch.setattr(scene, 'SimpleGroundStationModel', Access)
    # Capture the retained calculation state as well as detached material reuse.
    builds = []
    derive = _BpfTranslation._derive_boundary_plan
    def capture(self, *args, **kwargs):
        draft = derive(self, *args, **kwargs)
        builds.append(draft)
        return draft
    monkeypatch.setattr(_BpfTranslation, '_derive_boundary_plan', capture)
    end = (len(modes) - 1) * 5
    ends = tuple(range(0, end + 1, 5)) if detached else (end,)
    model = Snapshots() if modes[1] == 'local-b' else Routes()
    simulation, _, paths = produce_routes(tmp_path, monkeypatch, model, backend='bpf', ends=ends)
    simulation.shutdown()

    def forbidden(*args, **kwargs):
        pytest.fail('detached execution called translation')
    monkeypatch.setattr(_BpfTranslation, 'translate', forbidden)
    kernel = _Kernel(monkeypatch)
    runtime = cursor = previous = None
    ordinal = 0
    try:
        for batch, path in enumerate(paths):
            with closing(_open_runner_artifact(path)) as artifact:
                if runtime is None:
                    nodes = artifact.physical_world_projection.node_ids
                    addresses = {n: next(v['node_address'] for v in artifact.dataplane['nodes']
                                         if v['key'] == n.value) for n in nodes}
                    bindings = {n: SimpleNamespace(pid=i + 100, process_start_time_ticks=1, netns_inode=i + 200)
                                for i, n in enumerate(nodes)}
                    runtime = _TcBpfRuntimeRealization(executor=kernel, pin_root='/pins', bindings=bindings,
                        continuation_enabled=detached, batch_capacity_factor=4)
                    runtime.initialize(artifact.physical_world_projection, zone_prefixes=artifact.zone_prefixes,
                        runtime_capacity_demand=artifact.runtime_capacity_demand, dataplane=artifact.dataplane)
                    cursor = _MaterialCursor(nodes, artifact.dataplane)
                boundaries = tuple(artifact.boundaries())
                before_prepare = dict(kernel.links)
                ready = runtime.prepare_all(boundaries, logical_ports=artifact.logical_ports,
                    retirements=artifact.retirements,
                    **({'runtime_capacity_demand': artifact.runtime_capacity_demand} if batch else {}))
                assert kernel.links == before_prepare
                for boundary, plan in zip(boundaries, ready, strict=True):
                    mode = modes[ordinal]
                    draft = cursor.apply(boundary)
                    request = json.loads(plan.request)['request']
                    assert all('route_replace' not in n and 'route_withdraw' not in n
                               for n in request['operation_nodes'])
                    kernel.operations.clear()
                    runtime.activate(plan)
                    assert not any(e[0] in ('route', 'neighbour') for e in kernel.operations)

                    for node in (G, A):
                        peer = (A if mode in ('via-a', 'local-a') else B) if node == G else B
                        forwards = (node == G and mode in ('via-a', 'via-b', 'local-a', 'local-b')) or (node == A and mode == 'via-a')
                        version = draft.generation_by_node[node]
                        assert kernel.maps[node.value, 'generation_versions@0'][draft.generation] == version.version
                        entry = version.forwarding.get((0, addresses[B]))
                        assert (entry is not None) == forwards
                        key = version.version, int.from_bytes(IPv4Address(addresses[B]).packed, 'little')
                        actual = kernel.maps[node.value, 'routes@0'].get(key)
                        assert (actual is not None) == forwards
                        if forwards:
                            ports = artifact.logical_ports[connection(node, peer)]
                            port = ports[0 if connection(node, peer).endpoint_a == node else 1]
                            assert struct.unpack_from('<I', entry[1], 4)[0] == port
                            assert actual == port
                    if previous is not None and access_b[ordinal] == access_b[ordinal-1]:
                        assert draft.generation_by_node[B] is previous.generation_by_node[B]
                    if ordinal and modes[ordinal] == modes[ordinal-1] and access_b[ordinal] == access_b[ordinal-1]:
                        assert not request['operation_nodes'] and not boundary.executable['node_patches']
                        assert draft.generation_nodes is previous.generation_nodes
                        assert builds[ordinal].routes is builds[ordinal-1].routes
                        assert builds[ordinal].generation_nodes is builds[ordinal-1].generation_nodes
                    previous = draft
                    ordinal += 1
                    if detached:
                        while runtime.reclaim().get('work_units'):
                            pass
        assert ordinal == len(modes)
    finally:
        if runtime is not None:
            runtime.cleanup()


def test_access_only_changes_do_not_scan_explicit_route_tables(tmp_path, monkeypatch):
    class LookupOnly(dict):
        def __iter__(self):
            pytest.fail('access change scanned explicit destinations')
        def items(self):
            pytest.fail('access change scanned explicit destinations')
        def values(self):
            pytest.fail('access change scanned explicit destinations')

    class StableRoute:
        def plan_route_outcomes(self, *args, **kwargs):
            return api.RoutePlanningResult((api.ReachableRouteOutcome(DEMAND, A, (G, A, B)),))

    class Access:
        def select_ground_station_accesses(self, world, candidates, state):
            return tuple(c for c in candidates if c.satellite_id == A or state.timestamp == START)

    builds = []
    derive = _BpfTranslation._derive_boundary_plan
    def guard(self, *args, **kwargs):
        draft = derive(self, *args, **kwargs)
        if not builds:
            for node, routes in draft.routes.items():
                draft.routes[node] = LookupOnly(routes)
        else:
            assert draft.routes is builds[0].routes
        builds.append(draft)
        return draft

    monkeypatch.setattr(scene, 'SimpleGroundStationModel', Access)
    monkeypatch.setattr(_BpfTranslation, '_derive_boundary_plan', guard)
    simulation, _, _ = produce_routes(tmp_path, monkeypatch, StableRoute(), backend='bpf', ends=(0, 5, 10))
    simulation.shutdown()
    assert len(builds) == 3
    assert builds[1].operation_nodes == builds[2].operation_nodes == ()
    assert builds[2].generation_nodes is builds[1].generation_nodes
