"""Small operation-count and actual-helper regressions for the 2B corrections."""
from contextlib import closing
from types import SimpleNamespace

import pytest

from skyseed.shared import NodeId
from skyseed.simulator import (
    RouteDemand, GroundStationRouteDemandTarget, RoutePlanningResult,
    ReachableRouteOutcome, UnreachableRouteOutcome, RouteUnreachableReason,
    LocalRouteOutcome, OperationalConnectionId, OperationalConnectionKind, LogicalExit,
)
from skyseed.simulator.planner.planner import _validate_route_result
from skyseed.backend.bpf.dataplane import BpfProgram, DataPlane, DataPlaneBinding, AttachmentScope
from skyseed.backend.bpf.dataplane.program import _DEFAULT
from skyseed.backend.bpf.dataplane.runtime import _TcBpfRuntimeRealization
from skyseed.backend.bpf.dataplane import runtime_command as command
from skyseed.backend.execution_materials.codec import _open_runner_artifact
from test_dataplane_structured import produce, program, ROOT, A, B, G


def test_route_result_membership_indexes_demands_once_without_linear_searches():
    iterations = []
    class Demands(tuple):
        def __iter__(self):
            iterations.append(1)
            return super().__iter__()
        def __contains__(self, value):
            pytest.fail('linear tuple membership on requested demands')
    demands = Demands(RouteDemand(NodeId(65001, 'satellite', str(i)), GroundStationRouteDemandTarget(G)) for i in range(12))
    outcomes = tuple(ReachableRouteOutcome(d, G, (d.source, G)) for d in tuple(demands))
    local = LocalRouteOutcome(RouteDemand(A, GroundStationRouteDemandTarget(G)))
    iterations.clear()
    actual = _validate_route_result(demands, RoutePlanningResult((*outcomes, local)))
    assert len(actual) == 13 and len(iterations) == 1


def test_route_result_membership_keeps_partial_empty_and_local_contracts():
    first, other = (RouteDemand(n, GroundStationRouteDemandTarget(G)) for n in (A, B))
    reachable = ReachableRouteOutcome(first, G, (A, G))
    assert _validate_route_result((first, other), RoutePlanningResult((reachable,))) == {first: reachable}
    assert _validate_route_result((first,), RoutePlanningResult()) == {}
    local = LocalRouteOutcome(other)
    assert _validate_route_result((first,), RoutePlanningResult((local,))) == {other: local}
    with pytest.raises(ValueError, match='duplicate'):
        _validate_route_result((first,), RoutePlanningResult((reachable, reachable)))
    for outside in (ReachableRouteOutcome(other, G, (B, G)),
                    UnreachableRouteOutcome(other, RouteUnreachableReason.NO_ELIGIBLE_PATH)):
        with pytest.raises(ValueError, match='requested demands'):
            _validate_route_result((first,), RoutePlanningResult((outside,)))


@pytest.fixture(scope='module', params=['empty', 'unmanaged', 'default', 'mixed-nodes'])
def pin_material(request, tmp_path_factory):
    directory = tmp_path_factory.mktemp('pins-' + request.param)
    unmanaged = BpfProgram(ROOT / 'templates/dataplane/pass.bpf.c')
    if request.param == 'empty':
        selection, expected = DataPlane(), 7
    elif request.param == 'unmanaged':
        selection, expected = unmanaged, 10
    elif request.param == 'default':
        selection, expected = None, 19
    else:
        selection = DataPlane((DataPlaneBinding(program(directory), AttachmentScope(nodes=(A,))),
                              DataPlaneBinding(unmanaged, AttachmentScope(nodes=(G,))),
                              DataPlaneBinding(BpfProgram(_DEFAULT), AttachmentScope(nodes=(B,)))))
        expected = 16  # One object per node; two managed profiles and one unmanaged.
    class Empty:
        def plan_route_outcomes(self, *args, **kwargs): return RoutePlanningResult()
    # Prepare actual objects before any process/FD operations are replaced.
    paths, _ = produce(directory, model=Empty(), selection=selection, ends=(0,))
    return paths[0], expected


