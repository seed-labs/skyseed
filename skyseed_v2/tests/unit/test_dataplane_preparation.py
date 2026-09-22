"""Calculation/execution boundary, relocations and compact detached replay."""
import base64
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import struct
import subprocess
import sys
import zlib

import pytest

from skyseed_v2.backend.bpf.dataplane.materials import (
    _decode_foundation, _encode_foundation, _MaterialCursor,
    _validate_foundation,
)
from skyseed_v2.backend.bpf.dataplane.translation import _BpfTranslation
from skyseed_v2.backend.execution_materials.actions import _PhysicalWorldProjection
from skyseed_v2.backend.execution_materials.codec import _open_runner_artifact, _pack, _boundary_to_data
from skyseed_v2.shared import NodeId
from test_runner_batches import _produce
from test_runner_runtime_optimization import _capacity_transition_sequence


@pytest.mark.parametrize('corruption', ['version', 'map-layout', 'object-digest', 'declaration', 'byteorder'])
def test_material_foundation_rejects_incompatible_contract_and_bindings(corruption):
    projection, _ = _capacity_transition_sequence()
    foundation = _BpfTranslation(projection, ()).foundation()
    if corruption == 'version': foundation['contract'] = 'skyseed-default-tc-bpf/0'
    elif corruption == 'map-layout': foundation['programs'][0]['declaration']['maps']['routes'][1] = 4
    elif corruption == 'object-digest': foundation['programs'][0]['sha256'] = '0' * 64
    elif corruption == 'declaration': foundation['programs'][0]['declaration']['maps']['active_gen'][1] = 8
    else: foundation['byteorder'] = 'big'
    with pytest.raises(ValueError):
        _decode_foundation(_encode_foundation(foundation), tuple(n.value for n in projection.node_ids))


def _node_foundation(count):
    nodes = tuple(NodeId(65001, 'satellite', f'sat-{i:05d}') for i in range(count))
    projection = _PhysicalWorldProjection(nodes, ())
    return _BpfTranslation(projection, ()).foundation(), tuple(n.value for n in nodes)


def test_material_foundation_round_trips_node_metadata_above_former_size_limit():
    foundation, node_keys = _node_foundation(42_000)
    raw = json.dumps(foundation, sort_keys=True, separators=(',', ':')).encode()
    assert len(raw) > 4 << 20
    assert _validate_foundation(foundation, node_keys) is foundation
    assert _decode_foundation(_encode_foundation(foundation), node_keys) == foundation


