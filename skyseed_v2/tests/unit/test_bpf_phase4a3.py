"""Compiler/material/Runner ownership and changed Phase 4a-3 behavior."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from types import SimpleNamespace
import json
import subprocess
import sys

import pytest

from examples.Sky00_tiny_network.tiny_network import build_emulator, create_simulator
from tests._support import drain_simulator
from skyseed_v2.backend import compiler
from skyseed_v2.backend.execution_materials import codec, sealing
from skyseed_v2.backend.execution_materials.actions import _intent_phase
from skyseed_v2.backend.execution_materials.boundaries import _resolve_nominal_boundaries
from skyseed_v2.backend.runner import command_channel, event_history
from test_runner_runtime_optimization import _finite_runtime
from test_runner_batches import _produce, _runner_with_real_maintenance


def test_reading_and_collector_do_not_import_compiler_or_runtime_owner(tmp_path):
    code = '''
import sys, threading
from skyseed_v2.backend.execution_materials import codec, mechanics
assert not any(n.startswith('skyseed_v2.backend.compiler') for n in sys.modules)
assert not any(n.startswith('skyseed_v2.backend.bpf.runner') for n in sys.modules)
from skyseed_v2.backend.bpf.runner import traffic_telemetry_collector
assert 'skyseed_v2.backend.bpf.dataplane.runtime_command' not in sys.modules
assert 'skyseed_v2.backend.runner.lifecycle' not in sys.modules
assert len(threading.enumerate()) == 1
'''
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_compiler_validates_zone_coverage_once_and_retains_incremental_indexes(monkeypatch):
    simulation = create_simulator(build_emulator())
    calls = []
    original = compiler._validate_zone_assignments_for_projection
    def coverage(state, **kwargs):
        calls.append(state.timestamp)
        return original(state, **kwargs)
    monkeypatch.setattr(compiler, '_validate_zone_assignments_for_projection', coverage)
    _, results, _ = drain_simulator(simulation)
    assert len(calls) == 1
    assert sum(len(r.compiled_activation_boundaries) for r in results) > 1
    for result in results:
        indexes = result._incremental_indexes
        assert len(indexes) == 10
        pairs = indexes[6]
        for connection in result.output_planned_state.operational_connections:
            identifier = connection.connection_id
            assert pairs[identifier.endpoint_a, identifier.endpoint_b] == identifier
            assert pairs[identifier.endpoint_b, identifier.endpoint_a] == identifier
        assert indexes[7] == {a.service_key: a.satellite_id for a in result.output_planned_state.zone_service_assignments}
        for boundary in _resolve_nominal_boundaries((result,)):
            phases = [_intent_phase(op.intent) for op in boundary.operations]
            assert phases == sorted(phases)


def test_route_free_transition_does_not_touch_route_or_world_indexes():
    class Unavailable:
        def __getattribute__(self, name):
            raise AssertionError('irrelevant transition read ' + name)
    value = Unavailable()
    compiler._lower_route_outcomes(
        SimpleNamespace(route_outcome_transitions=()), value,
        state=value, next_connections=value, projection=value,
        node_ids=value, zone_ids=value, zone_prefixes=value,
        forwarding_intents=value, event_index=value, semantic_intents=value,
    )


def test_custom_dependency_survives_unless_shared_phase_order_guarantees_it():
    from skyseed_v2.simulator import SemanticEventKind, SemanticEventReference, SemanticEventPrecedence
    from skyseed_v2.backend.execution_materials.actions import _IntentReference, _OrderingConstraint
    _, results, _ = drain_simulator(create_simulator(build_emulator()))
    result = results[0]
    resources = result.resource_realizations[:2]
    assert len(resources) == 2 and resources[0].nominal_time == resources[1].nominal_time
    first, second = (
        SemanticEventReference(value.nominal_time, SemanticEventKind.CONNECTION_ESTABLISH,
                               connection_id=value.connection_id)
        for value in resources
    )
    parameter = result.parameter_realizations[0]
    ready = SemanticEventReference(parameter.nominal_time, SemanticEventKind.PARAMETER_READY,
                                   connection_id=parameter.connection_id)
    refs = {first: {_IntentReference('resource', 0)}, second: {_IntentReference('resource', 1)},
            ready: {_IntentReference('parameter', 0)}}
    def lower(before, after):
        plan = SimpleNamespace(transitions=(SimpleNamespace(
            semantic_event_precedences=(SemanticEventPrecedence(before, after),)),))
        return compiler._compile_constraints(plan, resource_intents=resources,
            qdisc_intents=(parameter,), forwarding_intents=(), zone_edge_intents=(),
            semantic_intents=refs)
    constraints, folding = lower(first, second)
    assert constraints == (_OrderingConstraint(_IntentReference('resource', 0),
        _IntentReference('resource', 1), 'plan-semantic-precedence-fallback'),)
    assert folding == (('explicit-fallback', 1),)
    assert lower(first, ready) == ((), (('runner-phase', 1),))
    with pytest.raises(ValueError, match='cannot be represented'):
        lower(ready, first)


def test_writer_completed_metadata_needs_no_output_read_and_is_detached(tmp_path, monkeypatch):
    _, results, _ = drain_simulator(create_simulator(build_emulator()))
    writer = sealing._RunnerArtifactWriter(tmp_path / 'material', final=False)
    with pytest.raises(RuntimeError, match='not been sealed'):
        writer.sealed_metadata()
    for result in results:
        writer.append(result)
    original = Path.open
    def no_output_read(path, mode='r', *args, **kwargs):
        if path.name in {'manifest.json', 'runner-runtime.bin'} and 'r' in mode:
            raise AssertionError('writer reopened completed output')
        return original(path, mode, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', no_output_read)
        path = writer.finalize()
        facts = writer.sealed_metadata()
        assert facts['manifest']['binary']['boundary_count'] > 0
        facts['manifest']['lineage'].clear()
        assert writer.sealed_metadata()['manifest']['lineage']
    with closing(codec._open_runner_artifact(path)) as reader:
        sealed = writer.sealed_metadata()
        assert sealed['manifest_sha256'] == reader.manifest_sha256
        assert sealed['manifest']['binary']['boundary_count'] == reader.boundary_count
        assert sealed['manifest']['sequence_sha256'] == reader.sequence_sha256
        assert len(tuple(reader.boundaries())) == reader.boundary_count
    writer.close()


def test_loaded_admission_metadata_is_detached_and_needs_no_manifest_reread(tmp_path, monkeypatch):
    _, paths = _produce(tmp_path, (0, 10, 30))
    with closing(codec._open_runner_artifact(paths[0])) as reader:
        def forbidden(*args, **kwargs):
            raise AssertionError('admission reread a loaded manifest')
        monkeypatch.setattr(Path, 'read_bytes', forbidden)
        first = reader.delivery_contract()
        assert first['manifest_sha256'] == reader.manifest_sha256
        assert first['timeline']['boundary_count'] == reader.boundary_count
        first['delivery']['index'] = 999
        assert reader.delivery_contract()['delivery']['index'] == 0


@pytest.mark.parametrize('owner', ('BpfRunner', 'Runner'))
@pytest.mark.parametrize('version', (1, 2))
def test_tools_read_existing_event_owner_labels_without_python_aliases(tmp_path, owner, version):
    from tools.observation.runner.reader import read_runner_event_history
    header = {'record_type': 'history_header', 'format_version': version, 'owner': owner}
    if version == 2:
        header['capture'] = 'bounded-nonblocking'
    event = {'record_type': 'runner_event', 'event': 'cleanup_end'}
    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps(header) + '\n' + json.dumps(event) + '\n')
    assert read_runner_event_history(path) == ({'event': 'cleanup_end'},)


def test_slow_normal_drain_and_overlapping_close_wait_for_the_worker(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    original = event_history._NonBlockingEventHistory._write_loop
    def writer(history):
        entered.set()
        assert release.wait(10), 'test failed to release writer'
        original(history)
    monkeypatch.setattr(event_history._NonBlockingEventHistory, '_write_loop', writer)
    history = event_history._NonBlockingEventHistory(tmp_path / 'events.jsonl')
    assert entered.wait(2)
    history.enqueue({'event': 'slow-normal-write'})
    completed = [Event(), Event()]
    outcomes = []
    def close(index):
        outcomes.append(history.close())
        completed[index].set()
    closers = [Thread(target=close, args=(i,)) for i in range(2)]
    for closer in closers:
        closer.start()
    try:
        # This is a test-only failure guard beyond the retired two-second limit.
        assert not completed[0].wait(2.1)
        assert not completed[1].is_set()
    finally:
        release.set()
        for closer in closers:
            closer.join(5)
    assert all(done.is_set() for done in completed)
    assert all(o['complete'] and o['failure'] is None and o['written_count'] == 1 for o in outcomes)
    assert history.close()['complete']


def test_empty_and_parameter_boundaries_reuse_generation_without_node_work(monkeypatch):
    from skyseed_v2.backend.bpf.dataplane.runtime import _TcBpfRuntimeRealization
    drafts = []
    from skyseed_v2.backend.bpf.dataplane.translation import _BpfTranslation
    original = _BpfTranslation._derive_boundary_plan
    def derive(self, *args, **kwargs):
        draft = original(self, *args, **kwargs)
        drafts.append(draft)
        return draft
    monkeypatch.setattr(_BpfTranslation, '_derive_boundary_plan', derive)
    runtime, _, _ = _finite_runtime(monkeypatch)
    assert drafts[2].operation_nodes == ()
    assert drafts[1].generation_nodes is drafts[0].generation_nodes
    assert drafts[1].generation_by_node is drafts[0].generation_by_node
    assert drafts[1].routes is drafts[0].routes
    assert len(drafts[1].operation_nodes) == 1
    assert drafts[2].generation_nodes is drafts[0].generation_nodes
    runtime.cleanup()


def test_failed_cleanup_retains_projected_ownership_then_success_releases_it(monkeypatch):
    runtime, plans, kernel = _finite_runtime(monkeypatch)
    runtime.activate(plans[0])
    build, indexes = runtime._previous_build, runtime._material_cursor
    original = kernel.execute
    def fail_cleanup(action, request):
        if action == 'cleanup':
            return {'pin_root_removed': False, 'errors': ['lost acknowledgment']}
        return original(action, request)
    monkeypatch.setattr(kernel, 'execute', fail_cleanup)
    with pytest.raises(RuntimeError, match='ownership retained'):
        runtime.cleanup()
    assert runtime._previous_build is build and runtime._material_cursor is indexes
    assert runtime._node_versions and runtime._logical_ports
    monkeypatch.setattr(kernel, 'execute', original)
    runtime.cleanup()
    assert runtime._previous_build is None and runtime._active is None
    for name in ('_node_versions', '_generation_reservations', '_logical_ports',
                 '_connection_names', '_telemetry_interfaces'):
        assert not getattr(runtime, name)
    assert runtime._material_cursor is None


def test_runner_cleanup_releases_current_and_ready_plans_preserving_frontier(tmp_path, monkeypatch):
    _, paths = _produce(tmp_path, (0, 10, 30))
    runner, _ = _runner_with_real_maintenance(monkeypatch, paths)
    runner.prepare()
    runner.append_batch(paths[1])
    runner._load_batch(runner._pending_batches[0], monotonic(), monotonic())
    assert runner.status()['next_batch'] == 'READY'
    frontier = runner.status()['active_frontier']
    try:
        raise ValueError('recorded maintenance failure')
    except ValueError as error:
        runner._maintenance_error = error
    runner.cleanup()
    assert runner.status()['active_frontier'] == frontier
    assert runner.status()['lifecycle_state'] == 'cleaned'
    assert not runner._prepared_plans and runner._ready_batch is None
    assert runner._active is None and runner._artifact is None
    assert runner._maintenance_thread is None
    assert runner._maintenance_error.__traceback__ is None
    assert runner.status()['maintenance_error'] == 'recorded maintenance failure'


def test_privileged_and_collector_script_paths_are_executable_without_kernel_work(tmp_path):
    channel = subprocess.run([sys.executable, '-B', str(command_channel._PRIVILEGED_COMMAND_PATH), 'serve-bpf'],
        input=json.dumps({'action': 'invalid-offline-test-action', 'request': {}}) + '\n',
        cwd=tmp_path, text=True, capture_output=True)
    assert channel.returncode == 0, channel.stderr
    assert json.loads(channel.stdout)['error'] == 'unknown private Runner command action'
    collector = subprocess.run([sys.executable, '-B', str(command_channel._TRAFFIC_TELEMETRY_COLLECTOR_PATH)],
        input='', cwd=tmp_path, text=True, capture_output=True)
    assert collector.returncode == 1, collector.stderr
    assert json.loads(collector.stdout)['error'] == 'traffic telemetry collector request is absent'
