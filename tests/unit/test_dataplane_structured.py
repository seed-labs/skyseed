"""Small real planning/material flows and native execution of the authored C."""
from dataclasses import replace
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import json
import shutil
import struct
import subprocess
import sys

import pytest

from skyseed.backend.bpf.dataplane import (
    BpfProgram, RouteData, Record, U8, U16, U32, U64, I32, BOOL, NODE, EXIT,
    DataPlane, DataPlaneBinding, AttachmentScope, prepare_program,
)
from skyseed.backend.bpf.dataplane.materials import _MaterialCursor
from skyseed.backend.bpf.dataplane.program import _INCLUDE, _compiler
from skyseed.backend.bpf.dataplane.runtime import _TcBpfRuntimeRealization
from skyseed.backend.bpf.dataplane import runtime_command as command
from skyseed.backend.execution_materials.sealing import _RunnerArtifactWriter
from skyseed.backend.execution_materials.codec import _open_runner_artifact
from skyseed.experiment import Producer
from skyseed.simulator import (
    LocalRouteOutcome, RoutePlanningResult, RouteDisposition, PolicyRecord,
    RouteCandidate, LogicalExit, RouteDemand, GroundStationRouteDemandTarget,
    plan_to_data, plan_from_data,
)
from templates.dataplane.threshold.definition import DATA
from templates.dataplane.threshold.scene import create_scene, ThresholdRoutes, START, A, B, G

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / 'templates/dataplane/threshold/handler.bpf.c'


class ChangingRoutes(ThresholdRoutes):
    def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
        elapsed = (timestamp - START).total_seconds()
        if elapsed >= 15:
            return RoutePlanningResult()
        result = super().plan_route_outcomes(world, connections, assignments, demands, previous, timestamp=timestamp)
        # Only one node is covered; all other arrangements are intentionally omitted.
        return RoutePlanningResult(tuple(replace(v, data=PolicyRecord(threshold=128 if elapsed < 5 else 200))
                                         for v in result.outcomes if v.demand.source == A))


def produce(directory, *, model=None, selection=None, ends=(15,)):
    simulator = create_scene(model or ChangingRoutes(), duration=ends[-1])
    timeline = simulator.get_timeline()
    paths, plans, previous = [], [], None
    with Producer(timeline, simulator.get_physical_world(), simulator.get_physical_link_model(), simulator.get_planner()) as producer:
        for index, seconds in enumerate(ends):
            writer = _RunnerArtifactWriter(directory / f'batch-{index}', continuation=previous,
                final=index == len(ends)-1, dataplane_program=selection if previous is None else None)
            try:
                while (result := producer.produce_next_realization(delivery_end=START + timedelta(seconds=seconds))) is not None:
                    plans.append(producer.latest_plan)
                    writer.append(result)
                paths.append(writer.finalize())
            finally:
                writer.close()
            previous = writer
    return paths, plans


def program(tmp_path):
    return BpfProgram(SOURCE, data=DATA, authoring_directory=tmp_path / 'generated')


def test_structured_plan_encoding_data_change_reuse_and_withdrawal(tmp_path):
    selected = program(tmp_path)
    paths, plans = produce(tmp_path, selection=selected)
    assert all(plan_from_data(plan_to_data(p)) == p for p in plans)
    assert len(plans[1].transitions[0].route_outcome_transitions) == 1
    assert not plans[2].transitions[0].route_outcome_transitions
    assert plans[3].transitions[0].route_outcome_transitions[0].current is None
    with closing(_open_runner_artifact(paths[0])) as artifact:
        cursor = _MaterialCursor(artifact.physical_world_projection.node_ids, artifact.dataplane)
        drafts = [cursor.apply(b) for b in artifact.boundaries()]
        assert [d.generation for d in drafts] == [0, 1, 1, 2]
        assert drafts[2].generation_by_node[A] is drafts[1].generation_by_node[A]
        for node in (B, G):
            assert not drafts[0].generation_by_node[node].forwarding
        first = next(iter(drafts[0].generation_by_node[A].forwarding.values()))[1][4:]
        second = next(iter(drafts[1].generation_by_node[A].forwarding.values()))[1][4:]
        assert struct.unpack_from('<III', first) == (1, 2, 128)
        assert struct.unpack_from('<III', second) == (1, 2, 200)
        assert first[12:] == second[12:]
        assert not drafts[-1].generation_by_node[A].forwarding
        assert artifact.dataplane['programs'][0]['declaration']['maps']['routes'][2] == DATA.value_size


