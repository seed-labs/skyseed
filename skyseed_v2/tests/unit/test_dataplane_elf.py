"""Real offline C/ELF preparation and libbpf ownership/binding boundaries."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from skyseed_v2.backend.bpf import BpfProgram
from skyseed_v2.backend.bpf.dataplane.program import _prepare_program, _INCLUDE
from skyseed_v2.backend.bpf.dataplane.object_contract import (
    _load_object, _validate_program, _FRAMEWORK_MAPS, _CONFIG_MAP, _CONFIG,
)
from skyseed_v2.backend.linux.elf import _BpfObject
from skyseed_v2.backend.bpf.dataplane.telemetry_abi import _TELEMETRY_EVENT, _decode_telemetry_event
from test_runner_batches import _produce

_SAMPLE = Path(__file__).parents[2] / 'templates/dataplane/pass.bpf.c'


@pytest.fixture(scope='module')
def custom():
    return _prepare_program(BpfProgram(_SAMPLE))


def test_c_local_dependencies_multiple_units_and_precompiled_detached(tmp_path, monkeypatch):
    (tmp_path / 'policy.h').write_text('int choice(void);\n')
    (tmp_path / 'policy.c').write_text('#include "policy.h"\nint choice(void) { return 0; }\n')
    source = tmp_path / 'entry.c'
    source.write_text('#include "skyseed.h"\n#include "policy.h"\nstatic int handle(struct sky_context *ctx) { return choice(); }\nSKYSEED_PROGRAM(handle);\n')
    prepared = _prepare_program(BpfProgram((source, tmp_path / 'policy.c')))
    assert str(tmp_path / 'policy.h') in prepared['provenance']['dependencies']
    assert str(_INCLUDE / 'skyseed.h') in prepared['provenance']['dependencies']
    assert len(prepared['provenance']['commands']) == 2
    output = tmp_path / 'prepared.o'
    output.write_bytes(base64.b64decode(prepared['elf']))
    source.unlink(); (tmp_path / 'policy.h').unlink(); (tmp_path / 'policy.c').unlink()
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('object input must not invoke compiler'))
    restored = _prepare_program(BpfProgram(output))
    assert restored['elf'] == prepared['elf']
    assert restored['declaration'] == prepared['declaration']
    assert restored['provenance']['compiler'] is None


@pytest.mark.parametrize('telemetry', [False, True])
@pytest.mark.parametrize('continuation', [False, True])
def test_objects_reuse_actual_shared_descriptors_and_own_private_maps(monkeypatch, custom, telemetry, continuation):
    reused, configurations, disabled, closed = [], [], [], []
    original_close = _BpfObject.close
    def reuse(self, name, fd, capacity):
        reused.append((self, name, fd, capacity))
    def load(self):
        configurations.append(_CONFIG.unpack(self.initial_value(_CONFIG_MAP)))
        assert 'packets' in self.maps
        return 111
    def close(self):
        closed.append(self)
        original_close(self)
    monkeypatch.setattr(_BpfObject, 'reuse', reuse)
    monkeypatch.setattr(_BpfObject, 'load', load)
    monkeypatch.setattr(_BpfObject, 'close', close)
    # set_autocreate is an offline libbpf operation; record and execute it.
    original_disable = _BpfObject.disable
    def disable(self, name):
        disabled.append(name); original_disable(self, name)
    monkeypatch.setattr(_BpfObject, 'disable', disable)
    bindings = {identity: 100 + i for i, (identity, _) in enumerate(_FRAMEWORK_MAPS.values())}
    capacities = {name: 32 for name in bindings}
    objects = []
    for slot in range(2):
        obj, fd = _load_object(custom, bindings=bindings, capacities=capacities, node_slot=slot,
                               telemetry=telemetry, continuation=continuation)
        objects.append(obj)
        assert fd == 111 and not closed
    assert configurations == [(2, 0, slot, int(telemetry), int(continuation)) for slot in (0, 1)]
    for name, (identity, _) in _FRAMEWORK_MAPS.items():
        omitted = name in {'traffic_events', 'traffic_losses'} and not telemetry or name == 'gen_readers' and not continuation
        calls = [entry for entry in reused if entry[1] == name]
        assert len(calls) == (0 if omitted else 2)
        assert all(entry[2] == bindings[identity] for entry in calls)
        assert disabled.count(name) == (2 if omitted else 0)
    assert all(entry[1] != 'packets' for entry in reused)
    for obj in objects: obj.close()
    assert closed == objects and all(obj.pointer is None for obj in objects)


@pytest.mark.parametrize('stage', ['reuse', 'load'])
def test_libbpf_failure_closes_partially_prepared_private_object(monkeypatch, custom, stage):
    closed = []
    original_close = _BpfObject.close
    def close(self):
        closed.append(self); original_close(self)
    def fail(*args): raise OSError('primary libbpf failure')
    monkeypatch.setattr(_BpfObject, 'close', close)
    monkeypatch.setattr(_BpfObject, 'reuse', fail if stage == 'reuse' else lambda *a: None)
    monkeypatch.setattr(_BpfObject, 'load', fail)
    with pytest.raises(OSError, match='primary libbpf failure'):
        _load_object(custom, bindings={v[0]: 123 for v in _FRAMEWORK_MAPS.values()},
                     capacities={v[0]: 32 for v in _FRAMEWORK_MAPS.values()}, node_slot=0,
                     telemetry=True, continuation=True)
    assert len(closed) == 1 and closed[0].pointer is None


def test_custom_materials_reuse_prepared_object_across_detached_continuation(tmp_path, monkeypatch):
    import skyseed_v2.backend.execution_materials.sealing as sealing
    writer = sealing._RunnerArtifactWriter
    import test_runner_batches as batches
    def selected(*args, **kwargs):
        if kwargs.get('continuation') is None:
            kwargs['dataplane_program'] = BpfProgram(_SAMPLE)
        return writer(*args, **kwargs)
    monkeypatch.setattr(batches, '_RunnerArtifactWriter', selected)
    _, paths = _produce(tmp_path / 'produce', (0, 10, 30))
    detached = tmp_path / 'detached'
    shutil.copytree(paths[0].parent.parent, detached)
    shutil.rmtree(tmp_path / 'produce')
    script = '''
import builtins, json, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('skyseed_v2.backend.bpf.dataplane.program', 'skyseed_v2.backend.bpf.dataplane.translation', 'skyseed_v2.backend.compiler')):
        raise AssertionError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from pathlib import Path
from skyseed_v2.backend.execution_materials.codec import _open_runner_artifact
from skyseed_v2.backend.bpf.dataplane.runtime import _TcBpfRuntimeRealization
from types import SimpleNamespace
class Executor:
    def execute(self, action, request):
        if action == 'initialize':
            return {'collision_audit': {'result':'passed', 'selected_node_address_range':request['node_address_range'], 'audited_node_namespaces':len(request['nodes'])}, 'installed_node_addresses':[n['key'] for n in request['nodes']], 'bpf_batch_update_supported':True}
        if action == 'prewrite_generation':
            assert all(not n['active_entries'] and not n['route_entries'] for n in request['generation_nodes'])
            return {'preloaded_active_links':0,'preloaded_routes':0}
        if action == 'cleanup': return {'pin_root_removed':True,'errors':[]}
        raise AssertionError(action)
a = [_open_runner_artifact(Path(p)) for p in sys.argv[1:]]
r = _TcBpfRuntimeRealization(executor=Executor(), pin_root='/pins', continuation_enabled=True, batch_capacity_factor=4,
    bindings={n:SimpleNamespace(pid=i+10, process_start_time_ticks=1, netns_inode=i+20) for i,n in enumerate(a[0].physical_world_projection.node_ids)})
try:
    r.initialize(a[0].physical_world_projection, dataplane=a[0].dataplane, zone_prefixes=a[0].zone_prefixes, runtime_capacity_demand=a[0].runtime_capacity_demand)
    for i,b in enumerate(a):
        assert b.dataplane['programs'][0] == a[0].dataplane['programs'][0]
        r.prepare_all(b.boundaries(), logical_ports=b.logical_ports, retirements=b.retirements, **({} if i==0 else {'runtime_capacity_demand': b.runtime_capacity_demand}))
finally:
    r.cleanup()
    for b in a: b.close()
print('detached-custom-ok')
'''
    result = subprocess.run([sys.executable, '-c', script, *[str(detached / p.parent.name / p.name) for p in paths]],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'detached-custom-ok' in result.stdout


def test_unknown_result_metadata_and_generic_pass_are_not_local_delivery():
    from tools.observation.traffic.reader import _is_endpoint_packet
    payload = _TELEMETRY_EVENT.pack(1, 0, 3, 4, 0, 0, 0, 98, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    record = _decode_telemetry_event(payload)
    assert record['decision'] == 'pass' and record['reason'] == 'unknown'
    assert record['source_ipv4'] is None and record['protocol'] is None
    assert record['selected_egress_ifindex'] is None
    record.update(node_id='local-node', destination_ipv4='100.64.0.1')
    assert not _is_endpoint_packet(record, {'100.64.0.1': {'node_id': 'local-node'}})
    assert not _is_endpoint_packet(record, None)
    record['reason'] = 'local-route'
    assert _is_endpoint_packet(record, None)

@pytest.mark.parametrize('fail_pin', [False, True])
def test_private_state_survives_generations_and_closes_on_cleanup(monkeypatch, tmp_path, custom, fail_pin):
    from skyseed_v2.backend.bpf.dataplane import runtime_command as command
    from test_dataplane_preparation import _node_foundation
    foundation, _ = _node_foundation(1)
    foundation['programs'][0] = custom
    maps, pins, live, names, closed = {}, {}, {}, {}, []
    next_fd = iter(range(100000, 100100))
    obj = SimpleNamespace(private_count=77, close=lambda: closed.append('object'))
    def create(name, **kwargs):
        fd = next(next_fd); names[name] = fd; maps[fd] = {}; return fd
    def pin(fd, path):
        if fail_pin and path.name == 'tc_ingress@0': raise OSError('primary pin failure')
        pins[path] = fd; path.touch()
    def retain(nodes):
        for node in nodes:
            live[node['key']] = {**node, 'maps': {}, 'namespace_fd': next(next_fd),
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
    monkeypatch.setattr(command, '_retain_namespaces', retain)
    monkeypatch.setattr(command, '_audit_local_ingress', lambda *a: None)
    monkeypatch.setattr(command, '_create_local_ingress', lambda *a: None)
    monkeypatch.setattr(command, '_probe_batch_update_support', lambda: True)
    monkeypatch.setattr(command, '_audit_node_addresses', lambda _: {'result': 'passed'})
    monkeypatch.setattr(command, '_create_map', create)
    monkeypatch.setattr(command, '_pin_object', pin)
    monkeypatch.setattr(command, '_open_pinned', lambda path: pins[path])
    monkeypatch.setattr(command, '_load_object', lambda *a, **kw: (obj, 100999))
    monkeypatch.setattr(command, '_update_map', lambda fd, key, value: maps[fd].__setitem__(key, value))
    monkeypatch.setattr(command, '_update_u64_map', lambda fd, key, value: maps[fd].__setitem__(key, value))
    monkeypatch.setattr(command, '_lookup_map', lambda fd, key: maps[fd].get(key))
    monkeypatch.setattr(command, '_lookup_u64_map', lambda fd, key: maps[fd].get(key))
    monkeypatch.setattr(command, '_delete_map', lambda fd, key: maps[fd].pop(key, None) is not None)
    original_close = command.os.close
    monkeypatch.setattr(command.os, 'close', lambda fd: closed.append(fd) if fd >= 100000 else original_close(fd))
    nodes = [{**node, 'pid':1, 'netns_inode':1, 'process_start_time_ticks':1,
              'active_links_capacity':8, 'routes_capacity':8, 'bindings_capacity':8, 'table_capacities': []} for node in foundation['nodes']]
    request = {'pin_root':'/pins', 'dataplane':foundation, 'nodes':nodes,
               'retirement_group_end':0x53530002, 'resident_generation_capacity':8,
               'inactive_generation':0xffffffff, 'telemetry_enabled':False, 'continuation_enabled':True,
               'node_address_range':foundation['node_address_range'], 'zone_prefixes':(), 'worker_count':1}
    if fail_pin:
        with pytest.raises(command._InitializationFailure, match='primary pin failure') as error:
            command._initialize(request)
        assert error.value.evidence['initialization_outcome'] == 'cleaned'
    else:
        command._initialize(request)
        assert set(names) == {'active_gen', 'gen_readers', 'ingress_bind', 'egress_bind'}
        for generation in (0, 1):
            command._prewrite_generation({'generation':generation, 'seal':True, 'bpf_batch_update_supported':True,
                'generation_nodes':[{'key':nodes[0]['key'], 'version':0, 'active_entries':[], 'route_entries':[]}]})
        maps[names['active_gen']][0] = 1
        maps[names['gen_readers']][0] = 1
        assert command._reclaim_generations({'before_generation':1})['reader_wait']
        maps[names['gen_readers']][0] = 0
        assert command._reclaim_generations({'before_generation':1})['reclaimed_generations'] == [0]
        assert obj.private_count == 77 and 'object' not in closed
        assert command._cleanup(request)['cleanup_confirmed']
    assert closed.count('object') == 1
    assert not live and not (tmp_path / 'pins').exists()


@pytest.mark.parametrize('damage', ['entry', 'layout', 'abi', 'pin'])
def test_actual_object_declarations_reject_incompatible_inputs(tmp_path, damage):
    header = (_INCLUDE / 'skyseed.h').read_text()
    source = _SAMPLE.read_text()
    if damage == 'entry': header = header.replace('int skyseed_tc(', 'int unrelated_tc(')
    if damage == 'layout': header = header.replace('gen_readers, BPF_MAP_TYPE_HASH, __u32, __u64', 'gen_readers, BPF_MAP_TYPE_HASH, __u32, __u32')
    if damage == 'abi': header = header.replace('2, SKY_MANAGED_TABLES, 0, 1, 1', '1, SKY_MANAGED_TABLES, 0, 1, 1')
    if damage == 'pin': source = source.replace('__uint(max_entries, 1);', '__uint(max_entries, 1); __uint(pinning, 1);')
    (tmp_path / 'skyseed.h').write_text(header)
    (tmp_path / 'bad.c').write_text(source)
    with pytest.raises(ValueError):
        _prepare_program(BpfProgram(tmp_path / 'bad.c'))

@pytest.mark.parametrize('capacity', [0, -1, 1 << 32, True])
def test_capacity_binding_cannot_silently_wrap_the_kernel_abi(custom, capacity):
    with _BpfObject(base64.b64decode(custom['elf'])) as obj:
        # This rejects before the fd-reuse operation, so no kernel calls occur.
        with pytest.raises(ValueError, match='u32 ABI'):
            obj.reuse('active_gen', 123, capacity)


def test_experiment_assembly_selects_custom_program_during_offline_generation(tmp_path):
    from examples.Sky00_tiny_network import tiny_network as tiny
    from seedemu.compiler import Docker
    from skyseed_v2.experiment import ExperimentController
    from skyseed_v2.backend.execution_materials.codec import _open_runner_artifact
    network = tiny.build_emulator()
    controller = ExperimentController(network, tiny.create_simulator(network), tmp_path / 'custom-experiment',
        entry_path=Path(tiny.__file__), input_files=(tiny.ZONE_LAYOUT_FILE,),
        dataplane_program=BpfProgram(_SAMPLE), traffic_enabled=False,
        docker_compiler=Docker(internetMapEnabled=False))
    output = controller.generate()  # SEED emits build contexts; no deployment/build/run.
    artifact = _open_runner_artifact(output / 'results/compiler/runner-artifact/manifest.json')
    try:
        assert not artifact.dataplane['programs'][0]['declaration']['managed_tables']
        assert 'packets' in artifact.dataplane['programs'][0]['declaration']['maps']
        assert 'routes' not in artifact.dataplane['programs'][0]['declaration']['maps']
        assert str(_SAMPLE) in artifact.dataplane['programs'][0]['provenance']['dependencies']
    finally:
        artifact.close()
