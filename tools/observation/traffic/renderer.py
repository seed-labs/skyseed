"""Read-only communication selection, globe, timeline and raw recorded events."""
from tools.observation._presentation.html import standalone_page, json_for_script
from tools.observation._presentation.spatial_display import _evidence_globe_script


def render_traffic_replay_page(data):
    body = '''<p>Choose a communication, play or seek, and inspect recorded events. Playback is read-only.</p>
<p id="evidence-limit"></p><label>Communication <select id="traffic-flow"></select></label>
<div style="display:flex;align-items:center;gap:10px"><button id="traffic-play">Play</button> <input style="flex:1;min-width:160px" id="traffic-seek" aria-label="Replay time in seconds" type="range" min="0" value="0" step="0.01"> <output id="traffic-time"></output> <button id="traffic-reset-globe">Reset globe</button></div>
<canvas id="traffic-globe" style="width:100%;height:520px;background:#06111e;touch-action:none"></canvas>
<p id="traffic-status"></p><p>Arrows show recorded ingress arrivals only. Missing intermediate hops stay missing. Endpoint relationships do not form paths.</p>
<label>Recorded event <select id="traffic-event"></select></label><pre id="traffic-detail" style="white-space:pre-wrap"></pre>'''
    script = _evidence_globe_script() + 'const DATA=' + json_for_script(data) + ';\n' + r'''
const flow=document.getElementById('traffic-flow'),seek=document.getElementById('traffic-seek'),button=document.getElementById('traffic-play'),eventSelect=document.getElementById('traffic-event');
for(const c of DATA.communications){const o=document.createElement('option');o.value=c.communication_id;o.textContent=[c.ground_station_id,c.direction,c.zone_id].filter(Boolean).join(' ')||c.communication_id;flow.append(o)}
document.getElementById('evidence-limit').textContent=DATA.evidence_limit;
const globe=createEvidenceGlobe(document.getElementById('traffic-globe'),DATA.nodes,{edgeVisible:()=>true,nodeVisible:()=>true,color:()=> '#74c9ee'});
let events=[],timer=null,last=0;const end=DATA.events.reduce((end,e)=>Math.max(end,e.seconds||0),0);seek.max=end;
function draw(selected){let t=Number(seek.value),eligible=events.filter(e=>e.seconds!=null&&e.seconds<=t),e=selected||eligible.at(-1);document.getElementById('traffic-time').textContent=t.toFixed(2)+' s';
let timestamp=e&&(e.published_active_frontier||e.simulation_timestamp),frame=timestamp?DATA.frames.filter(f=>f.timestamp<=timestamp).at(-1):DATA.frames[0];if(frame)globe.setFrame(frame);
let edges=[],node=null;for(const h of (e?.hops||[])){node=h.node;if(h.arrival_supported)edges.push({a:h.ingress_peer,b:h.node,color:'#63e6ef',width:3})}globe.setOverlay(edges,node);
document.getElementById('traffic-status').textContent=e?((e.observation||e.record_type)+' · '+((e.hops||[]).some(h=>h.gap_before||h.gap_after)?'Evidence gap; see event':'Recorded event')):'No observation at this time';
document.getElementById('traffic-detail').textContent=e?JSON.stringify(e,null,2):'No recorded event';if(e)eventSelect.value=String(events.indexOf(e));}
function choose(){events=DATA.events.filter(e=>e.communication_id===flow.value);eventSelect.replaceChildren();events.forEach((e,i)=>{let o=document.createElement('option');o.value=i;o.textContent=(e.seconds?.toFixed(2)??'unknown')+' s · '+(e.observation||e.record_type);eventSelect.append(o)});draw()}
function pause(){if(timer)cancelAnimationFrame(timer);timer=null;button.textContent='Play'}
function tick(now){seek.value=Math.min(end,Number(seek.value)+(now-last)/1000);last=now;draw();if(Number(seek.value)>=end){pause();return}timer=requestAnimationFrame(tick)}
button.onclick=()=>{if(timer){pause();return}if(Number(seek.value)>=end)seek.value=0;last=performance.now();button.textContent='Pause';timer=requestAnimationFrame(tick)};
seek.oninput=()=>draw();flow.onchange=choose;eventSelect.onchange=()=>{const e=events[Number(eventSelect.value)];if(e?.seconds!=null)seek.value=e.seconds;draw(e);document.getElementById('traffic-detail').textContent=JSON.stringify(e,null,2)};
document.getElementById('traffic-reset-globe').onclick=()=>globe.reset();choose();
'''
    return standalone_page(title='Traffic observations', eyebrow='Recorded communication', body=body+'<script>'+script+'</script>')
