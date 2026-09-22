"""Native spatial context over sealed Plans and recorded runtime/sender time.

No Models, runtime commands, inferred hops or synthetic packet events. One pass
through Plans emits compact geometry shards; the page loads one frame at a time.
"""
from pathlib import Path

from skyseed_v2.simulator import apply_plan_transition, planned_operational_state_fingerprint
from tools.observation._history import read_sealed_physical_frames
from tools.observation.planner.reader import _iter_traffic_planner_history
from tools.observation.runner.reader import read_runner_event_history
from tools.observation._presentation.html import json_for_script
from tools.observation._presentation.spatial_display import _evidence_globe_script
from .reader import _read_probe_plan


def write_sender_spatial(records, directory, *, physical_frames_path, planning_path,
                         probe_plan_path=None, runner_events_path=None):
    from skyseed_v2.experiment.output.history import _read_domain_history_manifest
    planning_manifest = _read_domain_history_manifest(Path(planning_path), minimum_through='compiler')
    if Path(physical_frames_path).resolve() != planning_manifest.stream_path("physical_frames"):
        raise ValueError('native spatial inputs must share a sealed history root')
    frames = read_sealed_physical_frames(Path(physical_frames_path))
    nodes = [{'node_id': p['node_id'], 'kind': p['kind']} for p in frames[0]['positions']]
    indices = {n['node_id']: i for i, n in enumerate(nodes)}
    times = [f['timestamp'] for f in frames]
    frame_indices = {timestamp: i for i, timestamp in enumerate(times)}
    root = Path(directory) / 'data'; root.mkdir(parents=True, exist_ok=True)
    state, count = None, 0
    for published in _iter_traffic_planner_history(Path(planning_path)):
        plan = published.plan
        if (None if state is None else planned_operational_state_fingerprint(state)) != plan.input_state_fingerprint:
            raise ValueError('native replay Plan input fingerprint differs')
        for transition in plan.transitions:
            state = apply_plan_transition(state, transition)
            if count >= len(frames) or times[count] != state.timestamp.isoformat():
                raise ValueError('native replay physical and planning timestamps differ')
            frame = frames[count]
            value = {'index': count, 'timestamp': frame['timestamp'],
                'positions': [[indices[p['node_id']], *(p['ecef_m'][a] for a in ('x', 'y', 'z'))] for p in frame['positions']],
                'connections': [[indices[c.connection_id.endpoint_a.value], indices[c.connection_id.endpoint_b.value], c.connection_id.kind.value]
                                for c in state.operational_connections],
                'services': [[s.network_scope, s.zone_id, indices[s.satellite_id.value] if s.is_served else None]
                             for s in state.zone_service_assignments]}
            (root / f'frame-{count:03d}.js').write_text('window.SKYSEED_NATIVE_FRAME=' + json_for_script(value) + ';\n')
            count += 1
        if planned_operational_state_fingerprint(state) != plan.output_state_fingerprint:
            raise ValueError('native replay Plan output fingerprint differs')
    if count != len(frames):
        raise ValueError('native replay physical and planning coverage differs')
    plan = _read_probe_plan(Path(probe_plan_path)) if probe_plan_path and Path(probe_plan_path).exists() else {}
    identities = {p['probe_id']: p for p in plan.get('probe_identities', ())}
    flows = {p['episode_id']: {**identities[p['probe_id']], 'episode_id': p['episode_id'],
             'record_indices': [], 'launches': [], 'replies': [], 'changes': []} for p in plan.get('episodes', ())}
    events = read_runner_event_history(Path(runner_events_path)) if runner_events_path and Path(runner_events_path).exists() else ()
    start = next((e for e in events if e['event'] == 'automatic_run_start'), None)
    origin = start.get('wall_origin', start['monotonic_seconds']) if start else None
    timed = origin is not None
    if start and start.get('nominal_origin') != times[0]:
        raise ValueError('Runner and retained physical origins differ')
    progression, held, active = [], False, None
    # Completed activation is the only basis for choosing a runtime spatial frame.
    # Missing/incomplete activation and capture gaps invalidate context until the
    # next complete activation; a planned link is never claimed as packet evidence.
    for e in events:
        kind = e['event']; at = max(0, e['monotonic_seconds'] - origin) if timed else None
        changed = False
        if kind == 'activation_end':
            timestamp = e.get('active_frontier', e.get('boundary_timestamp'))
            active = frame_indices.get(timestamp) if e.get('target_transition_complete', True) and e.get('semantic_outcome', 'activated') == 'activated' else None
            changed = True
        elif kind == 'simulation_clock_shift':
            held = e['action'] == 'hold'; changed = True
        elif kind in ('observation_gap', 'event_history_gap', 'failure'):
            active = None; changed = True
        if timed and changed:
            progression.append({'time': at, 'frame': active, 'held': held, 'event': kind})
    record_times = []
    for i, row in enumerate(records):
        at = (row['host_monotonic_ns'] / 1e9 - origin) if timed and 'host_monotonic_ns' in row else None
        record_times.append(at)
        episode = row.get('episode_id')
        if episode is None:
            continue
        flow = flows.setdefault(episode, {'episode_id': episode, **identities.get(row.get('probe_id'), {}),
                'record_indices': [], 'launches': [], 'replies': [], 'changes': []})
        flow['record_indices'].append(i)
        if row.get('record_type') == 'generator_launched': flow['launches'].append(i)
        if row.get('observation') in ('reply', 'timeout'): flow['replies'].append(i)
        if row.get('record_type') in ('generator_launched', 'generator_stopped', 'episode_paused', 'episode_completed', 'episode_cancelled', 'zone_source_migration'):
            flow['changes'].append(i)
    for flow in flows.values():
        flow['ground_node'] = indices.get(flow.get('ground_station_id'))
    end = max([0, *(t for t in record_times if t is not None), *(p['time'] for p in progression)])
    return {'nodes': nodes, 'times': times, 'flows': list(flows.values()),
            'record_times': record_times, 'progression': progression,
            'runtime_time_available': timed, 'end_seconds': end if timed else len(times)-1,
            'basis': 'recorded runtime elapsed seconds; geometry at latest completed ACTIVE boundary' if timed else 'planned frame index; runtime timing unavailable',
            'frame_count': count,
            'probe_plan_revisions': [p['planning_horizon']['revision_id'] for p in plan.get('delivery_plans', [plan]) if p]}


