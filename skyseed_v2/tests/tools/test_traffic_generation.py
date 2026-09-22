"""One sealed workload feeds offline preview and execution on either backend."""
from datetime import timedelta
import json
from pathlib import Path
import pytest
from examples.Sky00_tiny_network import tiny_network
from skyseed_v2.experiment import ExperimentController
from tools.generation.traffic.preview import write_traffic_preview
from tools.generation.traffic.probe_plan import read_traffic_probe_plan
from tools.observation.traffic.sender_records import write_sender_observation


@pytest.mark.parametrize('backend', ['bpf', 'linux'])
def test_preview_pins_completed_delivery_prefix_without_models_or_deployment(tmp_path, monkeypatch, backend):
    emu = tiny_network.build_emulator()
    sim = tiny_network.create_simulator(emu)
    output = tmp_path / backend
    ExperimentController(emu, sim, output, entry_path=Path(__file__), forwarding_backend=backend,
        traffic_enabled=True, traffic_telemetry_enabled=False, internet_map_enabled=False,
        delivery_ends=(tiny_network.DEFAULT_START_TIME + timedelta(seconds=10), tiny_network.DEFAULT_END_TIME)).generate()
    definition = json.loads((output / 'results/compiler/execution.json').read_text())
    assert definition['traffic_enabled'] and not definition['traffic_telemetry_enabled']
    publication = json.loads((output / 'results/experiment/completed.json').read_text())
    # Rendering completed data must not launch Docker, ping, Models or a new simulator.
    monkeypatch.setattr('subprocess.Popen', lambda *a, **kw: pytest.fail('preview launched a process'))
    write_traffic_preview(output, publication={**publication, 'published_batches': 1})
    old = json.loads((output / 'visualizations/traffic-generation/plan.json').read_text())
    first = read_traffic_probe_plan(output / 'results/traffic/probe-plan.json')
    assert first.episodes  # Complete planned paths remain usable on either backend.
    if backend == "linux":
        assert first.artifact()["planning"]["estimated_telemetry_events"] == 0
    assert old['episodes'] == first.artifact()['episodes']
    write_traffic_preview(output, publication=publication)
    expanded = json.loads((output / 'visualizations/traffic-generation/plan.json').read_text())
    second = read_traffic_probe_plan(output / 'results/traffic/probe-plan-001.json')
    assert expanded['episodes'] == first.artifact()['episodes'] + second.artifact()['episodes']
    assert expanded['planning_horizon']['coverage_end_time'] == second.coverage_end.isoformat()
    assert (output / 'visualizations/traffic-generation/index.html').read_text().count('<tr>') == len(expanded['episodes']) + 1


def test_missing_packet_telemetry_retains_actual_sender_statistics(tmp_path):
    source = tmp_path / 'probe-execution.jsonl'
    records = [{'record_type': 'execution_header'},
               {'record_type': 'generator_stopped', 'sent': 4, 'received': 3},
               {'record_type': 'executor_end', 'complete': True, 'cleanup_complete': True}]
    source.write_text(''.join(json.dumps(r)+'\n' for r in records))
    result = write_sender_observation(source, tmp_path / 'view')
    data = json.loads((tmp_path / 'view/traffic-replay.json').read_text())
    assert result['sent'] == 4 and result['received'] == 3
    assert result['loss'] == 1 and result['loss_percent'] == 25.0
    assert result['directory'] == tmp_path / 'view'
    assert data['packet_paths'] is None and data['telemetry_available'] is False


def test_missing_sender_records_do_not_report_zero_traffic(tmp_path):
    result = write_sender_observation(tmp_path / 'absent.jsonl', tmp_path / 'view')
    assert result['sent'] is None and result['received'] is None
    assert result['reply_records'] is None and not result['executor_complete']
