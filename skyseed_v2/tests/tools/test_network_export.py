from tools.observation.planner.export import _zone_feature


def test_network_metrics_include_pending_lifecycle_capacity():
    from types import SimpleNamespace
    from tools.observation.planner.export import _reserved_ground_accesses
    operational, pending = object(), object()
    evidence = SimpleNamespace(
        selected_ground_station_accesses=(operational,),
        ground_station_preparations=(
            SimpleNamespace(candidate=operational, operational=True),
            SimpleNamespace(candidate=pending, operational=False),
        ),
    )
    assert _reserved_ground_accesses(evidence) == (operational, pending)
    evidence.ground_station_preparations = ()
    assert _reserved_ground_accesses(evidence) == (operational,)


def test_date_line_zone_is_two_bounded_geojson_polygons():
    feature=_zone_feature({'zone_id':'date-line','geometry':{'west_longitude_deg':170,'east_longitude_deg':-170,'south_latitude_deg':-10,'north_latitude_deg':10}})
    assert feature['geometry']['type']=='MultiPolygon'
    for polygon in feature['geometry']['coordinates']:
        ring=polygon[0]
        assert ring[0]==ring[-1]
        assert all(abs(a[0]-b[0])<=180 for a,b in zip(ring,ring[1:]))


def test_offline_export_matches_sealed_formal_frames_and_has_no_runtime_timing(tmp_path, monkeypatch):
    import json
    from examples.Sky00_tiny_network.tiny_network import build_emulator
    from examples.Sky00_tiny_network.tiny_network import create_simulator as create_complete_static_simulator
    from skyseed_v2.experiment import ExperimentController
    from tools.observation.planner import read_planner_history
    from examples.Sky00_tiny_network import tiny_network as tiny
    from pathlib import Path
    from tools.observation.physical_world import write_physical_world_tool
    from tools.observation.planner.export import write_network_export
    generated=tmp_path/'generated'
    network=build_emulator()
    simulator=create_complete_static_simulator(network)
    ExperimentController(network,simulator,generated,entry_path=Path(tiny.__file__),
        through='planner',traffic_enabled=False).generate()
    plans=tuple(record.plan for record in read_planner_history(generated/'results/planner/planning.jsonl.gz'))
    write_physical_world_tool(simulator.get_physical_world(),generated/'visualizations/physical-world',physical_frames_path=generated/'results/physical-world/physical-frames.jsonl')
    from tools.observation.planner import export, reader
    calls = []
    original = export.apply_plan_transition
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError('export reader must not replay transitions')
    monkeypatch.setattr(export, 'apply_plan_transition', counted)
    monkeypatch.setattr(reader, 'apply_plan', forbidden)
    result=write_network_export(generated,tmp_path/'delivery')
    assert len(calls) == sum(len(p.transitions) for p in plans)
    assert result['formal_frames']==sum(len(p.transitions) for p in plans)
    assert result['traffic']['status']=='unavailable'
    index=json.loads((tmp_path/'delivery/network/index.json').read_text())
    for frame in index['frames']:
        data=json.loads((tmp_path/'delivery/network'/frame['file']).read_text())
        assert data['timestamp_utc']==frame['timestamp_utc']
        assert 'operational_connections' in data and 'zone_assignments' in data
    for path in (tmp_path/'delivery').rglob('*.json'):
        text=path.read_text()
        assert 'monotonic' not in text and 'lag_seconds' not in text


def test_traffic_export_keeps_executor_cases_without_fabricating_request_time(tmp_path):
    from datetime import datetime, timezone
    import json
    from tools.observation.traffic.export import write_traffic_export
    generated=tmp_path/'generated';source=generated/'visualizations/traffic';source.mkdir(parents=True)
    (source/'traffic-replay.json').write_text(json.dumps({'transactions':[], 'inventory':{'packet_observations':0},
        'qualification':{'summary':{'sent':1,'received':0,'loss':1,'status':'WARN','runner_lag_seconds':99},
        'episodes':[{'episode_id':'e1','probe_id':'p1','sent':1,'received':0,'loss':1,
            'request_conformance':[{'segment_index':0,'sequence':1,'reply':False,'classification':'expected-unreachable',
                'request_interval_monotonic_ns':[1,2],'report_monotonic_ns':3,'transaction':None}]}]}}))
    output=tmp_path/'traffic'
    write_traffic_export(generated,output,datetime(2026,1,1,tzinfo=timezone.utc),{})
    case=json.loads((output/'request-cases-0000.json').read_text())['cases'][0]
    assert case['classification']=='expected-unreachable' and case['request_seconds'] is None
    assert json.loads((output/'flow-results.json').read_text())['flows'][0]['sent']==1
    for path in output.glob('*.json'):
        assert 'monotonic' not in path.read_text() and 'lag_seconds' not in path.read_text()
