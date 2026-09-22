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
    plan=json.loads((output/'results/traffic/probe-plan.json').read_text())
    episode=next(e for e in plan['episodes'] if next(p for p in plan['probe_identities'] if p['probe_id']==e['probe_id'])['direction']=='zone-to-ground-station')
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
        output/'results/planner/planning.jsonl.gz',output/'results/compiler/compiler.jsonl',None,destination,
        output/'results/traffic/probe-plan.json',execution,events)
    return json.loads((destination/'traffic-replay.json').read_text()), destination


def test_globe_preserves_hold_migration_and_aggregate_evidence(output,tmp_path):
    data,view=observe(output,tmp_path)
    spatial=data['spatial']
    assert data['packet_paths'] is None
    assert data['statistics']['sent']==100 and data['statistics']['received']==70
    flow=next(f for f in spatial['flows'] if f['launches'])
    assert len(flow['replies'])==1  # Seventy aggregate replies are not seventy events.
    assert len(flow['launches'])==2 and len(data['records'])==6
    assert spatial['record_times']==[None,1,2,3,131,132]
    assert spatial['progression'][1]=={'time':3,'frame':0,'held':True,'event':'simulation_clock_shift'}
    assert spatial['progression'][2]['frame']==0 and not spatial['progression'][2]['held']
    assert spatial['progression'][3]['frame']==24
    def frame(i):return json.loads((view/f'data/frame-{i:03d}.js').read_text().split('=',1)[1].removesuffix(';\n'))
    assert frame(0)['connections']
    assert frame(0)['services'] != frame(24)['services']
    assert len(spatial['nodes'])==5
    assert 'createEvidenceGlobe' in (view/'index.html').read_text()
    page = (view/'index.html').read_text()
    assert page.index('no recorded intermediate packet hops') < page.index('id="communication-globe"')
    assert 'purple dashed endpoint line = communication relationship' in page
    assert 'Internet Map live observations are not automatically Traffic replay evidence' in page
    assert all(f'href="#{anchor}"' in page for anchor in ('sender-summary', 'sender-records', 'communication-globe'))


def test_missing_runtime_keeps_planned_geometry_and_records(output,tmp_path):
    data,view=observe(output,tmp_path,runtime=False)
    assert not data['spatial']['runtime_time_available']
    assert data['spatial']['progression']==[]
    assert all(t is None for t in data['spatial']['record_times'])
    assert (view/'data/frame-000.js').is_file()
    assert data['statistics']['received']==70