@pytest.mark.parametrize('corruption', [
    'base64', 'zlib', 'empty', 'truncated-header', 'truncated-payload',
    'truncated-trailer', 'checksum', 'trailing-data', 'concatenated-stream', 'json',
])
def test_material_foundation_rejects_malformed_streams(corruption):
    foundation, node_keys = _node_foundation(1)
    material = _encode_foundation(foundation)
    compressed = base64.b64decode(material['content'])
    if corruption == 'base64':
        material['content'] = '!invalid-base64!'
    else:
        damaged = {
            'zlib': b'not a zlib stream',
            'empty': b'',
            'truncated-header': compressed[:1],
            'truncated-payload': compressed[:len(compressed) // 2],
            'truncated-trailer': compressed[:-1],
            'checksum': compressed[:-1] + bytes([compressed[-1] ^ 0xff]),
            'trailing-data': compressed + b'trailing',
            'concatenated-stream': compressed + compressed,
            'json': zlib.compress(b'{'),
        }[corruption]
        material['content'] = base64.b64encode(damaged).decode()
    with pytest.raises(ValueError, match='invalid prepared DataPlane foundation'):
        _decode_foundation(material, node_keys)


def _translated_transition():
    projection, boundaries = _capacity_transition_sequence()
    connections = tuple(dict.fromkeys(op.intent.connection_id for b in boundaries for op in b.operations if op.domain == 'resource'))
    ports = {c: (i * 2 + 1, i * 2 + 2) for i, c in enumerate(connections)}
    translation = _BpfTranslation(projection, ())
    prepared = tuple(translation.translate(b, ports) for b in boundaries)
    return projection, prepared, translation


def test_prepared_actions_and_patches_preserve_parameter_zone_and_route_changes():
    projection, boundaries, translation = _translated_transition()
    cursor = _MaterialCursor(projection.node_ids)
    drafts = [cursor.apply(boundary) for boundary in boundaries]
    assert [d.generation for d in drafts] == [0, 0, 0, 1, 2]
    assert drafts[1].generation_nodes is drafts[0].generation_nodes is drafts[2].generation_nodes
    assert not boundaries[1].executable['node_patches'] and not boundaries[2].executable['node_patches']
    assert drafts[1].request['operation_nodes'][0]['qdisc'][0]['delay_us'] == 20
    assert not drafts[2].request['operation_nodes']
    assert sum(len(n['zone_address_add']) for n in drafts[3].request['operation_nodes']) == 1
    assert sum(len(n['zone_address_delete']) for n in drafts[3].request['operation_nodes']) == 1
    assert all('route_replace' not in n and 'route_withdraw' not in n for d in drafts for n in d.request['operation_nodes'])
    assert cursor.resources == set()
    assert {n.node_id: dict(n.routes) for n in drafts[-1].generation_nodes} == translation._previous.routes
    assert len(_pack(_boundary_to_data(boundaries[2])["executable"])) < len(_pack(_boundary_to_data(boundaries[0])["executable"])) / 3


@pytest.mark.parametrize('corruption', ['base', 'prefix', 'encoded-route', 'generation', 'missing-node'])
def test_prepared_replay_rejects_invalid_versions_or_encoded_rows(corruption):
    projection, boundaries, _ = _translated_transition()
    value = deepcopy(boundaries[0].executable)
    patch = value['node_patches'][0]
    if corruption == 'base': patch['base'] = 0
    elif corruption == 'prefix': patch['prefix'] = b'bad!'
    elif corruption == 'encoded-route': patch['route_put'][0][3] = b'wrong'
    elif corruption == 'generation': value['request']['generation'] = 2
    else: value['node_patches'].pop()
    with pytest.raises(ValueError):
        _MaterialCursor(projection.node_ids).apply(replace(boundaries[0], executable=value))


def test_runner_consumes_three_detached_deliveries_without_translation_imports(tmp_path):
    _, paths = _produce(tmp_path / 'produce', (0, 10, 30))
    script = r'''
import builtins, json, sys
from types import SimpleNamespace
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('skyseed_v2.backend.compiler', 'skyseed_v2.backend.bpf.dataplane.translation', 'skyseed_v2.backend.bpf.dataplane.program', 'skyseed_v2.experiment.producer')) or name.endswith('execution_materials.sealing'):
        raise AssertionError('runtime imported calculation: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from skyseed_v2.backend.execution_materials.codec import _open_runner_artifact
from skyseed_v2.backend.bpf.dataplane.runtime import _TcBpfRuntimeRealization
class Executor:
    def execute(self, action, request):
        if action == 'initialize':
            return {'collision_audit': {'result': 'passed', 'selected_node_address_range': request['node_address_range'], 'audited_node_namespaces': len(request['nodes'])}, 'installed_node_addresses': [n['key'] for n in request['nodes']], 'bpf_batch_update_supported': False}
        if action == 'prewrite_generation':
            return {'preloaded_active_links': sum(len(n['active_entries']) for n in request['generation_nodes']), 'preloaded_routes': sum(len(n['route_entries']) for n in request['generation_nodes'])}
        if action == 'cleanup': return {'pin_root_removed': True, 'errors': []}
        raise AssertionError(action)
artifacts = [_open_runner_artifact(__import__('pathlib').Path(p)) for p in sys.argv[1:]]
a = artifacts[0]
bindings = {n: SimpleNamespace(pid=i+10, process_start_time_ticks=10, netns_inode=i+20) for i,n in enumerate(a.physical_world_projection.node_ids)}
r = _TcBpfRuntimeRealization(executor=Executor(), pin_root='/pins', bindings=bindings, continuation_enabled=True, batch_capacity_factor=4)
try:
    r.initialize(a.physical_world_projection, zone_prefixes=a.zone_prefixes, runtime_capacity_demand=a.runtime_capacity_demand, dataplane=a.dataplane)
    plans=[]
    for i,a in enumerate(artifacts):
        plans.extend(r.prepare_all(a.boundaries(), logical_ports=a.logical_ports, retirements=a.retirements, **({} if i==0 else {'runtime_capacity_demand': a.runtime_capacity_demand})))
    assert len(plans)==4 and len({p.nominal_time for p in plans})==4
    assert all(p.generation==0 for p in plans)
finally:
    r.cleanup()
    for a in artifacts: a.close()
print('detached Runner: 3 deliveries, 4 boundaries, no calculation imports')
'''
    result = subprocess.run([sys.executable, '-B', '-c', script, *map(str, paths)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('failure', ['routes', 'program-load'])
def test_prepared_foundation_failure_closes_allocated_descriptors_and_addresses(monkeypatch, tmp_path, failure):
    from types import SimpleNamespace
    from skyseed_v2.backend.bpf.dataplane import runtime_command as command
    projection, _ = _capacity_transition_sequence()
    foundation = _BpfTranslation(projection, ()).foundation()
    addresses, allocated, closed, live = set(), [], [], {}
    def allocate(name, **kwargs):
        if name == failure:
            raise OSError('primary map allocation failure')
        fd = 50000 + len(allocated)
        allocated.append(fd)
        return fd
    def address(index, value, *, delete=False, exclusive=False):
        addresses.discard(value) if delete else addresses.add(value)
    def retain(nodes):
        for node in nodes:
            live[node['key']] = {**node, 'maps': {}, 'namespace_fd': 60000,
                'netlink': SimpleNamespace(initial_links=lambda **kw: 1, set_link=lambda *a, **kw: None,
                                          create_dummy=lambda name: None, link_index=lambda name: 2,
                                          delete_link=lambda index, value=node['node_address']: addresses.discard(value),
                                          address=address, close=lambda: None)}
    monkeypatch.setattr(command, '_LIVE_NODES', live)
    monkeypatch.setattr(command, '_INITIALIZATION_REQUESTED', False)
    monkeypatch.setattr(command, '_OWNED_PIN_ROOT', None)
    monkeypatch.setattr(command, '_OWNED_NODE_ADDRESSES', set())
    monkeypatch.setattr(command, '_GENERATION_FD', -1)
    monkeypatch.setattr(command, '_GENERATION_READERS_FD', -1)
    monkeypatch.setattr(command, '_pin_root', lambda _: tmp_path / 'pins')
    monkeypatch.setattr(command, '_retain_namespaces', retain)
    monkeypatch.setattr(command, '_audit_local_ingress', lambda *a: None)
    monkeypatch.setattr(command, '_create_local_ingress', lambda *a: None)
    monkeypatch.setattr(command, '_probe_batch_update_support', lambda: True)
    monkeypatch.setattr(command, '_audit_node_addresses', lambda _: {'result': 'passed'})
    monkeypatch.setattr(command, '_create_map', allocate)
    monkeypatch.setattr(command, '_pin_object', lambda fd, path: path.touch())
    monkeypatch.setattr(command, '_update_map', lambda *args: None)
    def fail_load(*args, **kwargs):
        raise OSError('primary program load failure')
    monkeypatch.setattr(command, '_load_object', fail_load)
    import os
    original_close = os.close
    monkeypatch.setattr(command.os, 'close', lambda fd: closed.append(fd) if fd >= 50000 else original_close(fd))
    with pytest.raises(command._InitializationFailure, match='primary') as error:
        command._initialize({'pin_root': '/pins', 'dataplane': foundation,
            'nodes': [{**node, 'pid': 1, 'netns_inode': 1, 'process_start_time_ticks': 1,
                       'active_links_capacity': 10, 'routes_capacity': 10, 'bindings_capacity': 10, 'table_capacities': [{'program': 0, 'active': 10, 'routes': 10}]} for node in foundation['nodes']],
            'retirement_group_end': 0x53530002, 'resident_generation_capacity': 2,
            'inactive_generation': 0xffffffff, 'telemetry_enabled': False,
            'node_address_range': foundation['node_address_range'], 'zone_prefixes': (), 'worker_count': 1})
    assert error.value.evidence['initialization_outcome'] == 'cleaned'
    assert all(closed.count(fd) == 1 for fd in allocated)
    assert not live and not addresses and not (tmp_path / 'pins').exists()


def test_artifact_rejects_retirement_binding_outside_owned_range(tmp_path):
    _, paths = _produce(tmp_path / 'retirement', (30,))
    path = paths[0]
    manifest = json.loads(path.read_text())
    reference = next(iter(manifest['retirements']['bindings']))
    manifest['retirements']['bindings'][reference][1] = 0
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='retirement'):
        _open_runner_artifact(path)


@pytest.mark.parametrize('telemetry', [False, True])
def test_changed_versions_across_deliveries_wait_for_readers_and_keep_shared_versions(monkeypatch, telemetry):
    from types import SimpleNamespace
    from skyseed_v2.backend.bpf.dataplane.runtime import _TcBpfRuntimeRealization
    from skyseed_v2.backend.bpf.dataplane import runtime_command as command
    from skyseed_v2.backend.bpf.dataplane.materials import _RuntimeCapacityDemandAccumulator
    from skyseed_v2.backend.execution_materials.codec import _PackedBoundaryReader, _DecodedIdentities
    from test_runner_runtime_optimization import _Kernel
    projection, boundaries = _capacity_transition_sequence()
    connections = tuple(dict.fromkeys(op.intent.connection_id for b in boundaries for op in b.operations if op.domain == 'resource'))
    ports = {c: (i*2+1, i*2+2) for i,c in enumerate(connections)}
    translator = _BpfTranslation(projection, (), continued=True)
    demand = _RuntimeCapacityDemandAccumulator(projection, ())
    kernel = _Kernel(monkeypatch)
    bindings = {n: SimpleNamespace(pid=i+10, process_start_time_ticks=10, netns_inode=i+20) for i,n in enumerate(projection.node_ids)}
    runtime = _TcBpfRuntimeRealization(executor=kernel, pin_root='/pins', bindings=bindings,
        telemetry_enabled=telemetry, continuation_enabled=True, batch_capacity_factor=4)
    work = []
    try:
        for index, source in enumerate((boundaries[:3], boundaries[3:4], boundaries[4:])):
            if index:
                demand.begin_batch()
                translator.begin_delivery()
            prepared = []
            for boundary in source:
                value = translator.translate(boundary, ports)
                demand.add(value)
                prepared.append(_PackedBoundaryReader(_pack(_boundary_to_data(value)), _DecodedIdentities()).boundary(projection))
            capacity = demand.snapshot()
            if index == 0:
                runtime.initialize(projection, zone_prefixes=(), dataplane=translator.foundation(), runtime_capacity_demand=capacity)
            plans = runtime.prepare_all(prepared, logical_ports=ports, retirements=translator.retirements(),
                **({} if index == 0 else {'runtime_capacity_demand': capacity}))
            work.append(runtime.preparation_timings())
            for plan in plans:
                runtime.activate(plan)
            if index:
                old = min(runtime._resident_generations)
                kernel.maps[1000000][old] = 1
                assert runtime.reclaim()['reader_wait'] is True
                assert old in runtime._resident_generations
                kernel.maps[1000000][old] = 0
                while runtime.reclaim().get('work_units'):
                    pass
                assert runtime._resident_generations == {index}
        assert sum(w['node_versions_written'] for w in work) == 8
        assert sum(w['node_versions_reused'] for w in work) == 1
        retained = next(n for n in projection.node_ids if n.entity_id == 'sat-b')
        assert command._GENERATIONS == {2: {n.value: (1 if n == retained else 2) for n in projection.node_ids}}
        assert command._VERSIONS[retained.value, 1]['references'] == 1
        assert all(identity[1] > 0 for identity in command._VERSIONS)
        assert sum(action == 'initialize' for action, _ in kernel.actions) == 1
    finally:
        runtime.cleanup()
