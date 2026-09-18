"""Optional Tools consume sealed facts and cannot alter the generation outcome."""
from hashlib import sha256
import json
from pathlib import Path
import pytest
from examples.Sky00_tiny_network import tiny_network as tiny
from skyseed_v2.experiment import ExperimentController
import tools
from tools import experiment as reporting


@pytest.fixture(scope='module')
def generated(tmp_path_factory):
    emulator = tiny.build_emulator()
    simulator = tiny.create_simulator(emulator)
    output = ExperimentController(emulator, simulator, tmp_path_factory.mktemp('reporting') / 'output',
        entry_path=Path(tiny.__file__), input_files=(tiny.ZONE_LAYOUT_FILE,),
        internet_map_enabled=False, route_workers=1).generate()
    return simulator, output


def test_tools_read_produced_histories_without_calling_models(generated, monkeypatch):
    simulator, output = generated
    def forbidden(*a, **kw):
        raise AssertionError('Tool reran a Model')
    monkeypatch.setattr(type(simulator.get_physical_world()), 'frame_at', forbidden)
    monkeypatch.setattr(type(simulator.get_physical_link_model()), 'compute_observation', forbidden)
    monkeypatch.setattr(type(simulator.get_planner()), 'observe', forbidden)
    results = tools.write_experiment_tools(output, physical_world=simulator.get_physical_world())
    assert set(results) == {'physical-world', 'zone', 'planner', 'bpf-compiler', 'performance'}
    assert all(result['status'] in {'PASS', 'COMPLETE'} for result in results.values()), results
    assert (output / 'visualizations/index.html').is_file()
    for directory in results:
        assert (output / 'visualizations' / directory / 'index.html').is_file()
    assert tools.physical_world.write_physical_world_tool is reporting.write_physical_world_tool


def test_optional_tool_failure_is_reported_without_changing_sealed_materials(generated, monkeypatch):
    simulator, output = generated
    artifact = output / 'results/compiler/runner-artifact/runner-runtime.bin'
    before = sha256(artifact.read_bytes()).hexdigest()
    def fail(*a, **kw):
        raise RuntimeError('tool-only failure')
    monkeypatch.setattr(reporting, 'write_physical_world_tool', fail)
    results = tools.write_experiment_tools(output, physical_world=simulator.get_physical_world())
    assert results['physical-world']['status'] == 'FAILED'
    assert sha256(artifact.read_bytes()).hexdigest() == before
    status = json.loads((output / 'results/experiment/generation-status.json').read_text())
    assert status['status'] == 'MATERIALS_READY'
    assert json.loads((output/'visualizations/tool-timing.json').read_text())['status'] == 'DEGRADED'
    assert status['producer']['domain_history_sealed']


def test_runtime_tool_paths_and_traffic_warning_survive_status_serialization(generated, monkeypatch):
    simulator, output = generated
    runs = output / 'runs'
    invocation = runs / 'fixture/runner'
    invocation.mkdir(parents=True)
    (runs / 'latest.json').write_text(json.dumps({'invocation_id': 'fixture'}))
    (invocation / 'runner-invocation.json').write_text(json.dumps({'runner_snapshot': {}, 'runtime_outcome': {}}))
    monkeypatch.setattr(reporting, 'write_physical_world_tool', lambda *a, **kw: {'status': 'PASS'})
    monkeypatch.setattr(reporting, 'write_zone_tool', lambda *a, **kw: {'status': 'PASS'})
    monkeypatch.setattr(reporting, '_run_dynamic_tools', lambda *a, **kw: ({}, ()))
    monkeypatch.setattr(tools.runner, 'write_runner_tool', lambda *a, **kw: {'status': 'COMPLETED', 'directory': output/'visualizations/runner'})
    monkeypatch.setattr(tools.traffic, 'write_traffic_replay_tool', lambda *a, **kw: {'status': 'WARN', 'directory': output/'visualizations/traffic'})
    result = tools.write_experiment_tools(output, physical_world=simulator.get_physical_world())
    status = json.loads((output/'results/experiment/generation-status.json').read_text())
    assert status['status'] == 'MATERIALS_READY'
    status = json.loads((output/'visualizations/tool-timing.json').read_text())
    assert status['results']['traffic']['status'] == 'WARN'
    assert status['results']['runner']['directory'] == str(output/'visualizations/runner')
    assert result['traffic']['status'] == 'WARN'
    (runs / 'latest.json').unlink()