@pytest.fixture
def native_handler(tmp_path):
    native = tmp_path / 'native'
    (tmp_path / 'bpf').mkdir()
    shutil.copy(ROOT / 'tests/fixtures/dataplane/bpf_helpers.h', tmp_path / 'bpf/bpf_helpers.h')
    shutil.copy(_INCLUDE / 'skyseed.h', tmp_path / 'skyseed.h')
    (tmp_path / 'selected.c').write_text(SOURCE.read_text())
    DATA.generate_headers(tmp_path)
    subprocess.run([_compiler(), '-O2', '-no-pie',
        *([] if shutil.which('ld') else ['--ld-path=' + str(Path(sys.prefix) / 'bin/x86_64-conda-linux-gnu-ld'),
          '-nostdlib', '/usr/lib/x86_64-linux-gnu/crt1.o', '/usr/lib/x86_64-linux-gnu/crti.o',
          '/lib/x86_64-linux-gnu/libc.so.6', '/usr/lib/x86_64-linux-gnu/crtn.o',
          '-Wl,--dynamic-linker=/lib64/ld-linux-x86-64.so.2']), '-I', str(tmp_path), '-I', str(Path(sys.prefix) / 'include'),
        str(ROOT / 'tests/fixtures/dataplane/structured_native.c'), '-o', str(native)], check=True)
    return native


def test_native_handler_selects_actual_encoded_exits_and_rebinds(tmp_path, native_handler):
    native = native_handler
    # Compile all C before any kernel fixture can replace process/FD operations.
    paths, plans = produce(tmp_path / 'data', selection=program(tmp_path))
    with closing(_open_runner_artifact(paths[0])) as artifact:
        draft = _MaterialCursor(artifact.physical_world_projection.node_ids, artifact.dataplane).apply(next(iter(artifact.boundaries())))
        encoded = next(iter(draft.generation_by_node[A].forwarding.values()))[1][4:]
    (tmp_path / 'value.bin').write_bytes(encoded)
    exits = (struct.unpack_from('<I', encoded, 12)[0], struct.unpack_from('<I', encoded, 20)[0])
    for length, expected in ((64, exits[0]), (128, exits[1]), (200, exits[1])):
        for offset in (100, 200):
            result = subprocess.check_output([str(native), str(tmp_path / 'value.bin'), str(length), str(offset)], text=True)
            assert tuple(map(int, result.split())) == (7, expected + offset, 0, 1)

    original = plans[0].transitions[0].route_outcome_transitions[0].current
    ports = dict(zip((c.exit for c in original.candidates), exits))
    scenarios = (
        (replace(original, disposition=RouteDisposition.DROP, candidates=()), 2, 0),
        (replace(original, disposition=RouteDisposition.PASS, candidates=()), 0, 0),
        (replace(original, candidates=()), 2, 0),
        (replace(original, candidates=tuple(replace(c, data=PolicyRecord(weight=i))
                                           for i, c in enumerate(original.candidates))), 7, exits[0] + 100),
    )
    for outcome, action, output in scenarios:
        (tmp_path / 'value.bin').write_bytes(DATA.encode(outcome, bind_exit=ports.__getitem__, bind_node=lambda _: 0))
        for local_origin in (0, 1):
            result = subprocess.check_output([str(native), str(tmp_path / 'value.bin'), '64', '100', '2', str(local_origin)], text=True)
            assert tuple(map(int, result.split())) == (action, output, 0, 2)


def test_nested_layout_references_and_integer_validation(tmp_path):
    definition = RouteData('nested', Record(enabled=BOOL, small=U8, medium=U16, number=U64,
        child=Record(target=NODE, backup=EXIT, delta=I32)), Record(), 1)
    connection = __import__('skyseed.simulator', fromlist=['OperationalConnectionId']).OperationalConnectionId(
        __import__('skyseed.simulator', fromlist=['OperationalConnectionKind']).OperationalConnectionKind.INTER_SATELLITE, A, B)
    exit = LogicalExit(connection, A)
    outcome = LocalRouteOutcome(RouteDemand(A, GroundStationRouteDemandTarget(G)), candidates=(RouteCandidate(exit),),
        data=PolicyRecord(enabled=True, small=255, medium=1234, number=2**50, child=PolicyRecord(target=G, backup=exit, delta=-3)))
    encoded = definition.encode(outcome, bind_exit=lambda _: 9, bind_node=lambda _: 123)
    assert len(encoded) == definition.value_size
    definition.generate_headers(tmp_path)
    source = tmp_path / 'nested.c'; source.write_text('#include "nested.h"\nstatic int handle(struct sky_context *c) {return sky_pass(c);}\nSKYSEED_PROGRAM(handle);\n')
    assert prepare_program(BpfProgram(source, data=definition))['declaration']['maps']['routes'][2] == len(encoded)
    with pytest.raises(ValueError, match='outside u8'):
        definition.encode(replace(outcome, data=PolicyRecord(dict(outcome.data.fields) | {'small': 256})), bind_exit=lambda _: 9, bind_node=lambda _: 123)
    with pytest.raises(ValueError, match='fields differ'):
        definition.encode(replace(outcome, data=PolicyRecord()), bind_exit=lambda _: 9, bind_node=lambda _: 123)


