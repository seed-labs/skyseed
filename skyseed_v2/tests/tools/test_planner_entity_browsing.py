"""Nonvisual clicks, list controls and asynchronous time/path loading in Planner."""
import json
import subprocess

from tests.tools.test_presentation_replay import _node
from tools.planner._route_display import _ROUTE_SCRIPT, _PAIR_SCRIPT
from tools.planner._ospf_display import _OSPF_SCRIPT
from tools.planner.projection import _pair_index
from tools.planner.renderer import _script


def test_entity_clicks_pair_context_and_time_use_recorded_scoped_associations(tmp_path):
    nodes = [dict(kind='ground-station', node_id='g0', display_name='Shared station'),
             dict(kind='ground-station', node_id='g1', display_name='Second station'),
             *[dict(kind='satellite', node_id=f's{i}', asn=1, shell_id=shell)
               for i, shell in enumerate(['S1', 'S1', 'S2', 'S1'])],
             dict(kind='ground-station', node_id='g2', display_name='No association')]
    routes, records = [], {}

    def route(key, source, target, scope, timeline):
        shard = len(routes)
        target["network_scope"] = scope
        routes.append(dict(key=key, label=key, source=source, target=target,
                           network_scope=scope, shard=shard,
                           activity=[[i, v[0] if v else None] for i, v in timeline]))
        records[shard] = {key: dict(timeline=timeline, demand={'fixture': key})}

    def zone(name):
        return dict(kind='zone', zone_id=name)

    def ground(index):
        return dict(kind='ground-station', node=index)

    route('z1', 0, zone('Z'), 'S1', [[0, ['reachable', 2, [0, 2]]],
          [1, ['unreachable', 'NO_ELIGIBLE_PATH']], [2, ['unreachable', 'ZONE_UNSERVED']],
          [3, None], [4, ['reachable', 3, [0, 3]]]])
    route('return-a', 2, ground(0), 'S1', [[0, ['reachable', 5, [2, 5, 0]]], [1, None]])
    route('return-b', 3, ground(0), 'S1', [[1, ['reachable', 5, [3, 5, 0]]], [2, None],
          [3, ['reachable', 5, [3, 5, 0]]]])
    route('q1', 0, zone('Q'), 'S1', [[0, ['unreachable', 'ZONE_UNSERVED']]])
    route('z2', 0, zone('Z'), 'S2', [[0, ['reachable', 4, [0, 4]]]])
    route('return-s2', 4, ground(0), 'S2', [[0, ['reachable', 0, [4, 0]]]])
    route('other-gs', 1, zone('Z'), 'S1', [[0, ['reachable', 2, [1, 2]]],
          [1, ['reachable', 3, [1, 3]]], [2, ['unreachable', 'ZONE_UNSERVED']], [3, ['reachable', 3, [1, 3]]]])
    route('return-other', 2, ground(1), 'S1', [[0, ['reachable', 1, [2, 1]]]])
    for i in range(23):
        route(f'extra-{i}', 1, zone(f'P{i:02}'), 'S1', [[0, ['unreachable', 'ZONE_UNSERVED']]])
    pairs = _pair_index(routes, nodes)
    frames = [dict(zone_assignments=[['Z', serving, 'S1'], ['Q', None, 'S1'], ['Z', 4, 'S2']],
                   operational_connections=[['access', 0, 2], ['access', 0, 4], ['isl', 2, 5], ['access', 0, 5]],
                   timestamp=f't{i}') for i, serving in enumerate([2, 3, None, 3, 3])]
    data = dict(route_index=routes, pair_index=pairs,
                frame_index=[dict(chunk=f'data/frame-{i:03d}.js') for i in range(5)])

    # Use the production map/globe click handlers, clear/Escape handlers and
    # frame loader. Only the canvas drawing and unrelated stage drawers are stubbed.
    renderer = _script({})
    wiring = 'function loadFrame' + renderer.split('function loadFrame', 1)[1].split('function prep', 1)[0]
    wiring += 'function choose' + renderer.split('function choose', 1)[1].split('function focusSelection', 1)[0]
    wiring += 'slider.oninput=' + renderer.split('slider.oninput=', 1)[1].split("document.querySelectorAll", 1)[0]
    wiring += "document.getElementById('play').onclick=" + renderer.split("document.getElementById('play').onclick=", 1)[1].split("document.getElementById('focus-view')", 1)[0]
    for canvas in ('globe', 'map'):
        wiring += canvas + '.onpointerdown=' + renderer.split(canvas + '.onpointerdown=', 1)[1].split(canvas + '.onwheel=', 1)[0]
    wiring += "document.getElementById('clear-focus').onclick=" + renderer.split("document.getElementById('clear-focus').onclick=", 1)[1].split(';', 1)[0] + ';'
    wiring += "window.addEventListener('keydown'" + renderer.split("window.addEventListener('keydown'", 1)[1].split('\n', 1)[0]
    program = 'const assert=require("node:assert/strict");\n' + _ROUTE_SCRIPT + _PAIR_SCRIPT + _OSPF_SCRIPT
    program += '\nconst nodes=' + json.dumps(nodes) + ',DATA=' + json.dumps(data) + ',records=' + json.dumps(records) + ',frames=' + json.dumps(frames) + ';\n'
    program += r'''
class Element{
 constructor(){this.children=[];this.dataset={};this.value='';this.checked=true;this.textContent=''}
 append(...v){this.children.push(...v)}replaceChildren(...v){this.children=[...v]}setAttribute(){}remove(){}setPointerCapture(){}
 getBoundingClientRect(){return {left:0,top:0}}
}
const elements=new Map(),pending=[],events=new Map();
const document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id)},createElement(){return new Element()},createTextNode:s=>s,body:{append(s){pending.push(s)}}};
const window={SKYSEED_PLANNER_ROUTE_SHARDS:{},addEventListener(name,fn){events.set(name,fn)}};
const visibleShells=new Set(['S1','S2']),visibility={nodeVisible:i=>nodes[i].kind==='ground-station'||visibleShells.has(nodes[i].shell_id),shellVisible:s=>visibleShells.has(s)};
const node=i=>nodes[i],zones=[{zone_id:'Z'},{zone_id:'Q'}],special=new Element(),stamp=new Element(),slider=new Element(),globe=new Element(),map=new Element(),UI={drag_threshold_css_pixels:5};
let frame=null,index=0,stage='route',focus=null,relationFocus=null,routeFocus=null,routeLoadSerial=0,serial=0,ospfView=null,drag=null,mapDrag=null,hits2=[],hits3=[],playing=null,tick=null;
function setInterval(fn){tick=fn;return 1}function clearInterval(){tick=null}
function drawGlobe(){}function drawMap(){}function showRouteOutcome(){}
function render(){if(!frame)return;ensurePairRoutes();updatePairHighlights();showCurrentRoutes()}
function text(el){return typeof el==='string'?el:el.textContent+' '+el.children.map(text).join(' ')}
function buttons(){return document.getElementById('pair-list').children.filter(e=>e.dataset?.pairKey)}
function routeNames(){return buttons().map(b=>DATA.route_index[DATA.pair_index.find(p=>p.key===b.dataset.pairKey).forward].key).sort()}
function clickPair(name){const pair=DATA.pair_index.find(p=>DATA.route_index[p.forward].key===name);const button=buttons().find(b=>b.dataset.pairKey===pair.key);assert.ok(button,name);button.onclick();return pair.key}
function finishRoute(shard){const at=pending.findIndex(s=>s.src===`data/routes-${String(shard).padStart(2,'0')}.js`);assert.ok(at>=0,'requested shard '+shard);const script=pending.splice(at,1)[0];window.SKYSEED_PLANNER_ROUTE_SHARDS[shard]=records[shard];script.onload()}
function finishFrame(i){const at=pending.findIndex(s=>s.src===`data/frame-${String(i).padStart(3,'0')}.js`);assert.ok(at>=0);const script=pending.splice(at,1)[0];window.SKYSEED_PLANNER_FRAME_CHUNK=structuredClone(frames[i]);script.onload()}
function scrub(i){slider.value=String(i);slider.oninput();finishFrame(i)}
function clickEntity(canvas,value){const hit={...value,x:10,y:10,r:8};if(canvas===map)hits2=[hit];else hits3=[hit];const e={clientX:10,clientY:10,pointerId:1};canvas.onpointerdown(e);canvas.onpointerup(e)}
'''
    program += wiring + r'''
loadFrame(0);finishFrame(0);
assert.equal(pairFocus,null);assert.equal(pending.length,0);assert.deepEqual(buttons(),[]);
assert.match(document.getElementById('pair-context').textContent,/Select a Zone or GroundStation/);
// Globe GS selection: count distinct geographic entities separately from scope pairs.
clickEntity(globe,{kind:'node',index:0});assert.deepEqual(routeNames(),['q1','z1','z2']);
assert.match(text(document.getElementById('pair-list')),/2 associated Zones \(unique\).*3 recorded pairs/);
assert.deepEqual([...pairHighlights.zones].sort(),['Q','Z']);assert.equal(pending.length,0);
const selected=clickPair('z1');assert.deepEqual(focus,{kind:'node',index:0});
finishRoute(0);finishRoute(1);assert.deepEqual(pairEdges().map(e=>[e.a,e.b]),[[0,2],[2,5],[5,0]]);
assert.equal(pairHighlights.serving,2);assert.match(text(document.getElementById('pair-outcome')),/Shared station/);
// Handover changes the reverse typed demand; no old path waits on the map.
scrub(1);assert.equal(pairFocus.key,selected);assert.deepEqual(focus,{kind:'node',index:0});
assert.equal(pairEdges().length,0);finishRoute(2);assert.deepEqual(pairEdges().map(e=>[e.a,e.b]),[[3,5],[5,0]]);
assert.equal(pairHighlights.serving,3);assert.match(text(document.getElementById('pair-outcome')),/NO_ELIGIBLE_PATH/);
scrub(2);assert.equal(pairFocus.key,selected);assert.equal(pairEdges().length,0);assert.equal(pairHighlights.serving,null);
assert.deepEqual(routeNames(),['q1','z1','z2']);assert.match(text(document.getElementById('pair-outcome')),/UNSERVED/);
const unserved=document.getElementById('pair-unserved');unserved.checked=false;unserved.onchange();
assert.deepEqual(routeNames(),['z2']);assert.equal(pairFocus.key,selected);assert.match(text(document.getElementById('pair-outcome')),/hidden by current filter/);
unserved.checked=true;unserved.onchange();scrub(3);
assert.equal(pairOutcome(currentPairRefs().forward),null);assert.match(text(document.getElementById('pair-outcome')),/Absent \/ withdrawn/);
assert.deepEqual(pairEdges().map(e=>[e.a,e.b]),[[3,5],[5,0]]);assert.ok(pairCache.size<=2);
// Playback uses the same frame loader and preserves the entity and pair.
const play=document.getElementById('play');play.onclick({target:play});assert.ok(tick);tick();finishFrame(4);
assert.deepEqual(focus,{kind:'node',index:0});assert.equal(pairFocus.key,selected);assert.deepEqual(pairEdges().map(e=>[e.a,e.b]),[[0,3],[3,5],[5,0]]);
play.onclick({target:play});assert.equal(tick,null);
// A late frame callback cannot overwrite a newer time selection.
slider.value='0';slider.oninput();slider.value='3';slider.oninput();finishFrame(0);assert.equal(index,4);finishFrame(3);assert.equal(index,3);assert.equal(pairFocus.key,selected);
// Map Zone selection narrows the primary list and clears the unrelated pair/path.
clickEntity(map,{kind:'zone',id:'Z'});assert.equal(pairFocus,null);assert.equal(pairEdges().length,0);
assert.deepEqual(routeNames(),['other-gs','z1','z2']);assert.match(text(document.getElementById('pair-list')),/2 associated GroundStations \(unique\).*3 recorded pairs/);
assert.deepEqual([...pairHighlights.nodes].sort(),[0,1]);
visibleShells.delete('S2');render();assert.deepEqual(routeNames(),['other-gs','z1']);
// A text filter narrows only this entity's recorded pairs, never the global list.
const query=document.getElementById('pair-query');query.value='Second station';query.oninput();assert.deepEqual(routeNames(),['other-gs']);
query.value='';query.oninput();clickPair('z1');assert.deepEqual(focus,{kind:'zone',id:'Z'});
// Changing entity while an old shard is in flight must fence its callback.
clickEntity(globe,{kind:'node',index:1});finishRoute(0);assert.equal(pairFocus,null);assert.equal(pairRecords.size,0);assert.equal(pending.length,0);
assert.equal(buttons().length,20);assert.match(text(document.getElementById('pair-list')),/24 associated Zones \(unique\)/);
document.getElementById('pair-list').children.find(e=>e.textContent==='Next pairs').onclick();assert.equal(buttons().length,4);
clickPair('other-gs');assert.equal(pairPage,1);assert.deepEqual(focus,{kind:'node',index:1});finishRoute(6);
assert.equal(pairEdges().length,1); // No reverse demand is recorded for the current serving satellite.
// Scope filtering retains explicit selection, while route segments obey node visibility.
visibleShells.delete('S1');render();assert.equal(buttons().length,0);assert.equal(pairFocus.network_scope,'S1');
assert.equal(pairEdges().filter(e=>visibility.nodeVisible(e.a)&&visibility.nodeVisible(e.b)).length,0);
assert.match(text(document.getElementById('pair-outcome')),/hidden by current filter/);
visibleShells.add('S1');visibleShells.add('S2');
// Clear button, repeated click, empty-space click and Escape share one selection reset.
document.getElementById('clear-focus').onclick();assert.equal(focus,null);assert.equal(pairFocus,null);assert.equal(pairEdges().length,0);assert.equal(buttons().length,0);
clickEntity(map,{kind:'zone',id:'Z'});clickEntity(map,{kind:'zone',id:'Z'});assert.equal(focus,null);
clickEntity(globe,{kind:'node',index:0});hits3=[];globe.onpointerdown({clientX:100,clientY:100,pointerId:1});globe.onpointerup({clientX:100,clientY:100});assert.equal(focus,null);
clickEntity(map,{kind:'zone',id:'Z'});clickPair('z1');events.get('keydown')({key:'Escape'});finishRoute(0);assert.equal(pairRecords.size,0);assert.equal(focus,null);assert.equal(pending.length,0);
// Global browsing is explicit; satellite transit does not create associations.
document.getElementById('pair-global').onclick();assert.equal(buttons().length,20);assert.equal(pairFocus,null);assert.equal(pairGlobal,true);
clickEntity(map,{kind:'node',index:5});assert.equal(buttons().length,0);assert.equal(pairRawOpen,true);assert.equal(pairFocus,null);
clickEntity(globe,{kind:'node',index:6});assert.equal(buttons().length,0);assert.match(text(document.getElementById('pair-list')),/No recorded business associations/);
// Association marks draw endpoint rings, never link segments.
clickEntity(map,{kind:'zone',id:'Z'});const strokes=[];const ctx={beginPath(){},arc(...v){strokes.push(v)},stroke(){}};
drawPairNodeMark(ctx,[5,6],0);assert.equal(strokes.length,1);assert.equal(ctx.strokeStyle,'#d8a7ff');
assert.equal(pairEdges().length,0);stage='gs';render();assert.equal(pairHighlights.nodes.size,0);assert.deepEqual(focus,{kind:'zone',id:'Z'});
'''
    script = tmp_path/'entity-browsing.js'
    script.write_text(program)
    result = subprocess.run([_node(), str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
