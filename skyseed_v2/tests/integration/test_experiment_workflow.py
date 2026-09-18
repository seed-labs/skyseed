"""Offline workflow contracts across the real SEED and backend handoffs."""
from contextlib import closing
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from subprocess import run, CompletedProcess
import sys

import pytest
from seedemu.compiler import Docker
from examples.Sky00_tiny_network import tiny_network as tiny
from skyseed_v2.backend.bpf.execution_materials.codec import _open_runner_artifact
from skyseed_v2.experiment import ExperimentController
from skyseed_v2.experiment import controller as workflow
from skyseed_v2.experiment.output.generation import _guard_existing_output
from skyseed_v2.experiment.output.provenance import _capture_inputs
from skyseed_v2.experiment.execution import _read_and_verify_execution_definition, _write_generated_run_script
from tools._history import read_sealed_physical_frames


def configured(output, **options):
    emulator = tiny.build_emulator()
    simulator = tiny.create_simulator(emulator)
    return simulator, ExperimentController(emulator, simulator, output, entry_path=Path(tiny.__file__),
        input_files=(tiny.ZONE_LAYOUT_FILE,), docker_compiler=Docker(internetMapEnabled=False), **options)


def test_construction_is_inert_and_requires_explicit_generation(tmp_path, monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('construction performed work')
    monkeypatch.setattr(workflow, 'Producer', forbidden)
    simulator, instance = configured(tmp_path / 'new')
    assert not (tmp_path / 'new').exists()
    assert simulator.get_planner()._physical_observation_frontier is None
    simulator.get_planner().shutdown()


@pytest.mark.parametrize('through,plans,compiled', [('physical-world', 0, 0), ('planner', 1, 0), ('compiler', 1, 1)])
def test_cutoffs_use_one_producer_and_seal_every_timestamp(tmp_path, through, plans, compiled, monkeypatch):
    count = 0
    original = workflow.Producer
    def create(*args, **kwargs):
        nonlocal count
        count += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(workflow, 'Producer', create)
    simulator, instance = configured(tmp_path / through, through=through, route_workers=1)
    output = instance.generate()
    assert count == 1
    state = json.loads((output / 'results/experiment/generation-status.json').read_text())
    assert state['status'] == 'MATERIALS_READY'
    assert state['producer']['plan_count'] == plans
    assert state['producer']['realization_count'] == compiled
    assert state['producer']['formal_frame_count'] == 4
    assert state['producer']['domain_history_sealed']
    assert len(read_sealed_physical_frames(output / 'results/physical-world/physical-frames.jsonl')) == 4
    assert (output / 'results/compiler/execution.json').exists() is bool(compiled)
    with pytest.raises(RuntimeError, match='already been invoked'):
        instance.generate()
    if compiled:
        definition = _read_and_verify_execution_definition(output)
        assert definition['traffic_enabled'] is True
        with closing(_open_runner_artifact(output / 'results/compiler/runner-artifact/manifest.json')) as artifact:
            assert [b.nominal_time for b in artifact.boundaries()] == list(simulator.get_timeline().iter_timestamps())
        for script in ('deploy.sh', 'run.sh', 'cleanup.sh'):
            assert run(['bash', '-n', str(output / script)], capture_output=True).returncode == 0
        assert len((output / 'deployment/skyseed-build-plan.txt').read_text().split('prerequisite ')) == 3


def test_two_deliveries_preserve_one_lineage_and_packaged_paths(tmp_path):
    simulator, instance = configured(tmp_path / 'continued', delivery_ends=(tiny.DEFAULT_START_TIME + timedelta(seconds=10), tiny.DEFAULT_END_TIME))
    output = instance.generate()
    execution = _read_and_verify_execution_definition(output)
    paths = [output / execution['runner_artifact']['manifest'], output / execution['runner_batches'][0]['manifest']]
    timestamps = []
    for index, path in enumerate(paths):
        seal = json.loads(path.read_text())
        assert seal['terminal']
        assert seal['delivery']['final'] is (index == 1)
        with closing(_open_runner_artifact(path)) as artifact:
            timestamps.extend(boundary.nominal_time for boundary in artifact.boundaries())
    assert timestamps == list(simulator.get_timeline().iter_timestamps())
    assert len(timestamps) == len(set(timestamps)) == 4


def test_semantic_failure_closes_producer_and_reports_stage(tmp_path, monkeypatch):
    simulator, instance = configured(tmp_path / 'failed')
    closed = []
    original = workflow.Producer.close
    def close(self):
        closed.append(self)
        return original(self)
    def fail(*a, **kw):
        raise RuntimeError('physical failure')
    monkeypatch.setattr(workflow.Producer, 'close', close)
    monkeypatch.setattr(type(simulator.get_physical_link_model()), 'compute_observation', fail)
    with pytest.raises(RuntimeError, match='physical failure') as caught:
        instance.generate()
    assert closed
    assert any('compiler-production' in note for note in caught.value.__notes__)
    status = json.loads((tmp_path / 'failed/results/experiment/generation-status.json').read_text())
    assert status['status'] == 'FAILED'
    assert status['stage'] == 'compiler-production'
    assert not (tmp_path / 'failed/results/compiler/materials.jsonl').exists()


def test_existing_output_is_protected_without_docker_when_not_replacing(tmp_path, monkeypatch):
    marker = tmp_path / 'retained'
    marker.write_text('keep')
    _, instance = configured(tmp_path)
    with pytest.raises(FileExistsError):
        instance.generate()
    assert marker.read_text() == 'keep'
    assert not (tmp_path / 'results/experiment/generation-status.json').exists()


def test_replacement_guard_preserves_live_or_unverifiable_compose(tmp_path, monkeypatch):
    runtime = tmp_path / 'deployment'
    runtime.mkdir()
    (runtime / 'docker-compose.yml').write_text('services: {}')
    with pytest.raises(RuntimeError, match='identity'):
        _guard_existing_output(tmp_path)
    (runtime / 'compose-project-name').write_text('owned-project')
    from skyseed_v2.experiment.output import generation as support
    monkeypatch.setattr(support.subprocess, 'run', lambda *a, **kw: CompletedProcess([], 0, 'container-id\n', ''))
    with pytest.raises(RuntimeError, match='still has containers'):
        _guard_existing_output(tmp_path)
    monkeypatch.setattr(support.subprocess, 'run', lambda *a, **kw: CompletedProcess([], 1, '', 'daemon unavailable'))
    with pytest.raises(RuntimeError, match='cannot verify'):
        _guard_existing_output(tmp_path)


def test_input_capture_follows_relative_shared_files_once(tmp_path):
    data = tmp_path / 'data'
    example = tmp_path / 'example'
    data.mkdir(); example.mkdir()
    shells = data / 'shells.yaml'
    shells.write_text('shells: []')
    scenario = example / 'scenario.yaml'
    scenario.write_text('skyseed_autonomous_systems:\n  - shells_file: ../data/shells.yaml\n')
    captured = _capture_inputs((scenario, shells))
    assert captured == {scenario: scenario.read_bytes(), shells: shells.read_bytes()}


def test_generated_resource_overrides_are_distinct_and_environment_can_override(tmp_path):
    _, instance = configured(tmp_path / 'resources', deploy_parallel=3, runtime_workers=2, route_workers=1)
    output = instance.generate()
    assert 'SKYSEED_DEPLOY_PARALLEL:-3' in (output / 'deploy.sh').read_text()
    assert 'SKYSEED_BPF_RUNTIME_WORKERS:-2' in (output / 'run.sh').read_text()
    assert 'SKYSEED_ROUTE_WORKERS' not in (output / 'run.sh').read_text()
    # Execute only the generated assignment, never deployment/runtime commands.
    for script, name, default in (
        ('deploy.sh', 'SKYSEED_DEPLOY_PARALLEL', '3'),
        ('run.sh', 'SKYSEED_BPF_RUNTIME_WORKERS', '2'),
    ):
        assignment = next(line for line in (output / script).read_text().splitlines()
                          if line.startswith(f'export {name}='))
        for supplied, expected in ((None, default), ('', default), ('7', '7')):
            environment = dict(os.environ)
            environment.pop(name, None)
            if supplied is not None:
                environment[name] = supplied
            result = run(
                ['bash', '-c', assignment + '\nprintf "%s" "${' + name + '}"'],
                env=environment, capture_output=True, text=True, check=True,
            )
            assert result.stdout == expected
    records = [json.loads(line) for line in (output / 'results/experiment/generation-performance.jsonl').read_text().splitlines()]
    resources = [record['resources'] for record in records[1:] if record['resources'].get('last_route_compute_workers')]
    assert resources and all(record['last_route_compute_workers'] == 1 for record in resources)
    status = json.loads((output / 'results/experiment/generation-status.json').read_text())
    assert status['resources']['runtime_preparation']['requested_workers'] == 2
    definition = _read_and_verify_execution_definition(output)
    # Changed effective parameters are generated-input changes, never relabelled as current.
    (output / 'inputs/effective-simulation.json').write_text('{}')
    assert _read_and_verify_execution_definition(output) == definition


def test_route_worker_override_restores_environment_after_failure(monkeypatch):
    import os
    monkeypatch.setenv('SKYSEED_ROUTE_WORKERS', '3')
    with pytest.raises(RuntimeError, match='compute failed'):
        with workflow._route_worker_override(1):
            assert os.environ['SKYSEED_ROUTE_WORKERS'] == '1'
            raise RuntimeError('compute failed')
    assert os.environ['SKYSEED_ROUTE_WORKERS'] == '3'
    with pytest.raises(ValueError, match='1 to 8'):
        workflow.validate_route_worker_limit(9)
    assert os.environ['SKYSEED_ROUTE_WORKERS'] == '3'


def test_compiler_failure_preserves_primary_error_and_closes_packaging(tmp_path, monkeypatch):
    from skyseed_v2.backend.bpf.execution_materials.sealing import _RunnerArtifactWriter
    _, instance = configured(tmp_path / 'backend-failed')
    primary = RuntimeError('backend writer failed')
    def fail(*args, **kwargs):
        raise primary
    closed = []
    original = _RunnerArtifactWriter.close
    def close(self):
        closed.append(self)
        original(self)
    monkeypatch.setattr(_RunnerArtifactWriter, 'append', fail)
    monkeypatch.setattr(_RunnerArtifactWriter, 'close', close)
    with pytest.raises(RuntimeError) as caught:
        instance.generate()
    assert caught.value is primary
    assert closed
    status = json.loads((tmp_path / 'backend-failed/results/experiment/generation-status.json').read_text())
    assert status['status'] == 'FAILED'
    assert status['stage'] == 'compiler-production'


@pytest.mark.parametrize('options', [
    {'deploy_parallel': 0}, {'deploy_parallel': 65}, {'deploy_parallel': True},
    {'runtime_workers': 0}, {'runtime_workers': 65}, {'runtime_workers': 1.5},
    {'route_workers': 0}, {'route_workers': 9}, {'route_workers': True},
    {'delivery_ends': ()},
    {'delivery_ends': (tiny.DEFAULT_START_TIME,)},
    {'delivery_ends': (tiny.DEFAULT_START_TIME + timedelta(seconds=9), tiny.DEFAULT_END_TIME)},
    {'delivery_ends': (tiny.DEFAULT_END_TIME, tiny.DEFAULT_END_TIME)},
    {'delivery_ends': (tiny.DEFAULT_START_TIME - timedelta(seconds=10), tiny.DEFAULT_END_TIME)},
    {'traffic_options': {'maximum_concurrent_probes': 0}},
    {'traffic_options': {'persistent_interval_seconds': 0}},
    {'delivery_ends': (tiny.DEFAULT_START_TIME + timedelta(seconds=10), tiny.DEFAULT_END_TIME), 'batch_lead_fraction': 0},
    {'delivery_ends': (tiny.DEFAULT_START_TIME + timedelta(seconds=10), tiny.DEFAULT_END_TIME), 'batch_capacity_factor': 0},
])
@pytest.mark.parametrize('existing', [False, True])
def test_invalid_configuration_precedes_all_generation_work(tmp_path, monkeypatch, options, existing):
    output = tmp_path / 'output'
    before = {}
    if existing:
        (output / 'results/experiment').mkdir(parents=True)
        (output / 'results/experiment/generation-status.json').write_text('{"status":"retained"}')
        (output / 'nested').mkdir()
        (output / 'nested/source.txt').write_text('keep these bytes')
        before = {p.relative_to(output): p.read_bytes() for p in output.rglob('*') if p.is_file()}
    simulator, instance = configured(output, replace=existing, **options)
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid configuration reached expensive or destructive work')
    for name in ('_capture_inputs', '_prepare_generated_output', '_guard_existing_output', 'Producer'):
        monkeypatch.setattr(workflow, name, forbidden)
    monkeypatch.setattr(type(instance._emulator), 'compile', forbidden)
    with pytest.raises(ValueError) as caught:
        instance.generate()
    assert any("stage 'configuration'" in note for note in caught.value.__notes__)
    assert simulator.get_planner()._physical_observation_frontier is None
    assert output.exists() is existing
    after = {p.relative_to(output): p.read_bytes() for p in output.rglob('*') if p.is_file()}
    assert after == before
    simulator.get_planner().shutdown()


@pytest.mark.parametrize('through', ['physical-world', 'planner'])
def test_runtime_options_remain_inapplicable_to_earlier_cutoffs(tmp_path, through):
    _, instance = configured(
        tmp_path / through, through=through, deploy_parallel=0, runtime_workers=0,
        traffic_options={'maximum_concurrent_probes': 0}, batch_lead_fraction=0,
    )
    output = instance.generate()
    assert json.loads((output / 'results/experiment/generation-status.json').read_text())['status'] == 'MATERIALS_READY'
    assert not (output / 'deployment').exists()


def test_route_environment_is_validated_before_replacement_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv('SKYSEED_ROUTE_WORKERS', '0')
    marker = tmp_path / 'retained'
    marker.write_text('same')
    simulator, instance = configured(tmp_path, replace=True, through='physical-world')
    with pytest.raises(ValueError, match='1 to 8'):
        instance.generate()
    import os
    assert os.environ['SKYSEED_ROUTE_WORKERS'] == '0'
    assert marker.read_text() == 'same'
    assert simulator.get_planner()._physical_observation_frontier is None
    simulator.get_planner().shutdown()


def test_future_execution_environment_is_not_evaluated_or_frozen_during_generation(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv('SKYSEED_DEPLOY_PARALLEL', 'not-set-until-execution')
    monkeypatch.setenv('SKYSEED_BPF_RUNTIME_WORKERS', 'not-set-until-execution')
    monkeypatch.setenv('SKYSEED_ROUTE_WORKERS', '2')
    _, instance = configured(tmp_path / 'future-environment', route_workers=1,
                             batch_lead_fraction=0, batch_capacity_factor=0)
    # Batch options only enter generated execution for a continued delivery.
    output = instance.generate()
    assert 'export SKYSEED_DEPLOY_PARALLEL=' not in (output / 'deploy.sh').read_text()
    assert 'export SKYSEED_BPF_RUNTIME_WORKERS=' not in (output / 'run.sh').read_text()
    assert os.environ['SKYSEED_ROUTE_WORKERS'] == '2'
    definition = _read_and_verify_execution_definition(output)
    assert 'batch_lead_fraction' not in definition['runner']
    assert 'batch_capacity_factor' not in definition['runner']