def test_empty_scopes_and_attachment_conflicts(tmp_path):
    paths, _ = produce(tmp_path / 'empty', selection=DataPlane())
    with closing(_open_runner_artifact(paths[0])) as artifact:
        assert artifact.dataplane['programs'] == []
        assert all(n['program'] is None for n in artifact.dataplane['nodes'])
        assert all(not p['route_put'] and not p['active_put'] for b in artifact.boundaries() for p in b.executable['node_patches'])
        assert all(not n['tables'] for n in artifact.runtime_capacity_demand['per_node'])
    selected = program(tmp_path)
    conflict = DataPlane((DataPlaneBinding(selected, AttachmentScope(nodes=(A,))),
                          DataPlaneBinding(selected, AttachmentScope(nodes=(A,)))))
    with pytest.raises(ValueError, match='conflicting'):
        produce(tmp_path / 'conflict', selection=conflict)


def test_structured_multidelivery_runtime_reader_reclamation(tmp_path, monkeypatch):
    paths, _ = produce(tmp_path / 'delivery', selection=program(tmp_path), ends=(0, 5, 15))
    from test_runner_runtime_optimization import _Kernel
    kernel = _Kernel(monkeypatch)
    artifacts = [_open_runner_artifact(p) for p in paths]
    first = artifacts[0]
    runtime = _TcBpfRuntimeRealization(executor=kernel, pin_root='/pins', continuation_enabled=True, batch_capacity_factor=4,
        bindings={n: SimpleNamespace(pid=i+10, process_start_time_ticks=1, netns_inode=i+20) for i,n in enumerate(first.physical_world_projection.node_ids)})
    try:
        runtime.initialize(first.physical_world_projection, zone_prefixes=first.zone_prefixes,
                           runtime_capacity_demand=first.runtime_capacity_demand, dataplane=first.dataplane)
        for index, artifact in enumerate(artifacts):
            plans = runtime.prepare_all(artifact.boundaries(), logical_ports=artifact.logical_ports, retirements=artifact.retirements,
                **({} if index == 0 else {'runtime_capacity_demand': artifact.runtime_capacity_demand}))
            for plan in plans:
                runtime.activate(plan)
        kernel.maps[command._GENERATION_READERS_FD][0] = 1
        assert runtime.reclaim()['reader_wait']
        assert any(version == 0 for _, version in command._VERSIONS)
        kernel.maps[command._GENERATION_READERS_FD][0] = 0
        assert 0 in runtime.reclaim()['reclaimed_generations']
        assert any(isinstance(e[-1], bytes) and len(e[-1]) == DATA.value_size for e in kernel.operations if e[0] == 'map-update')
    finally:
        runtime.cleanup()
        for artifact in artifacts: artifact.close()


