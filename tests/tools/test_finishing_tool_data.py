"""Nonvisual regressions for the final Tool correction, without opening pages."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, timezone

import pytest

from tools.observation.traffic.export import write_traffic_export
from tools.observation.traffic._spatial import _observed_steps
from tools.observation.planner.projection import _route_activity
from tools.observation.planner._route_display import _ROUTE_SCRIPT
from tools.observation._presentation.shell_display import _SHELL_SCRIPT




def test_hops_preserve_repeated_visits_direction_and_unobserved_redirects():
    def packet(node, peer, t, decision='redirect', valid='exact-generation'):
        return {'node_id': node, 'kernel_monotonic_ns': t,
                'simulation_timestamp': f'2026-01-01T00:00:{t:02d}+00:00',
                'observation': 'ingress', 'decision': decision, 'reason': 'fixture',
                'ingress_identity': {'peer_node_id': peer, 'connection_id': 'edge', 'validity': valid},
                'egress_identity': {'peer_node_id': 'c'}}
    packets = [packet('b', 'a', 1), packet('a', 'b', 2), packet('b', 'a', 6),
               packet('c', 'a', 7, valid='unresolved'), packet('a', 'b', 8, 'local/pass')]
    tx = {'request_packet_indices': [2, 0, 1, 3], 'reply_packet_indices': [4]}
    index = {'a': 0, 'b': 1, 'c': 2}
    times = [datetime(2026, 1, 1, 0, 0, i, tzinfo=timezone.utc).timestamp() for i in (0, 5, 10)]
    steps = _observed_steps(tx, 'request', packets, index, times)
    assert [s['node'] for s in steps] == [1, 0, 1, 2]
    assert [s['frame'] for s in steps] == [0, 0, 1, 1]
    assert [s['arrival_supported'] for s in steps] == [True, True, True, False]
    assert steps[-1]['gap_before'] and 'unobserved' in steps[-1]['gap_after']
    assert len(_observed_steps(tx, 'reply', packets, index, times)) == 1
    tx['association_uncertainty'] = ['identifier reuse']
    assert not any(s['arrival_supported'] for s in _observed_steps(tx, 'request', packets, index, times))
    assert _observed_steps({}, 'reply', packets, index, times) == []
    gap = _observed_steps({'request_packet_indices': [0, 4]}, 'request', packets, index, times)
    assert gap[1]['gap_before'] is None  # b -> a arrival corroborates previous node
    packets[4]['ingress_identity']['peer_node_id'] = 'c'
    assert 'missing observations' in _observed_steps({'request_packet_indices': [0, 4]}, 'request', packets, index, times)[1]['gap_before']


def _run_js(script):
    node = os.environ.get('SKYSEED_TEST_NODE') or shutil.which('node')
    if not node:
        pytest.skip('Node required for nonvisual JavaScript function checks')
    subprocess.run([node, '-e', script], check=True, capture_output=True, text=True)


def test_orbit_filter_uses_owner_metadata_and_retains_hidden_focus():
    stub = r'''
const assert=require('node:assert/strict');
class Element{constructor(){this.children=[];this.dataset={};this.style={};this.checked=true;this.value='';this.textContent=''}append(...v){this.children.push(...v)}replaceChildren(...v){this.children=v}}
const elements=new Map();const document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id)},createElement(){return new Element()},createTextNode(v){return v}};
'''
    _run_js(stub + _SHELL_SCRIPT + r'''
const nodes=[{kind:'satellite',asn:1,shell_id:'S',orbit_id:'alpha',node_id:'misleading-omega'},
{kind:'satellite',asn:1,shell_id:'S',orbit_id:'beta',node_id:'alpha-looking-name'},
{kind:'satellite',asn:2,shell_id:'S',orbit_id:'alpha'},
{kind:'satellite',asn:2,shell_id:'T',orbit_id:'alpha'}, {kind:'ground-station'}];
let changes=0;const v=createShellVisibility(nodes,3,()=>changes++),owner=document.getElementById('orbit-shell'),orbit=document.getElementById('orbit-select');
owner.value=JSON.stringify([1,'S']);owner.onchange();assert.deepEqual(nodes.map((_,i)=>v.nodeVisible(i)),[true,true,false,false,true]);
orbit.value='alpha';orbit.onchange();assert.deepEqual(nodes.map((_,i)=>v.nodeVisible(i)),[true,false,false,false,true]);
v.focus([0,2]);assert.match(document.getElementById('shell-focus-note').textContent,/1 hidden/);
assert.equal(v.edgeVisible({a:0,b:2}),false);document.getElementById('orbit-reset').onclick();assert.ok(nodes.every((_,i)=>v.nodeVisible(i)));
document.getElementById('shared-gs').checked=false;document.getElementById('shared-gs').onchange();assert.equal(v.nodeVisible(4),false);assert.equal(changes,4);
''')


def test_current_routes_resolve_persistent_outcomes_and_authoritative_zone_scope():
    assert _route_activity([[0, ['reachable', 1, [0, 1]]], [2, ['reachable', 2, [0, 2, 1]]],
                            [5, ['unreachable', 'NO_PATH']], [7, None]]) == [[0, 'reachable'], [5, 'unreachable'], [7, None]]
    _run_js("const assert=require('node:assert/strict');\n" + _ROUTE_SCRIPT + r'''
const timeline=[[2,['reachable',1,[0,1]]],[5,['unreachable','NO_PATH']],[9,null]];
assert.equal(plannerRouteState(timeline,1),null);assert.deepEqual(plannerRouteState(timeline,4),timeline[0][1]);assert.deepEqual(plannerRouteState(timeline,8),timeline[1][1]);assert.equal(plannerRouteState(timeline,9),null);
const routes=[{source:1,target:{kind:'zone',zone_id:'Z'},activity:[[0,'reachable']]},{source:2,target:{kind:'zone',zone_id:'other'},activity:[[0,'reachable']]}];
const focus={kind:'zone',id:'Z'},assignments=[['Z',3]];
assert.ok(plannerRouteRelevant({source:3,target:{kind:'ground-station',node:1}},focus,assignments,routes,4));
assert.equal(plannerRouteRelevant({source:3,target:{kind:'ground-station',node:2}},focus,assignments,routes,4),false);
assert.equal(plannerRouteRelevant({source:4,target:{kind:'ground-station',node:1}},focus,assignments,routes,4),false);
assert.ok(plannerRouteRelevant(routes[0],focus,assignments,routes,4));
assert.ok(plannerRouteRelevant({source:3,target:{node:1}}, {kind:'node',index:1},assignments,routes,4));
''')

def test_export_preserves_unknown_and_aggregate_events(tmp_path):
    source = tmp_path / 'source/visualizations/traffic'; source.mkdir(parents=True)
    events = [{'communication_id': 'a', 'segment_index': 1, 'sent': None, 'received': 3, 'hops': [], 'seconds': None}]
    (source / 'traffic-replay.json').write_text(json.dumps({'communications': [{'communication_id': 'a'}], 'events': events}))
    write_traffic_export(tmp_path / 'source', tmp_path / 'out')
    result = json.loads((tmp_path / 'out/index.json').read_text())
    assert result['events'] == events
    assert 'request_conformance' not in result
