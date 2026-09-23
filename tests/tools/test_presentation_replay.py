"""Presentation behavior and asynchronous controls; no browser or visual gate."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.observation.planner.projection import _pair_index
from tools.observation.planner._route_display import _ROUTE_SCRIPT, _PAIR_SCRIPT
from tools.observation._presentation.shell_display import _SHELL_SCRIPT


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
        0: {'gs-zone': {'timeline': [[0, ['reachable', 1, [0, 1]]], [2, ['unreachable', 'NO_ELIGIBLE_PATH']]], 'demand': {'typed': 'forward'}}},
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
render();assert.equal(pairFocus,null);assert.equal(pending.length,0);selectPair(DATA.pair_index[0]);finish(0);finish(1);const key=pairFocus.key;
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


def test_planner_model_panel_resolves_actual_assembly_and_historical_descriptors(tmp_path):
    # Execute the panel's selection expression, including the GS validation gate.
    # The renderer is a Python f-string; only the literal braces need unescaping.
    import re
    from tools.observation.planner import renderer
    source = Path(renderer.__file__).read_text()
    expression = re.search(r'const descriptors=(.*?);modelDetail', source).group(1)
    expression = expression.replace('{{', '{').replace('}}', '}')
    program = 'const assert=require("node:assert/strict");\n'
    program += 'function select(DATA,stage,gsStep){const descriptors=' + expression + ';return model;}\n'
    program += '''
const current={implementation:'actual injected ISL',configuration:{maximum_cross_plane_separation:3}};
const past={implementation:'recorded historical ISL',configuration:{maximum_cross_plane_separation:1}};
const data=v=>({model_provenance:{descriptors:v}});
for(const stage of ['isl','lifecycle']){
  assert.equal(select(data({inter_satellite:current}),stage,''),current);
  assert.equal(select(data({isl_selection:past}),stage,''),past);
}
assert.equal(select(data({ground_station:current}),'gs','allocation'),current);
assert.equal(select(data({ground_station:current}),'gs','validation').planning_model_invoked,'no');
'''
    result = subprocess.run([_node(), '-e', program], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