BODY = '''<section class="card" id="communication-globe"><h2>Communication globe</h2>
<p><strong>Legend:</strong> gold = GS; cyan = satellite; purple dashed endpoint line = communication relationship.
<strong>No intermediate packet hops are recorded.</strong> Reply highlights are sender observations.
Geometry and thin dynamic links are retained planned context at the last completed runtime ACTIVE boundary.
Zone service is the recorded planned assignment at that boundary, not an observed packet route.</p>
<div class="controls"><label for="traffic-flow">Communication</label><select id="traffic-flow" style="max-width:100%;min-width:0"></select></div>
<p id="traffic-flow-summary"></p><div class="controls">
<button id="traffic-play">Play</button><label class="controls" style="margin:0">Speed <select id="traffic-speed"><option value="1">1×</option><option value="5">5×</option><option value="20">20×</option><option value="100">100×</option></select></label>
<label class="controls" style="margin:0">Seek <input id="traffic-seek" aria-label="Recorded runtime elapsed seconds" type="range" min="0" value="0" step="0.01"></label>
<strong id="traffic-play-time" style="white-space:nowrap;font-variant-numeric:tabular-nums"></strong><button id="traffic-focus">Focus GS</button><button id="traffic-reset-globe">Reset globe</button>
<label class="controls" style="margin:0"><input id="traffic-links" type="checkbox" checked> Planned dynamic connections</label></div>
<canvas id="traffic-globe" width="1100" height="520" style="width:100%;height:520px;background:#06111e;touch-action:none;cursor:grab"></canvas>
<p id="traffic-geometry-basis" role="status"></p><p id="traffic-selection-status" role="status"></p>
<p>Reply highlights persist for one recorded second at the sender. They indicate an actual sender observation, with no path or packet motion inferred. Aggregate counts never create packet events. A timeout is a sender report, not a located network drop.</p>
<details open><summary>Recorded evidence at this time</summary><pre id="traffic-evidence"></pre></details></section>'''


