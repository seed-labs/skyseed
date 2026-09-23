"""Existing evidence consumers preserve Satellite identity without inferred paths."""
from datetime import timedelta
from pathlib import Path
import json

import pytest
from skyseed import simulator as api
from skyseed.experiment import ExperimentController
from skyseed.backend import compile_plan
from tools.observation.planner import read_planner_history, collect_planner_data, write_planner_tool
from tools.observation.planner.export import write_network_export
from tools.observation.physical_world import write_physical_world_tool
from tools.observation.bpf_compiler.reader import read_bpf_compiler_inspection
from tools.development import check_scene
from templates.dataplane.threshold import scene
from tests.unit.test_satellite_route_targets import Snapshots, DEMAND


@pytest.mark.parametrize('backend', ['linux','bpf'])
def test_recorded_satellite_target_roundtrips_tools_and_network_export(tmp_path, monkeypatch, backend):
    from examples.Sky00_tiny_network import tiny_network as tiny
    class SatelliteRoutes(api.GatewayHopCountRoutePlanningModel):
        def can_reuse_unchanged_inputs(self):
            return False
        def create_route_demands(self, world, assignments, *, timestamp):
            node_ids = world.frame_at(timestamp).positions
            satellite = next(p for p in node_ids if p.entity_kind == 'satellite')
            ground = next(p for p in node_ids if p.entity_kind == 'ground-station')
            return (api.RouteDemand(ground,api.SatelliteRouteDemandTarget(satellite)),)
        def plan_route_outcomes(self, world, connections, assignments, demands, previous, *, timestamp):
            offset = (timestamp-tiny.DEFAULT_START_TIME).total_seconds()
            if offset == 0:
                return api.RoutePlanningResult((api.LocalRouteOutcome(demands[0],api.RouteDisposition.PASS),))
            if offset == 10:
                return api.RoutePlanningResult()
            return super().plan_route_outcomes(world,connections,assignments,demands,(),timestamp=timestamp)
    monkeypatch.setattr(tiny.sky.simulator.planner,'GatewayHopCountRoutePlanningModel',SatelliteRoutes)
    network = tiny.build_emulator()
    sim = tiny.create_simulator(network,duration=timedelta(seconds=10),observation_interval=timedelta(seconds=5))
    generated = tmp_path/'generated'
    ExperimentController(network,sim,generated,entry_path=Path(tiny.__file__),through='compiler',
                         forwarding_backend=backend,traffic_enabled=False).generate()
    evidence = read_planner_history(generated/'results/planner/planning.jsonl.gz')
    data = collect_planner_data(evidence)
    route, = data['route_index']
    assert route['target']['kind'] == 'satellite'
    assert route['activity'][-1][1] is None
    assert data['frames'][0]['state_summary']['local_route_count'] == 1
    assert any(f['state_summary']['reachable_route_count'] == 1 for f in data['frames'])
    for publication in evidence:
        encoded = api.published_planning_evidence_to_data(publication)
        assert api.published_planning_evidence_from_data(json.loads(json.dumps(encoded))) == publication
        with pytest.raises(ValueError,match='schema'):
            api.published_planning_evidence_from_data({**encoded,'schema':'skyseed-v2-published-planning-evidence-v6'})
    plans = tuple(p.plan for p in evidence)
    compiled = []
    for plan in plans:
        compiled.append(compile_plan(plan,sim.get_physical_world(),compiled[-1] if compiled else None,
                                     forwarding_backend=backend))
    live = read_bpf_compiler_inspection(plans,tuple(compiled)).snapshot
    assert '"kind": "satellite"' in json.dumps(live)
    write_planner_tool(evidence,tmp_path/'planner-view')
    write_physical_world_tool(sim.get_physical_world(),generated/'visualizations/physical-world',
        physical_frames_path=generated/'results/physical-world/physical-frames.jsonl')
    write_network_export(generated,tmp_path/'export')
    demand, = json.loads((tmp_path/'export/network/demands.json').read_text())['demands']
    assert set(demand['target']) == {'satellite_id','network_scope'}
    frame = data['frames'][0]
    change, = frame['transition']['route_outcome_transitions']
    target = change['demand']['target']['satellite_id']
    source = change['demand']['source']
    def node(n):return f"{n['asn']}/{n['entity_kind']}/{n['entity_id']}"
    outcome = {'demand':change['demand'],**change['current']}
    transaction = {'request':{'simulation_timestamp':frame['timestamp'],
                              'source_ipv4':'192.0.2.1','destination_ipv4':'192.0.2.2'},
                   'observed_reply':False,'identifier':1,'sequence':1,
                   'packet_observations':1,'pairing_status':'request-only'}


def test_standalone_scene_check_accepts_satellite_paths_and_local_results(tmp_path):
    def factory():
        sim = scene.create_scene(Snapshots(),duration=10)
        sim.get_planner().set_route_demands((DEMAND,))
        return sim
    checks = check_scene(factory,stop_at='materials',output=tmp_path/'check')
    assert checks[0].status == 'pass'
    assert all(value.status != 'error' for value in checks)


def test_satellite_navigation_is_an_endpoint_not_a_zone_reverse_association():
    import shutil
    import subprocess
    from tools.observation.planner._route_display import _ROUTE_SCRIPT
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node required for nonvisual navigation checks')
    checks = r'''
const assert = require('node:assert/strict');
const sat = {source: 2, target: {kind:'satellite', node:1}};
const zone = {source:1, target:{kind:'zone',zone_id:'z'},activity:[[0,'reachable']]};
const gs = {source:2, target:{kind:'ground-station',node:1}};
assert.equal(plannerRouteRelevant(sat,{kind:'node',index:1},[],[],0),true);
assert.equal(plannerRouteRelevant(sat,{kind:'node',index:2},[],[],0),true);
assert.equal(plannerRouteRelevant(sat,{kind:'zone',id:'z'},[['z',2]],[zone],0),false);
assert.equal(plannerRouteRelevant(gs,{kind:'zone',id:'z'},[['z',2]],[zone],0),true);
'''
    result = subprocess.run([node,'-e',_ROUTE_SCRIPT+checks],capture_output=True,text=True)
    assert result.returncode == 0,result.stderr
