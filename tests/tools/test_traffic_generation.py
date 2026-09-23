"""Preview is static configuration and does not inspect Plans or route outcomes."""
from examples.Sky00_tiny_network import tiny_network as tiny
from tools.generation.traffic.workload import select_workload,write_workload
from tools.generation.traffic.preview import write_traffic_preview


def test_preview_works_before_any_network_calculation(tmp_path):
    simulator=tiny.create_simulator(tiny.build_emulator())
    workload=select_workload(simulator.get_physical_world())
    simulator.shutdown()
    write_workload(workload,tmp_path/'results/traffic/workload.json')
    assert write_traffic_preview(tmp_path)['status']=='PASS'
    page=(tmp_path/'visualizations/traffic-generation/index.html').read_text()
    assert 'evaluates no routes' in page and 'Ground station' in page
    assert not (tmp_path/'results/compiler').exists()


def test_persistent_sender_streams_details_with_bounded_statistics(tmp_path):
    from dataclasses import fields
    from types import SimpleNamespace
    import json
    import pytest
    from tools.generation.traffic.probe_execution import TrafficExecutor, _RunningGenerator
    from tools.generation.traffic.workload import Communication, TrafficWorkload
    communication = Communication('flow', 'ground', 'zone', 'ground-to-zone')
    executor = TrafficExecutor(workload=TrafficWorkload((communication,)), evidence_path=tmp_path / 'probe.jsonl')
    generator = _RunningGenerator(communication, SimpleNamespace(node_id='ground'), 'identity', '/pid',
        '192.0.2.1', '192.0.2.2', 7, 1, SimpleNamespace(returncode=0), 1)
    record = executor._record
    seen = [0]
    def count(record_type, **facts):
        seen[0] += 1
    executor._record = count
    for index in range(70_000):
        executor._record_ping_line(generator, 'stdout', f'64 bytes from peer: icmp_seq={index % 65536} time=1.5 ms')
    assert seen == [70_000]
    assert generator.observed_replies == generator.rtt_count == 70_000
    assert generator.rtt_mean_ms == 1.5
    assert not any(isinstance(getattr(generator, f.name), (list, set, dict)) for f in fields(generator))
    executor._record = record
    executor._record_ping_line(generator, 'stdout', '64 bytes from peer: icmp_seq=1 time=2.5 ms (DUP!)')
    executor._record_ping_line(generator, 'stdout', 'no answer yet for icmp_seq=2')
    executor._record_generator_stopped(generator, 'incomplete-summary')
    records = [json.loads(line) for line in executor._path.read_text().splitlines()]
    assert records[1]['duplicate'] and records[1]['icmp_sequence'] == 1
    terminal = records[-1]
    assert terminal['sent'] is terminal['received'] is terminal['loss'] is None
    assert terminal['observed_duplicates'] == terminal['observed_timeouts'] == 1
    assert terminal['rtt_summary']['mean_ms'] == pytest.approx((70_000 * 1.5 + 2.5) / 70_001)
    assert not {'rtt_ms', 'reply_sequences', 'timeout_sequences', 'observed_sequences'} & terminal.keys()
    executor._record_ping_line(generator, 'stdout', '70002 packets transmitted, 70000 received, +1 duplicates, 0.002% packet loss')
    executor._record_generator_stopped(generator, 'complete')
    terminal = json.loads(executor._path.read_text().splitlines()[-1])
    assert (terminal['sent'], terminal['received'], terminal['loss']) == (70002, 70000, 2)
