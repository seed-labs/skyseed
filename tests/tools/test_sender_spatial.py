"""Native globe uses completed runtime state and actual records, never inferred hops."""
import json
from pathlib import Path
import pytest
from tests.fixtures.native_zone_business import generate
from tools.observation.traffic.inspection import write_traffic_replay_tool


@pytest.fixture
def output(tmp_path):
    return generate(tmp_path/'native', 'bird', duration=125, tools_enabled=False)


def observe(output, tmp_path, *, runtime=True):
    workload=json.loads((output/'results/traffic/workload.json').read_text())
    communication=next(c for c in workload['communications'] if c['direction']=='zone-to-ground')
    episode={'episode_id':communication['communication_id'], 'probe_id':communication['communication_id']}
    execution=tmp_path/'sender.jsonl'; events=tmp_path/'runner.jsonl'
    records=[{'record_type':'execution_header'},
        {'record_type':'generator_launched','episode_id':episode['episode_id'],'probe_id':episode['probe_id'],
         'segment_index':1,'host_monotonic_ns':101_000_000_000,'logical_direction':'zone-to-ground-station',
         'source_ipv4':'198.18.0.1','destination_ipv4':'100.64.0.1','physical_source_node_id':'65040/satellite/c'},
        {'record_type':'ping_observation','episode_id':episode['episode_id'],'segment_index':1,
         'host_monotonic_ns':102_000_000_000,'observation':'reply'},
        {'record_type':'generator_stopped','episode_id':episode['episode_id'],'segment_index':1,
         'host_monotonic_ns':103_000_000_000,'sent':100,'received':70},
        {'record_type':'zone_source_migration','episode_id':episode['episode_id'],'host_monotonic_ns':231_000_000_000},
        {'record_type':'generator_launched','episode_id':episode['episode_id'],'probe_id':episode['probe_id'],
         'segment_index':2,'host_monotonic_ns':232_000_000_000,'logical_direction':'zone-to-ground-station',
         'source_ipv4':'198.18.0.1','destination_ipv4':'100.64.0.1','physical_source_node_id':'65040/satellite/a'}]
    execution.write_text(''.join(json.dumps(r)+'\n' for r in records))
    first='2026-01-01T00:00:00+00:00'
    rows=[{'event':'activation_end','monotonic_seconds':90,'active_frontier':first,'target_transition_complete':True},
          {'event':'automatic_run_start','monotonic_seconds':100,'wall_origin':100,'nominal_origin':first},
          {'event':'simulation_clock_shift','monotonic_seconds':103,'action':'hold'},
          {'event':'simulation_clock_shift','monotonic_seconds':113,'action':'release'},
          {'event':'activation_end','monotonic_seconds':230,'active_frontier':'2026-01-01T00:02:00+00:00','target_transition_complete':True}]
    if runtime:
        events.write_text(json.dumps({'record_type':'history_header','format_version':2,'owner':'Runner','capture':'bounded-nonblocking'})+'\n'+''.join(json.dumps({'record_type':'runner_event',**r})+'\n' for r in rows))
    destination=tmp_path/'view'
    write_traffic_replay_tool(tmp_path/'absent-telemetry', output/'results/physical-world/physical-frames.jsonl',
        destination, workload_path=output/'results/traffic/workload.json', probe_execution_path=execution)
    return json.loads((destination/'traffic-replay.json').read_text()), destination


def test_globe_preserves_hold_migration_and_aggregate_evidence(output,tmp_path):
    data,view=observe(output,tmp_path)
    events = data['events']
    assert len(events) == 5
    assert sum(e.get('observation') == 'reply' for e in events) == 1
    assert next(e for e in events if e['record_type'] == 'generator_stopped')['received'] == 70
    assert len([e for e in events if e['record_type'] == 'generator_launched']) == 2
    assert all(e['hops'] == [] for e in events)
    assert len(data['nodes']) == 5
    page = (view/'index.html').read_text()
    assert 'createEvidenceGlobe' in page and 'traffic-seek' in page and 'traffic-play' in page
    assert 'no intermediate per-packet transit evidence' in page


def test_missing_runtime_retains_geometry_without_inventing_execution(output,tmp_path):
    data,view=observe(output,tmp_path,runtime=False)
    assert data['frames']
    assert all(not e['hops'] for e in data['events'])
    assert next(e for e in data['events'] if e['record_type']=='generator_stopped')['received']==70