def test_node_definitions_have_separate_tables_and_attachments(tmp_path, monkeypatch):
    definitions = (RouteData('left', Record(threshold=U32), Record(), 2, value_field='left'),
                   RouteData('right', Record(limit=U64), Record(), 2, value_field='right'))
    bindings = []
    for definition, node in zip(definitions, (A, B)):
        source = tmp_path / f'{definition.name}.c'
        source.write_text(f'#include "{definition.name}.h"\nstatic int h(struct sky_context *c) {{return sky_pass(c);}}\nSKYSEED_PROGRAM(h);\n')
        bindings.append(DataPlaneBinding(BpfProgram(source, data=definition), AttachmentScope(nodes=(node,))))
    class Scoped(ThresholdRoutes):
        def plan_route_outcomes(self, *args, **kwargs):
            result = super().plan_route_outcomes(*args, **kwargs)
            return RoutePlanningResult(tuple(replace(v, data=PolicyRecord(left=PolicyRecord(threshold=12), right=PolicyRecord(limit=2**40)),
                candidates=tuple(replace(c, data=PolicyRecord(left=PolicyRecord(), right=PolicyRecord())) for c in v.candidates)) for v in result.outcomes))
    paths, _ = produce(tmp_path / 'scopes', model=Scoped(), selection=DataPlane(tuple(bindings)))
    with closing(_open_runner_artifact(paths[0])) as artifact:
        draft = _MaterialCursor(artifact.physical_world_projection.node_ids, artifact.dataplane).apply(next(iter(artifact.boundaries())))
        for i, node in enumerate((A, B)):
            assert draft.generation_by_node[node].table_counts == {i: [2, 1]}
            assert len(next(iter(draft.generation_by_node[node].forwarding.values()))[1])-4 == definitions[i].value_size
        assert not draft.generation_by_node[G].forwarding
        from test_runner_runtime_optimization import _Kernel
        kernel = _Kernel(monkeypatch)
        runtime = _TcBpfRuntimeRealization(executor=kernel, pin_root='/pins',
            bindings={n: SimpleNamespace(pid=i+10, process_start_time_ticks=1, netns_inode=i+20) for i,n in enumerate(artifact.physical_world_projection.node_ids)})
        try:
            runtime.initialize(artifact.physical_world_projection, zone_prefixes=artifact.zone_prefixes,
                runtime_capacity_demand=artifact.runtime_capacity_demand, dataplane=artifact.dataplane)
            plans = runtime.prepare_all(artifact.boundaries(), logical_ports=artifact.logical_ports, retirements=artifact.retirements)
            runtime.activate(plans[0])
            calls = [e for e in kernel.operations if e[0] == 'attach']
            assert len(calls) == 4
            for i, node in enumerate((A, B)):
                assert {c[2][1][1] for c in calls if c[1] == node.value} == {f'tc_ingress@{i}'}
        finally:
            runtime.cleanup()


