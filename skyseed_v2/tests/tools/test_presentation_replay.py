"""Presentation behavior and asynchronous controls; no browser or visual gate."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.planner.projection import _pair_index
from tools.planner._route_display import _ROUTE_SCRIPT, _PAIR_SCRIPT
from tools.traffic._spatial import _browser_index
from tools.traffic._spatial_renderer import _traffic_spatial_script
from tools._shell_display import _SHELL_SCRIPT


def _node():
    result = os.environ.get('SKYSEED_TEST_NODE') or shutil.which('node')
    if not result:
        pytest.skip('Node required for nonvisual functional execution')
    return result


def test_stable_pair_uses_typed_demand_and_resolves_independent_handover_paths(tmp_path):
    nodes = [{'kind': 'ground-station', 'node_id': 'GS with satellite-looking label'},
             *({'kind': 'satellite', 'node_id': f'opaque-{i}', 'asn': 1,
                'shell_id': shell} for i, shell in enumerate(['S1', 'S2', 'S1']))]
    routes = [
        {'key': 'gs-zone', 'label': 'unrelated text', 'source': 0, 'target': {'kind': 'zone', 'zone_id': 'Z'}, 'shard': 0, 'activity': [[0, 'reachable'], [2, 'unreachable']]},
        {'key': 's1-gs', 'source': 1, 'target': {'kind': 'ground-station', 'node': 0}, 'shard': 1, 'activity': [[0, 'reachable'], [2, None]]},
        {'key': 's2-gs', 'source': 2, 'target': {'kind': 'ground-station', 'node': 0}, 'shard': 2, 'activity': [[2, 'reachable']]},
    ]
    pairs = _pair_index(routes, nodes)
    assert len(pairs) == 1
    with pytest.raises(ValueError, match='multiple recorded demands'):
        _pair_index([routes[0], routes[0]], nodes)
    records = {
        0: {'gs-zone': {'timeline': [[0, ['reachable', 1, [0, 1]]], [2, ['unreachable', 'NO_OPERATIONAL_PATH']]], 'demand': {'typed': 'forward'}}},
        1: {'s1-gs': {'timeline': [[0, ['reachable', 3, [1, 3, 0]]], [2, None]], 'demand': {'typed': 'reverse-s1'}}},
        2: {'s2-gs': {'timeline': [[2, ['reachable', 0, [2, 0]]]], 'demand': {'typed': 'reverse-s2'}}},
    }
    program = 'const assert=require("node:assert/strict");\n' + _ROUTE_SCRIPT + _PAIR_SCRIPT
    program += '\nconst nodes=' + json.dumps(nodes) + ',DATA=' + json.dumps({'route_index': routes, 'pair_index': pairs}) + ',records=' + json.dumps(records) + ';\n'
    program += r'''
let frame={zone_assignments:[['Z',1]],operational_connections:[['isl',1,3],['access',0,1]]},index=0,routeFocus=null,routeLoadSerial=0,relationFocus=null,focus=null;
const window={SKYSEED_PLANNER_ROUTE_SHARDS:{}},pending=[];
const document={getElementById(){return null},createElement(){return{remove(){}}},body:{append(s){pending.push(s)}}};
function render(){ensurePairRoutes()}
function finish(shard){const i=pending.findIndex(s=>s.src===`data/routes-${String(shard).padStart(2,'0')}.js`);assert.ok(i>=0);const script=pending.splice(i,1)[0];window.SKYSEED_PLANNER_ROUTE_SHARDS[shard]=records[shard];script.onload()}
render();finish(0);finish(1);const key=pairFocus.key;
assert.equal(pairFocus.key,DATA.pair_index[0].key);assert.deepEqual(pairEdges().map(e=>[e.a,e.b]),[[0,1],[1,3],[3,0]]); // Return is not reversed forward.
index=1;render();assert.equal(pairFocus.key,key);assert.equal(pending.length,0);assert.equal(pairEdges().length,3);
frame={zone_assignments:[['Z',2]],operational_connections:[['access',0,1],['isl',1,3]]};index=2;render();assert.equal(pairFocus.key,key);finish(2);
assert.equal(currentPairRefs().serving,2);assert.equal(pairOutcome(currentPairRefs().forward)[0],'unreachable');assert.deepEqual(pairEdges().map(e=>[e.a,e.b]),[[2,0]]);
assert.equal(plannerPairVisible(pairFocus,currentPairRefs(),i=>nodes[i].shell_id==='S1',true),false);
assert.equal(plannerPairVisible(pairFocus,currentPairRefs(),i=>nodes[i].shell_id==='S2',true),true);
assert.match(plannerAccessExplanation(currentPairRefs(),pairFocus,frame,nodes),/no operational access/);
frame.operational_connections.push(['cross-shell',1,2]);assert.equal(plannerAccessExplanation(currentPairRefs(),pairFocus,frame,nodes),'');
frame.zone_assignments=[['Z',null]];index=3;render();assert.equal(pairFocus.key,key);assert.equal(currentPairRefs().reverse,null);assert.equal(pairEdges().length,0);
assert.equal(plannerPairVisible(pairFocus,currentPairRefs(),()=>false,false),false);assert.equal(plannerPairVisible(pairFocus,currentPairRefs(),()=>false,true),true);
// Rapid handover back, including a cache hit, fences the old pair load.
pairCache.clear();frame.zone_assignments=[['Z',1]];index=0;selectPair(DATA.pair_index[0]);
frame.zone_assignments=[['Z',2]];index=2;render();finish(0);assert.equal(pairRecords.size,0);finish(0);finish(2);assert.equal(currentPairRefs().serving,2);assert.deepEqual(pairEdges().map(e=>[e.a,e.b]),[[2,0]]);assert.ok(pairCache.size<=2);
// The primary pair view does not enumerate raw demands until expanded.
class Element{constructor(){this.children=[];this.dataset={}}append(...v){this.children.push(...v)}replaceChildren(){this.children=[]}setAttribute(){}}
const elements=new Map(),special=new Element(),visibility={nodeVisible(){return true}},node=i=>nodes[i];let rawCalls=0;
document.getElementById=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};document.createElement=()=>new Element();document.createTextNode=s=>s;showRawRoutes=()=>rawCalls++;
showCurrentRoutes();assert.equal(rawCalls,0);const drawer=document.getElementById('pair-raw');drawer.open=true;drawer.ontoggle();assert.equal(rawCalls,1);drawer.ontoggle();assert.equal(rawCalls,1);
'''
    result = subprocess.run([_node(), '-e', program], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _traffic_fixture():
    def time(seconds):
        return f'2026-01-01T00:00:{seconds:02d}+00:00'
    nodes = [{'kind': 'ground-station', 'asn': 1, 'node_id': 'g'},
             {'kind': 'satellite', 'asn': 1, 'shell_id': 'S1', 'orbit_id': 'O', 'node_id': 's'}]
    packets = [{'node_id': 's', 'simulation_timestamp': time(t)} for t in [1, 6, 9]]
    transactions = [{'identifier': 1, 'sequence': i+1, 'generator': {'episode_id': episode, 'probe_id': 'p', 'segment_index': segment},
                     'request_packet_indices': [i], 'reply_packet_indices': [], 'request': {'simulation_timestamp': time(t)},
                     'formal_boundary_timestamp': time(0 if t<5 else 5), 'classification': 'fixture', 'observed_reply': i!=1}
                    for i, (t, episode, segment) in enumerate([(1, 'A', 1), (6, 'A', 2), (9, 'B', 1)])]
    return {'shell_nodes': nodes, 'telemetry': {'packets': packets}, 'transactions': transactions,
            'physical_evidence': {'frames': [{'timestamp': time(t)} for t in [0, 5, 10]]},
            'planned_evidence': {'formal_transitions': [{'state_summary': {'zone_assignment_count': 1}}]},
            'qualification': {'episodes': [{'episode_id': 'A', 'probe_id': 'p', 'zone_id': 'Z', 'sent': 3, 'received': 1, 'loss': 2},
                                           {'episode_id': 'B', 'probe_id': 'p', 'zone_id': 'Z'},
                                           {'episode_id': 'empty', 'probe_id': 'p', 'sent': 2, 'received': 0, 'loss': 2}]}}


def test_flow_continuity_uses_episode_probe_segments_not_display_labels():
    data = _traffic_fixture()
    rows = _browser_index(data)
    assert rows['schema_version'] == 2
    assert rows['flows'][0]['transactions'] == [0, 1]
    assert rows['flows'][0]['segments'] == [1, 2]
    assert rows['flows'][1]['transactions'] == [2]
    assert rows['flows'][2]['transactions'] == []
    data['transactions'][1]['generator']['probe_id'] = 'different'
    assert _browser_index(data)['flows'][0]['transactions'] == [0]
    for tx in data['transactions']:
        tx['generator'] = None
    rows = _browser_index(data)
    assert len(rows['flows']) == 6  # Three qualified + three separate unassociated cases.
    data['telemetry']['packets'][0]['simulation_timestamp'] = None
    assert _browser_index(data)['transactions'][0]['evidence_time'] is None


_HARNESS = r'''
const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=process.argv[1],elements=new Map(),pending=[],requests=[];let context,now=0;
const drawing=new Proxy({}, {get(t,k){return t[k]||(()=>{})},set(t,k,v){t[k]=v;return true}});
class Element{
 constructor(tag='div'){this.tagName=tag;this.style={};this.dataset={};this.children=[];this.checked=true;this.value='0';this.textContent='';this.attrs={};}
 append(...children){this.children.push(...children);for(const child of children)if(child.tagName==='script'){elements.set(child.id,child);pending.push(child);requests.push(child.src)}}
 replaceChildren(...v){this.children=v}remove(){if(elements.get(this.id)===this)elements.delete(this.id);document.body.children=document.body.children.filter(v=>v!==this)}
 setAttribute(k,v){this.attrs[k]=v}querySelectorAll(){return []}addEventListener(){}setPointerCapture(){}getBoundingClientRect(){return{width:800,height:400}}getContext(){return drawing}
}
const document={getElementById(id){if(id.endsWith('-chunk'))return elements.get(id)||null;if(!elements.has(id))elements.set(id,new Element());return elements.get(id)},createElement(tag){return new Element(tag)},createTextNode(s){return s},querySelectorAll(){return[]},body:new Element('body')};
document.getElementById('traffic-direction').value='request';document.getElementById('traffic-speed').value='1';document.getElementById('traffic-planned').checked=false;
context=vm.createContext({document,devicePixelRatio:1,assert,performance:{now:()=>now},setInterval(fn){return fn},clearInterval(){},addEventListener(){}});context.window=context;
function run(s){vm.runInContext(s,context)}
function finish(name){const i=pending.findIndex(s=>s.src===name);assert.ok(i>=0,'pending '+name);const s=pending.splice(i,1)[0];run(fs.readFileSync(path.join(root,name),'utf8'));s.onload()}
run(fs.readFileSync(path.join(root,'program.js'),'utf8'));
'''


def test_play_seek_stale_frame_cache_and_bounded_assets_without_browser(tmp_path):
    data = _traffic_fixture()
    (tmp_path/'program.js').write_text(_SHELL_SCRIPT + _traffic_spatial_script(data))
    assets = tmp_path/'data'; assets.mkdir()
    for i, frame in enumerate(data['physical_evidence']['frames']):
        value = {'index': i, 'timestamp': frame['timestamp'], 'positions': [[0, 6371000, 0, 0], [1, 6471000+i, 0, 0]]}
        (assets/f'frame-{i:03d}.js').write_text('window.SKYSEED_TRAFFIC_FRAME='+json.dumps(value)+';')
    step = {'packet': 0, 'node': 1, 'ingress_peer': 0, 'arrival_supported': True, 'frame': 0, 'observed_time': 'recorded', 'decision': 'local/pass', 'reason': 'observed', 'gap_before': None}
    txs = {i: {'index': i, 'html': '', 'association_uncertainty': [], 'request': [step | {'frame': 0 if i==0 else 1}],
               'reply': [] if i==1 else [step | {'node': 0, 'ingress_peer': 1}], 'planned': [0, 1]} for i in range(3)}
    (assets/'transactions-000.js').write_text('window.SKYSEED_TRAFFIC_TRANSACTIONS='+json.dumps(txs)+';')
    harness = _HARNESS + r'''
finish('data/frame-000.js');run("assert.equal(frameIndex,0);selectFrame(1);selectFrame(0)");finish('data/frame-001.js');
run("assert.equal(frameIndex,0);assert.equal(document.getElementById('traffic-globe').dataset.frame,TRAFFIC_TIMES[0]);assert.equal(requestedFrame,0)");
run("seekTraffic(1.1)");finish('data/transactions-000.js');
run("assert.equal(selectedIndex,0);assert.equal(selectedFlow.episode_id,'A');assert.equal(JSON.parse(document.getElementById('traffic-globe').dataset.markers)[0].direction,'request');seekTraffic(1.2)");
run("assert.ok(JSON.parse(document.getElementById('traffic-globe').dataset.markers)[0].progress>0.2);seekTraffic(1.75);assert.equal(JSON.parse(document.getElementById('traffic-globe').dataset.markers)[0].direction,'reply');seekTraffic(3);assert.equal(JSON.parse(document.getElementById('traffic-globe').dataset.markers).length,0)");
run("playTraffic();assert.ok(playTimer);lastTick=0;tickTraffic(3100);assert.equal(selectedFlow.episode_id,'A');assert.equal(selectedIndex,1);assert.equal(selectedTransaction.reply.length,0);assert.equal(frameIndex,0);assert.equal(JSON.parse(document.getElementById('traffic-globe').dataset.markers).length,0)");
// Seek back while playing; late frame 1 must not overwrite cached frame 0.
run("seekTraffic(1.3);assert.ok(playTimer);assert.equal(selectedIndex,0)");finish('data/frame-001.js');run("assert.equal(frameIndex,0);pauseTraffic();assert.equal(playTimer,null)");
run("seekTraffic(6.75)");finish('data/frame-001.js');run("assert.equal(selectedIndex,1);assert.equal(selectedFlow.episode_id,'A');assert.equal(TRAFFIC_ROWS[selectedIndex].segment_index,2);assert.equal(JSON.parse(document.getElementById('traffic-globe').dataset.markers).length,0);assert.match(document.getElementById('traffic-selection-status').textContent,/NO REPLY/)");
run("seekTraffic(9.2);assert.equal(selectedFlow.episode_id,'A');assert.equal(selectedIndex,1);flowSelect.value=TRAFFIC_FLOWS[1].key;flowSelect.onchange();assert.equal(selectedIndex,2);assert.equal(selectedFlow.episode_id,'B');flowSelect.value=TRAFFIC_FLOWS[2].key;flowSelect.onchange();assert.equal(selectedIndex,null);assert.match(document.getElementById('traffic-selection-status').textContent,/No exchange/)");
for(let i=0;i<24;i++){run(`seekTraffic(${i%2?0:10})`);if(pending.length)finish(pending[0].src);run("assert.equal(frameIndex,requestedFrame);assert.equal(window.SKYSEED_TRAFFIC_FRAME,null);assert.equal(window.SKYSEED_TRAFFIC_TRANSACTIONS,null);assert.ok(Object.keys(shardData).length<=25)");assert.ok(document.body.children.filter(s=>s.tagName==='script').length<=1)}
run("const uncertain={association_uncertainty:['ambiguous'],request:[{arrival_supported:true}],reply:[]};assert.equal(trafficAnimation(uncertain,0.2).markers.length,0);const gap={association_uncertainty:[],request:[{arrival_supported:false,gap_before:'unresolved ingress'}],reply:[]};assert.equal(trafficAnimation(gap,0.2).edges.length,0)");
'''
    result = subprocess.run([_node(), '-e', harness, str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_scoped_pair_filter_retains_wrong_shell_evidence_and_never_substitutes_service(tmp_path):
    script = "const assert=require('node:assert/strict');\n" + _PAIR_SCRIPT.split('let pairFocus=')[0] + r'''
const nodes=[{kind:'ground-station'},{kind:'satellite',shell_id:'S1'},{kind:'satellite',shell_id:'S2'}];
const routes=[{source:0,target:{kind:'zone',zone_id:'Z'},network_scope:'S1'},
{source:0,target:{kind:'zone',zone_id:'Z'},network_scope:'S2'},
{source:1,target:{kind:'ground-station',node:0},network_scope:'S1'},
{source:2,target:{kind:'ground-station',node:0},network_scope:'S2'}];
const reverse=plannerReverseIndex(routes,nodes), pair={zone_id:'Z',ground_station:0,network_scope:'S1',forward:0};
let refs=plannerPairRefs(pair,[['Z',2,'S2'],['Z',1,'S1']],routes,reverse);
assert.equal(refs.serving,1);assert.equal(refs.forward,routes[0]);assert.equal(refs.reverse,routes[2]);
refs=plannerPairRefs(pair,[['Z',2,'S2'],['Z',null,'S1']],routes,reverse);
assert.equal(refs.serving,null);assert.equal(refs.reverse,null);
assert.equal(plannerPairVisible(pair,refs,()=>false,true,s=>s==='S1'),true);
assert.equal(plannerPairVisible(pair,refs,()=>true,true,s=>s==='S2'),false);
refs=plannerPairRefs(pair,[['Z',2,'S1']],routes,reverse); // Deliberately incorrect serving Shell.
assert.equal(refs.serving,2);assert.equal(refs.reverse,null);
assert.equal(plannerPairVisible(pair,refs,()=>false,true,s=>s==='S1'),true);
'''
    path=tmp_path/'scope-data-check.js';path.write_text(script)
    result=subprocess.run([_node(),str(path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