def script(spatial):
    return 'const SPATIAL=' + json_for_script(spatial) + ';\n' + _evidence_globe_script() + r'''
const flowSelect=document.getElementById('traffic-flow'),seek=document.getElementById('traffic-seek'),canvas=document.getElementById('traffic-globe');
const globe=createEvidenceGlobe(canvas,SPATIAL.nodes,{nodeVisible:()=>true,edgeVisible:()=>true,color:()=> '#5ee5e5'});
const byNode=new Map(SPATIAL.nodes.map((n,i)=>[n.node_id,i]));
let flow=SPATIAL.flows[0],time=0,frame=null,requested=null,serial=0,timer=null,last=null;
for(const f of SPATIAL.flows){const o=document.createElement('option');o.value=f.episode_id;o.textContent=`${f.zone_id||'Unknown Zone'} ↔ ${f.ground_station_id||'Unknown GS'} · ${f.direction||'communication'} · ${f.episode_id}`;flowSelect.append(o)}
seek.max=SPATIAL.end_seconds;
function latest(rows,at){let l=0,r=rows.length;while(l<r){const m=(l+r)>>1;if(rows[m].time<=at)l=m+1;else r=m}return l?rows[l-1]:null}
function lastRecord(indices){let found=null;for(const i of indices||[]){const t=SPATIAL.record_times[i];if(t!=null&&t<=time)found=i}return found}
function loadFrame(index){
 const token=++serial;requested=index;
 if(index==null){frame=null;globe.setFrame({timestamp:'runtime spatial state unavailable',positions:[]});draw();return}
 if(frame?.index===index){draw();return}
 const s=document.createElement('script');s.src=`data/frame-${String(index).padStart(3,'0')}.js`;
 s.onload=()=>{s.remove();if(token!==serial)return;const value=window.SKYSEED_NATIVE_FRAME;if(value?.index!==index){document.getElementById('traffic-geometry-basis').textContent='Spatial chunk identity mismatch';return}frame=value;globe.setFrame(frame);draw()};
 s.onerror=()=>{s.remove();if(token===serial){frame=null;requested=undefined;globe.setFrame({timestamp:'spatial data unavailable',positions:[]});document.getElementById('traffic-geometry-basis').textContent='Spatial data unavailable'}};draw();document.body.append(s);
}
function draw(){
 const progress=latest(SPATIAL.progression,time),usable=frame&&frame.index===requested;
 const service=usable?frame.services.find(s=>s[0]===(flow?.network_scope||'')&&s[1]===flow?.zone_id):null;
 const serviceNode=service?.[2],ground=flow?.ground_node;
 const launchIndex=lastRecord(flow?.launches),launch=launchIndex==null?null:DATA.records[launchIndex];
 const changeIndex=lastRecord(flow?.changes),change=changeIndex==null?null:DATA.records[changeIndex];
 const replyIndex=lastRecord(flow?.replies),reply=replyIndex==null?null:DATA.records[replyIndex];
 const source=launch?byNode.get(launch.physical_source_node_id):null;
 const overlays=[];
 if(usable&&document.getElementById('traffic-links').checked)for(const [a,b]of frame.connections)overlays.push({a,b,color:'#738ca544',width:.6});
 if(usable&&ground!=null&&serviceNode!=null)overlays.push({a:flow.direction==='zone-to-ground-station'?serviceNode:ground,b:flow.direction==='zone-to-ground-station'?ground:serviceNode,color:'#c59bff',dash:[6,5],width:2});
 const fresh=replyIndex!=null&&time-SPATIAL.record_times[replyIndex]<1;
 // Find the launch of this exact segment, so a delayed reply never flashes a migrated source.
 const replyLaunch=reply?flow.launches.map(i=>DATA.records[i]).find(r=>r.segment_index===reply.segment_index):null;
 const highlight=usable&&fresh&&reply.observation==='reply'?byNode.get(replyLaunch?.physical_source_node_id):null;
 globe.setOverlay(overlays,highlight);
 canvas.dataset.sourceNode=launch?.physical_source_node_id||'';canvas.dataset.serviceNode=serviceNode==null?'':SPATIAL.nodes[serviceNode].node_id;canvas.dataset.held=String(!!progress?.held);
 document.getElementById('traffic-play-time').textContent=time.toFixed(2)+(SPATIAL.runtime_time_available?' runtime s':' planned frame');
 document.getElementById('traffic-geometry-basis').textContent=SPATIAL.basis+' · '+(usable?frame.timestamp:'context unavailable')+(progress?.held?' · RUNTIME HOLD: spatial state remains fixed':'')+(usable?` · ${frame.connections.length} planned connections`:'');
 document.getElementById('traffic-selection-status').textContent=`Zone ${flow?.zone_id||'unknown'} · service satellite: ${serviceNode==null?'unserved / unavailable':SPATIAL.nodes[serviceNode].node_id} · latest recorded source: ${launch?.physical_source_node_id||'none yet'} · sender state: ${change?.record_type||'not launched'}${change?.reason?' ('+change.reason+')':''}. Transit path unknown.`;
 document.getElementById('traffic-evidence').textContent=JSON.stringify({sender_change:change,latest_observation:reply},null,2);
 const totals=DATA.episodes.find(e=>e.episode_id===flow?.episode_id);
 document.getElementById('traffic-flow-summary').textContent=totals?`Completed segments: ${totals.sent??'unavailable'} sent / ${totals.received??'unavailable'} replies / ${totals.loss??'unavailable'} loss · ${totals.source_nodes.length} recorded source node(s). These are whole-run totals, not events at the selected time.`:'No completed sender totals for this planned communication.';
}
function seekNative(value){time=Math.max(0,Math.min(SPATIAL.end_seconds,value));seek.value=String(time);const index=SPATIAL.runtime_time_available?latest(SPATIAL.progression,time)?.frame??null:Math.floor(time);if(requested!==index||serial===0)loadFrame(index);else draw()}
function pauseNative(){if(timer)clearInterval(timer);timer=null;document.getElementById('traffic-play').textContent='Play'}
flowSelect.onchange=()=>{flow=SPATIAL.flows.find(f=>f.episode_id===flowSelect.value);draw()};
seek.oninput=()=>{pauseNative();seekNative(Number(seek.value))};
document.getElementById('traffic-play').onclick=()=>{if(timer){pauseNative();return}if(time>=SPATIAL.end_seconds)seekNative(0);last=performance.now();document.getElementById('traffic-play').textContent='Pause';timer=setInterval(()=>{const now=performance.now();seekNative(time+(now-last)/1000*Number(document.getElementById('traffic-speed').value));last=now;if(time>=SPATIAL.end_seconds)pauseNative()},50)};
document.getElementById('traffic-focus').onclick=()=>globe.focus(flow?.ground_node);document.getElementById('traffic-reset-globe').onclick=()=>globe.reset();document.getElementById('traffic-links').onchange=draw;
seekNative(0);
'''