def test_repeated_tools_preserve_generation_facts_and_measure_each_invocation(generated, monkeypatch):
    simulator, output = generated
    status_path = output/'results/experiment/generation-status.json'
    before = status_path.read_bytes()
    monkeypatch.setattr(reporting, '_run_dynamic_tools', lambda *a, **k: ({}, ()))
    monkeypatch.setattr(reporting, 'write_physical_world_tool', lambda *a, **k: {'status': 'PASS'})
    monkeypatch.setattr(reporting, 'write_zone_tool', lambda *a, **k: {'status': 'PASS'})
    for _ in range(2):
        tools.write_experiment_tools(output, physical_world=simulator.get_physical_world())
        assert status_path.read_bytes() == before
    runs = [json.loads(p.read_text()) for p in (output/'results/tools/tool-runs').glob('*.json')]
    assert len({r['invocation_id'] for r in runs}) == len(runs) >= 2
    assert all(r['wall_seconds'] >= 0 for r in runs)
    assert all('total_elapsed_seconds' not in r for r in runs)


def test_traffic_cli_pins_one_completed_request(generated, monkeypatch):
    import sys
    from tools.traffic import __main__ as cli
    from skyseed_v2.experiment.output.history import _completed_range
    from tools._history import read_sealed_physical_frames
    _, output = generated
    completed = output / 'results/experiment/completed.json'
    before = completed.read_bytes()
    publication = json.loads(before)
    frames = output / 'results/physical-world/physical-frames.jsonl'
    def render(*args):
        # A new publication cannot change the boundary selected by this command.
        completed.write_text('{being-replaced}')
        assert _completed_range.get()[1] == publication
        assert len(read_sealed_physical_frames(frames)) == publication['history']['counts']['formal_frames']
        return {'artifact_metrics': {}}
    monkeypatch.setattr(cli, 'write_traffic_replay_tool', render)
    monkeypatch.setattr(sys, 'argv', ['traffic', '--telemetry', str(output/'unused'),
        '--physical-frames', str(frames), '--planning', str(output/'results/planner/planning.jsonl.gz'),
        '--bpf-compiler', str(output/'results/compiler/compiler.jsonl'), '--output', str(output/'visualizations/traffic')])
    try:
        assert cli.main() == 0
        assert _completed_range.get() is None
    finally:
        completed.write_bytes(before)


def test_missing_optional_runtime_handoff_keeps_partial_tool_results(generated, monkeypatch):
    simulator, output = generated
    before = (output / 'results/experiment/generation-status.json').read_bytes()
    latest = output / 'runs/latest.json'
    latest.write_text(json.dumps({'invocation_id': 'missing-optional-handoff'}))
    monkeypatch.setattr(reporting, '_run_dynamic_tools', lambda *a, **k: ({}, ()))
    monkeypatch.setattr(reporting, 'write_physical_world_tool', lambda *a, **k: {'status': 'PASS'})
    monkeypatch.setattr(reporting, 'write_zone_tool', lambda *a, **k: {'status': 'PASS'})
    results = tools.write_experiment_tools(output, physical_world=simulator.get_physical_world())
    assert results['physical-world']['status'] == 'PASS'
    assert results['presentation']['status'] == 'FAILED'
    invocation = json.loads((output / 'visualizations/tool-timing.json').read_text())
    assert invocation['status'] == 'DEGRADED'
    assert invocation['results'] == results
    assert invocation['wall_seconds'] >= results['physical-world']['duration_seconds']
    assert (output / 'results/experiment/generation-status.json').read_bytes() == before