@pytest.mark.parametrize('telemetry', [False, True])
@pytest.mark.parametrize('continuation', [False, True])
def test_pin_statistics_match_actual_helper_pins(tmp_path, monkeypatch, pin_material, telemetry, continuation):
    path, expected = pin_material
    with closing(_open_runner_artifact(path)) as artifact:
        maps, pins, live, objects = {}, {}, {}, []
        serial = iter(range(100000, 101000))
        def create(name, **kwargs):
            fd = next(serial); maps[fd] = {}; return fd
        def pin(fd, path):
            assert path not in pins
            pins[path] = fd; path.touch()
        def load(program, **kwargs):
            obj = SimpleNamespace(closed=False)
            def close(): obj.closed = True
            obj.close = close; objects.append(obj)
            return obj, next(serial)
        def retain(nodes):
            for node in nodes:
                live[node['key']] = {**node, 'maps': {}, 'namespace_fd': next(serial),
                    'netlink': SimpleNamespace(initial_links=lambda **kw: 1, set_link=lambda *a, **kw: None,
                        create_dummy=lambda name: None, link_index=lambda name: 2, delete_link=lambda index: None,
                        address=lambda *a, **kw: None, close=lambda: None)}
        monkeypatch.setattr(command, '_LIVE_NODES', live)
        for name, value in [('_INITIALIZATION_REQUESTED', False), ('_OWNED_PIN_ROOT', None),
                            ('_OWNED_NODE_ADDRESSES', set()), ('_OWNED_RETIREMENTS', {}),
                            ('_GENERATION_FD', -1), ('_GENERATION_READERS_FD', -1),
                            ('_GENERATIONS', {}), ('_VERSIONS', {}), ('_READY_GENERATIONS', set())]:
            monkeypatch.setattr(command, name, value)
        monkeypatch.setattr(command, '_pin_root', lambda _: tmp_path / 'pins')
        monkeypatch.setattr(command, '_reserve_foundation_descriptors', lambda *a: None)
        monkeypatch.setattr(command, '_retain_namespaces', retain)
        monkeypatch.setattr(command, '_audit_local_ingress', lambda *a: None)
        monkeypatch.setattr(command, '_create_local_ingress', lambda *a: None)
        monkeypatch.setattr(command, '_probe_batch_update_support', lambda: True)
        monkeypatch.setattr(command, '_audit_node_addresses', lambda request: {'result': 'passed',
            'selected_node_address_range': request['node_address_range'], 'audited_node_namespaces': len(request['nodes'])})
        monkeypatch.setattr(command, '_create_map', create)
        monkeypatch.setattr(command, '_pin_object', pin)
        monkeypatch.setattr(command, '_open_pinned', pins.__getitem__)
        monkeypatch.setattr(command, '_load_object', load)
        monkeypatch.setattr(command, '_update_map', lambda fd, key, value: maps[fd].__setitem__(key, value))
        original_close = command.os.close
        monkeypatch.setattr(command.os, 'close', lambda fd: None if fd >= 100000 else original_close(fd))
        runtime = _TcBpfRuntimeRealization(executor=SimpleNamespace(execute=command._dispatch_action), pin_root='/pins',
            continuation_enabled=continuation, telemetry_enabled=telemetry,
            bindings={node: SimpleNamespace(pid=i+10, process_start_time_ticks=1, netns_inode=i+20)
                      for i, node in enumerate(artifact.physical_world_projection.node_ids)})
        try:
            result = runtime.initialize(artifact.physical_world_projection, zone_prefixes=artifact.zone_prefixes,
                runtime_capacity_demand=artifact.runtime_capacity_demand, dataplane=artifact.dataplane)
            assert len(pins) == expected + int(continuation) + 2 * int(telemetry)
            assert result['capacity_audit']['pinned_object_count'] == len(pins)
            assert not any(p.name in {'packets', '.rodata.skyseed', '.rodata.skydata'} for p in pins)
        finally:
            runtime.cleanup()
        assert all(obj.closed for obj in objects) and not live and not (tmp_path / 'pins').exists()


def test_node_scope_covers_all_network_ingress_and_rejects_endpoint_selection(tmp_path):
    from skyseed.backend.bpf.dataplane.translation import _BpfTranslation
    from skyseed.backend.bpf.dataplane.materials import _MaterialCursor
    from skyseed.backend.execution_materials.mechanics import _interface_name_for_source
    selected = program(tmp_path)
    endpoints = (
        LogicalExit(OperationalConnectionId(OperationalConnectionKind.INTER_SATELLITE, A, B), A),
        LogicalExit(OperationalConnectionId(OperationalConnectionKind.GROUND_STATION_ACCESS, A, G), A),
    )
    with pytest.raises(TypeError):
        AttachmentScope(endpoints=endpoints)
    selection = DataPlane((DataPlaneBinding(selected, AttachmentScope(nodes=(A,))),))
    paths, _ = produce(tmp_path / 'scoped', selection=selection, ends=(0,))
    with closing(_open_runner_artifact(paths[0])) as artifact:
        translation = _BpfTranslation(artifact.physical_world_projection, (), dataplane_program=selection)
        foundation = translation.foundation()
        assert len(foundation['programs']) == 1
        owner = next(n for n in foundation['nodes'] if n['key'] == A.value)
        assert owner['program'] == 0 and 'instances' not in owner
        names = tuple(_interface_name_for_source(e.connection, e.source) for e in endpoints)
        ports = {(A.value, name): i+1 for i,name in enumerate(names)}
        node = SimpleNamespace(node_id=A, key=A.value, active_interfaces=names, routes=())
        assert len(translation._encode_node(node, ports)[0]) == 2
        draft = _MaterialCursor(artifact.physical_world_projection.node_ids, foundation).apply(next(iter(artifact.boundaries())))
        assert len(draft.generation_by_node[A].active) == 2