def test_detached_structured_material_needs_no_author_sources_or_compiler(tmp_path):
    author = tmp_path / 'author'; author.mkdir()
    source = author / 'entry.c'; shutil.copy(SOURCE, source)
    paths, _ = produce(tmp_path / 'sealed', selection=BpfProgram(source, data=DATA))
    shutil.rmtree(author)
    code = r'''
import builtins, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('templates', 'skyseed.backend.compiler', 'skyseed.backend.bpf.dataplane.program', 'skyseed.backend.bpf.dataplane.translation')):
        raise AssertionError('author/build dependency: '+name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from pathlib import Path
from skyseed.backend.execution_materials.codec import _open_runner_artifact
from skyseed.backend.bpf.dataplane.runtime import _TcBpfRuntimeRealization
from types import SimpleNamespace
class Executor:
    def execute(self, action, request):
        if action == 'initialize':
            return {'collision_audit': {'result':'passed','selected_node_address_range':request['node_address_range'],'audited_node_namespaces':len(request['nodes'])},'installed_node_addresses':[n['key'] for n in request['nodes']],'bpf_batch_update_supported':True}
        if action == 'prewrite_generation':
            return {'preloaded_active_links':sum(len(n['active_entries']) for n in request['generation_nodes']), 'preloaded_routes':sum(len(n['route_entries']) for n in request['generation_nodes'])}
        if action == 'cleanup': return {'pin_root_removed':True,'errors':[]}
        raise AssertionError(action)
a = _open_runner_artifact(Path(sys.argv[1]))
r = _TcBpfRuntimeRealization(executor=Executor(), pin_root='/pins', bindings={n:SimpleNamespace(pid=i+10,process_start_time_ticks=1,netns_inode=i+20) for i,n in enumerate(a.physical_world_projection.node_ids)})
try:
    r.initialize(a.physical_world_projection, dataplane=a.dataplane, zone_prefixes=a.zone_prefixes, runtime_capacity_demand=a.runtime_capacity_demand)
    plans = r.prepare_all(a.boundaries(),logical_ports=a.logical_ports,retirements=a.retirements)
    assert len(plans) == 4
finally:
    r.cleanup(); a.close()
print('detached-structured-pass')
'''
    result = subprocess.run([sys.executable, '-c', code, str(paths[0])], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'detached-structured-pass' in result.stdout


def test_partial_and_empty_results_replace_without_filling_demands(tmp_path):
    paths, plans = produce(tmp_path, model=ChangingRoutes(), selection=program(tmp_path))
    outcomes = plans[0].transitions[0].route_outcome_transitions
    assert len(outcomes) == 1 and outcomes[0].demand.source == A
    assert plans[-1].transitions[0].route_outcome_transitions[0].current is None
    class Empty(ThresholdRoutes):
        def plan_route_outcomes(self, *args, **kwargs):
            return RoutePlanningResult()
    _, empty = produce(tmp_path / 'empty', model=Empty(), selection=program(tmp_path))
    assert all(not t.route_outcome_transitions for p in empty for t in p.transitions)


def test_local_exit_references_checked_by_planner_and_compiler(tmp_path):
    from skyseed.simulator import OperationalConnectionId, OperationalConnectionKind
    # The local endpoint is known, but this declared access was not selected.
    from skyseed.shared import NodeId
    foreign = NodeId(65001, 'satellite', 'absent')
    exit = LogicalExit(OperationalConnectionId(OperationalConnectionKind.INTER_SATELLITE, A, foreign), A)
    class Invalid(ThresholdRoutes):
        def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
            return RoutePlanningResult((LocalRouteOutcome(next(d for d in demands if d.source == A), candidates=(RouteCandidate(exit),)),))
    with pytest.raises(ValueError, match='foreign or non-operational exit'):
        produce(tmp_path, model=Invalid(), selection=program(tmp_path))


def test_structured_batch_width_and_existing_fallback(monkeypatch):
    import ctypes
    from skyseed.backend.linux import bpf
    entries = [(struct.pack('<II', 2, i), bytes([i]) * DATA.value_size) for i in (1, 2)]
    seen = []
    def call(operation, attributes):
        seen.append((ctypes.string_at(attributes.keys, 16), ctypes.string_at(attributes.values, DATA.value_size * 2)))
    monkeypatch.setattr(bpf, '_bpf', call)
    assert bpf._update_map_entries(5, entries, batch_supported=True) == 2
    assert seen == [(b''.join(k for k, _ in entries), b''.join(v for _, v in entries))]
    writes = []
    monkeypatch.setattr(bpf, '_update_map', lambda *args: writes.append(args))
    assert bpf._update_map_entries(5, entries, batch_supported=False) == 2
    assert writes == [(5, k, v) for k, v in entries]


def test_default_rejects_silent_multipath_and_accepts_explicit_dispositions(tmp_path):
    with pytest.raises(ValueError, match='custom RouteData/program'):
        produce(tmp_path / 'unsupported')
    class Dispositions(ThresholdRoutes):
        def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
            demand = next(d for d in demands if d.source == A)
            kind = RouteDisposition.DROP if timestamp == START else RouteDisposition.PASS
            return RoutePlanningResult((LocalRouteOutcome(demand, kind),))
    paths, _ = produce(tmp_path / 'default', model=Dispositions(), ends=(5,))
    with closing(_open_runner_artifact(paths[0])) as artifact:
        cursor = _MaterialCursor(artifact.physical_world_projection.node_ids, artifact.dataplane)
        values = []
        for boundary in artifact.boundaries():
            node = cursor.apply(boundary).generation_by_node[A]
            values.append({struct.unpack_from('<I', entry[1], 4)[0] for entry in node.forwarding.values()})
        assert 0xfffffffe in values[0] and 0xfffffffd in values[1]


def test_scoped_attachment_reappears_with_new_live_interface(tmp_path, monkeypatch):
    from test_runner_runtime_optimization import _Kernel, _capacity_transition_sequence
    from skyseed.backend.bpf.dataplane.translation import _BpfTranslation
    from skyseed.backend.execution_materials.actions import _ResourceIntent
    from skyseed.backend.bpf.dataplane.materials import _RuntimeCapacityDemandAccumulator
    projection, boundaries = _capacity_transition_sequence()
    first = boundaries[0]
    when = boundaries[-1].nominal_time + timedelta(seconds=1)
    recreated = replace(first, nominal_time=when, operations=tuple(
        replace(operation, intent=replace(operation.intent, nominal_time=when)) for operation in first.operations))
    boundaries = (*boundaries, recreated)
    connections = tuple(dict.fromkeys(op.intent.connection_id for b in boundaries for op in b.operations
                                     if isinstance(op.intent, _ResourceIntent)))
    endpoint = LogicalExit(connections[0], connections[0].endpoint_a)
    selection = DataPlane((DataPlaneBinding(BpfProgram(ROOT / 'templates/dataplane/pass.bpf.c'),
                                            AttachmentScope(nodes=(endpoint.source,))),))
    translation = _BpfTranslation(projection, ('198.18.0.1/32',), dataplane_program=selection)
    ports = {c: (2*i+1, 2*i+2) for i, c in enumerate(connections)}
    prepared = tuple(translation.translate(b, ports) for b in boundaries)
    capacity = _RuntimeCapacityDemandAccumulator(projection, ())
    for boundary in prepared:
        capacity.add(boundary)
    kernel = _Kernel(monkeypatch)
    runtime = _TcBpfRuntimeRealization(executor=kernel, pin_root='/pins',
        bindings={n: SimpleNamespace(pid=i+10, process_start_time_ticks=1, netns_inode=i+20)
                  for i,n in enumerate(projection.node_ids)})
    try:
        runtime.initialize(projection, zone_prefixes=('198.18.0.1/32',), dataplane=translation.foundation(),
                           runtime_capacity_demand=capacity.snapshot())
        plans = runtime.prepare_all(prepared, logical_ports=ports, retirements=translation.retirements())
        before = runtime.activate(plans[0])['interface_identities']
        for plan in plans[1:]:
            after = runtime.activate(plan)['interface_identities']
        assert [v['logical_port_id'] for v in before] == [v['logical_port_id'] for v in after]
        assert [v['ifindex'] for v in before] != [v['ifindex'] for v in after]
        attached = [op for op in kernel.operations if op[0] == 'attach']
        assert len(attached) == 3 and all(op[1] == endpoint.source.value for op in attached)
        assert attached[0][2][0] != attached[1][2][0]
        assert attached[0][2][1] == attached[1][2][1]
    finally:
        runtime.cleanup()


@pytest.mark.parametrize('length,expected', [(64, (A, G)), (128, (A, B, G)), (200, (A, B, G))])
def test_threshold_example_composes_actual_c_choices_to_ground(tmp_path, native_handler, length, expected):
    paths, _ = produce(tmp_path / 'composed', model=ThresholdRoutes(), selection=program(tmp_path), ends=(0,))
    with closing(_open_runner_artifact(paths[0])) as artifact:
        draft = _MaterialCursor(artifact.physical_world_projection.node_ids, artifact.dataplane).apply(next(iter(artifact.boundaries())))
        # Connect the native handler's logical exit to its physical peer, without
        # reimplementing the C selection or the Model's candidate policy.
        peers = {(source, port): peer for connection, ports in artifact.logical_ports.items()
                 for source, peer, port in zip((connection.endpoint_a, connection.endpoint_b),
                    (connection.endpoint_b, connection.endpoint_a), ports)}
        node, visited = A, [A]
        for _ in artifact.physical_world_projection.node_ids:
            encoded = next(iter(draft.generation_by_node[node].forwarding.values()))[1][4:]
            value = tmp_path / 'hop.bin'; value.write_bytes(encoded)
            result = subprocess.check_output([str(native_handler), str(value), str(length), '0'], text=True)
            action, output, readers, counter = map(int, result.split())
            assert (readers, counter) == (0, 1)
            if action == 0:
                assert node == G and output == 0
                break
            assert action == 7
            node = peers[node, output]
            visited.append(node)
        else:
            pytest.fail(f'teaching packet did not reach Linux PASS: {visited}')
        assert tuple(visited) == expected


def test_structured_translation_reuses_program_values_and_unaffected_nodes(tmp_path, monkeypatch):
    from skyseed.backend.bpf.dataplane import program as preparation
    from skyseed.backend.bpf.dataplane.translation import _BpfTranslation
    compiled, values, nodes = [], [], []
    build, encode, encode_node = preparation._build_program, RouteData.encode, _BpfTranslation._encode_node
    def record_build(selection):
        compiled.append(selection)
        return build(selection)
    def record_value(self, outcome, **bindings):
        values.append(outcome.data['threshold'])
        return encode(self, outcome, **bindings)
    def record_node(self, node, ports):
        nodes.append(node.node_id)
        return encode_node(self, node, ports)
    monkeypatch.setattr(preparation, '_build_program', record_build)
    monkeypatch.setattr(RouteData, 'encode', record_value)
    monkeypatch.setattr(_BpfTranslation, '_encode_node', record_node)
    produce(tmp_path, selection=program(tmp_path), ends=(0, 5, 15))
    assert len(compiled) == 1
    assert values == [128, 200]  # No repeat encoding at the unchanged step or withdrawal.
    assert nodes.count(A) == 3 and nodes.count(B) == nodes.count(G) == 1
