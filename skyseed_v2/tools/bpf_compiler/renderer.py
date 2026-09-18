"""Render BPF Compiler evidence as a compact dashboard and drill-down."""

from __future__ import annotations

from html import escape
from typing import Any

from tools._presentation.html import json_for_script, standalone_page


def render_bpf_compiler_page(data: dict[str, Any]) -> str:
    inventory = data["inventory"]
    stats = "".join(
        _stat(label, inventory[key])
        for key, label in (
            ("plans", "Plans / realizations"),
            ("activation_boundaries", "Activation boundaries"),
            ("resource_intents", "Resource intents"),
            ("parameter_intents", "Parameter intents"),
            ("forwarding_intents", "Forwarding intents"),
            ("zone_edge_intents", "Zone-edge intents"),
            ("constraints", "Constraints"),
        )
    )
    timing_note = (
        "No completed Observation timing source was supplied."
        if data["compiler_timing"] is None
        else escape(str(data["compiler_timing"]["note"]))
    )
    deliveries = "".join(
        f'<tr><td>{value["index"]}</td><td>{escape(value["timeline"]["start"])}</td>'
        f'<td>{escape(value["timeline"]["end"])}</td><td>{value["timeline"]["boundary_count"]}</td>'
        f'<td><button data-delivery-start="{escape(value["timeline"]["start"])}">Focus first boundary</button></td></tr>'
        for value in data.get("delivery_batches", ())
    )
    body = f"""
<style>
.dashboard{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}} .chart{{min-height:270px}} .chart canvas{{height:220px;border:1px solid var(--line)}}
.focus{{display:grid;grid-template-columns:minmax(0,1fr) minmax(320px,.65fr);gap:14px}} .toolbar{{display:flex;gap:9px;align-items:center;flex-wrap:wrap}} .toolbar input[type=range]{{flex:1;min-width:180px}}
.kv{{display:grid;grid-template-columns:max-content 1fr;gap:5px 10px}} .kv dt{{color:var(--muted)}} .kv dd{{margin:0;overflow-wrap:anywhere}} .chips{{display:flex;gap:7px;flex-wrap:wrap}} .chip{{padding:5px 8px;border:1px solid var(--line);border-radius:99px}}
#compiler-timeline{{width:100%;height:330px;border:1px solid var(--line);cursor:crosshair}} .timeline-controls{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}} .timeline-controls input{{flex:1;min-width:260px}} .legend{{display:flex;gap:14px;flex-wrap:wrap;font-size:13px}} .legend span::before{{content:"";display:inline-block;width:18px;border-top:4px solid currentColor;margin-right:5px;vertical-align:middle}} .boundary-key{{color:#5ee5e5}} .realization-key{{color:#69a9ff}} .timing-key{{color:#ffd166}} .cursor-key{{color:#ff7474}}
@media(max-width:900px){{.dashboard,.focus{{grid-template-columns:1fr}}}}
</style>
<nav><a href="../index.html">← Tool Overview</a></nav>
<section class="card"><span class="status PASS">PASS</span><h2>Plan → compiled intended realization</h2><p>This detached Tool reads completed Plan and realization evidence and does not invoke Compiler. Charts expose scenario trends, backend operation deltas, semantic-precedence folding, boundary workload, and measured Compiler timing. Compiled/intended is not realized; completed ACTIVE belongs to Runner evidence.</p></section>
<section class="grid stats">{stats}</section>
<section class="card" id="delivery-batches"><h2>Sealed deliveries / 密封交付</h2><p>One continuous semantic history. Delivery boundaries preserve Planner memory, Compiler lineage and runtime identities. This retained evidence remains available after runtime reclamation.</p><table><thead><tr><th>Batch</th><th>First timestamp</th><th>Last timestamp</th><th>Boundaries</th><th>Inspect seam</th></tr></thead><tbody>{deliveries}</tbody></table></section>
<section class="card"><h2>Activation and compilation timeline</h2><p>All {inventory['activation_boundaries']} activation boundaries and {inventory['realizations']} realization coverage windows share the same formal simulation-time axis. Measured per-compile duration controls the amber marker height; it is not treated as simulation time.</p><div class="legend"><span class="boundary-key">boundary workload</span><span class="realization-key">realization window</span><span class="timing-key">measured compile seconds</span><span class="cursor-key">focused boundary</span></div><canvas id="compiler-timeline" width="1200" height="330"></canvas><div class="timeline-controls"><label>Formal boundary <input id="global-boundary" type="range" min="0" max="{max(0, inventory['activation_boundaries'] - 1)}" value="0"></label><strong id="global-time"></strong></div></section>
<section class="dashboard">
 <article class="card chart"><h2>Intent workload by realization</h2><canvas id="workload-chart" width="760" height="260"></canvas><p class="muted">Resource, parameter, forwarding, and Zone-edge intent totals.</p></article>
 <article class="card chart"><h2>Add / update / remove trend</h2><canvas id="delta-chart" width="760" height="260"></canvas><p class="muted">Exact backend verbs normalized only for this display.</p></article>
 <article class="card chart"><h2>Precedence folding trend</h2><canvas id="folding-chart" width="760" height="260"></canvas><p class="muted">Constraints versus semantic precedences across each Plan.</p></article>
 <article class="card chart"><h2>Compiler timing</h2><canvas id="timing-chart" width="760" height="260"></canvas><p>{timing_note}</p></article>
</section>
<section class="focus">
 <div class="card"><h2>Selected activation boundary</h2><div class="toolbar"><label>Jump to realization <select id="realization" aria-label="Jump to realization"></select></label><strong id="compiler-timestamp"></strong></div><p class="muted">This shortcut moves the shared formal-boundary cursor; the detail below is derived from that one cursor.</p><div id="boundary-workload"></div></div>
 <aside class="card"><h2>Lineage and intended state</h2><div id="realization-detail"></div><h3>Exact operation families</h3><div id="operation-detail" class="chips"></div><h3>Folding</h3><div id="folding-detail"></div></aside>
</section>
<section class="card"><details id="compiler-spatial-context"><summary>Spatial context · shared Physical World / Shell → Orbit controls</summary><p>This is retained physical geometry, not proof of compiled or realized passage. The Compiler boundary cursor stays linked; Shell/orbit filters change visibility only.</p><iframe id="compiler-spatial-view" title="Shared Physical World spatial inspection" style="width:100%;height:1100px;border:0" data-src="../physical-world/index.html"></iframe></details></section>
<section class="card"><h2>Evidence boundary</h2><p>{escape(str(data['interpretation']['raw_detail']))}</p><p><code>bpf-compiler-inspection.json</code> retains lineage, every activation timestamp, boundary workload, operation counts, folding evidence, cardinalities, stable Zone prefixes, and the detached Traffic contract. It does not duplicate Planner-owned exact Route objects.</p></section>
<script>window.SKYSEED_PAGE_ERRORS=[];window.addEventListener("error",event=>window.SKYSEED_PAGE_ERRORS.push({{message:event.message,line:event.lineno,column:event.colno}}));</script>
"""
    return standalone_page(
        title="BPF Compiler Inspection",
        eyebrow="Compiled workload, folding, delta, and timing dashboard",
        body=body,
        script=_script(data),
        language_strings={
            "BPF Compiler Inspection": "BPF 编译器检查",
            "Compiled workload, folding, delta, and timing dashboard": "编译工作量、折叠、增量与时间仪表板",
            "Tool Overview": "工具总览",
            "Intent workload by realization": "各实现的意图工作量",
            "Add / update / remove trend": "新增 / 更新 / 删除趋势",
            "Precedence folding trend": "优先约束折叠趋势",
            "Compiler timing": "编译器时间",
            "Activation and compilation timeline": "激活与编译时间线",
            "Selected activation boundary": "已选激活边界",
            "Lineage and intended state": "血缘与预期状态",
        },
    )


def _stat(label: str, value: object) -> str:
    return f'<div class="card stat"><span class="muted">{escape(label)}</span><strong>{escape(str(value))}</strong></div>'


def _script(data: dict[str, Any]) -> str:
    return f"""
document.querySelectorAll('[data-delivery-start]').forEach(button=>button.onclick=()=>{{const index=activation.findIndex(value=>Date.parse(value.timestamp)===Date.parse(button.dataset.deliveryStart));if(index>=0){{globalBoundary.value=String(index);focusGlobal()}}}});
const DATA={json_for_script(data)},select=document.getElementById('realization'),stamp=document.getElementById('compiler-timestamp'),detail=document.getElementById('realization-detail'),operations=document.getElementById('operation-detail'),folding=document.getElementById('folding-detail'),workload=document.getElementById('boundary-workload'),globalBoundary=document.getElementById('global-boundary'),globalTime=document.getElementById('global-time');
const colors=['#5ee5e5','#69a9ff','#ffd166','#62d894','#ff7474'];function prep(canvas){{const x=canvas.getContext('2d'),b=canvas.getBoundingClientRect(),d=Math.max(1,devicePixelRatio||1);canvas.width=Math.round(b.width*d);canvas.height=Math.round(220*d);x.setTransform(d,0,0,d,0,0);return[x,b.width,220]}}function chart(id,series,yUnit){{const c=document.getElementById(id),[x,w,h]=prep(c),n=Math.max(1,DATA.trend.length),max=Math.max(1,...series.flatMap(v=>v.values));x.clearRect(0,0,w,h);x.strokeStyle='#294966';x.beginPath();x.moveTo(48,20);x.lineTo(48,h-32);x.lineTo(w-8,h-32);x.stroke();for(let i=0;i<=4;i++){{const value=max*i/4,py=h-32-(h-58)*i/4;x.strokeStyle='#203a52';x.beginPath();x.moveTo(44,py);x.lineTo(w-8,py);x.stroke();x.fillStyle='#9bb5c9';x.font='10px system-ui';x.fillText(value>=1000?`${{(value/1000).toFixed(1)}}k`:value.toFixed(yUnit==='seconds'?3:0),3,py+3)}}const group=(w-60)/n;series.forEach((s,j)=>{{x.strokeStyle=s.color;x.lineWidth=Math.max(1,group/(series.length+2));x.beginPath();s.values.forEach((v,i)=>{{const px=52+(i+.5)*group+(j-(series.length-1)/2)*x.lineWidth,py=h-33-v/max*(h-58);if(i)x.lineTo(px,py);else x.moveTo(px,py)}});x.stroke()}});x.font='11px system-ui';series.forEach((s,i)=>{{x.fillStyle=s.color;x.fillText(s.label,52+i*Math.max(105,(w-60)/series.length),14)}});x.fillStyle='#9bb5c9';x.fillText('realization # / 实现序号',Math.max(50,w/2-70),h-8);x.fillText('#1',50,h-20);x.fillText(`#${{n}}`,w-34,h-20);c.dataset.series=series.map(v=>v.label).join(',');c.dataset.points=String(DATA.trend.length);c.dataset.xAxis='realization index';c.dataset.yAxis=yUnit;c.onclick=event=>{{const b=c.getBoundingClientRect(),i=Math.max(0,Math.min(n-1,Math.floor((event.clientX-b.left-48)/Math.max(1,b.width-60)*n))),target=activation.findIndex(v=>v.realization_index===i);if(target>=0){{globalBoundary.value=String(target);focusGlobal()}}}}}}
function foldingCounts(v){{const f=v.semantic_precedence_folding||{{}};const numeric=Object.entries(f).filter(([,x])=>typeof x==='number');return numeric.reduce((a,[,x])=>a+x,0)}}function drawCharts(){{chart('workload-chart',[{{label:'资源 resource',color:colors[0],values:DATA.trend.map(v=>v.resource_intents)}},{{label:'参数 parameter',color:colors[1],values:DATA.trend.map(v=>v.parameter_intents)}},{{label:'转发 forwarding',color:colors[2],values:DATA.trend.map(v=>v.forwarding_intents)}},{{label:'区域边 Zone edge',color:colors[3],values:DATA.trend.map(v=>v.zone_edge_intents)}}],'intent count');chart('delta-chart',[{{label:'新增 add',color:colors[3],values:DATA.trend.map(v=>v.delta_counts.add)}},{{label:'更新 update',color:colors[2],values:DATA.trend.map(v=>v.delta_counts.update)}},{{label:'删除 remove',color:colors[4],values:DATA.trend.map(v=>v.delta_counts.remove)}}],'operation count');chart('folding-chart',[{{label:'语义优先级 semantic precedence',color:colors[0],values:DATA.trend.map(v=>v.semantic_precedences)}},{{label:'约束 constraints',color:colors[2],values:DATA.trend.map(v=>v.constraints)}},{{label:'折叠证据 fold evidence',color:colors[4],values:DATA.realizations.map(foldingCounts)}}],'count');const samples=DATA.realizations.map(v=>Number(v.compile_evidence?.duration_seconds||0));chart('timing-chart',[{{label:'编译耗时 compile time',color:colors[1],values:samples.length?samples:[0]}}],'seconds')}}
function rows(values){{return'<dl class="kv">'+values.map(v=>`<dt>${{v[0]}}</dt><dd>${{v[1]??'—'}}</dd>`).join('')+'</dl>'}}for(const v of DATA.realizations){{const option=document.createElement('option');option.value=String(v.realization_index);option.textContent=`#${{v.realization_index}} · ${{v.lineage.source_plan_id}}`;select.append(option)}}function updateRealization(index){{const v=DATA.realizations[Number(index)];detail.innerHTML=rows([['source Plan',v.lineage.source_plan_id],['previous Plan',v.lineage.source_previous_plan_id||'bootstrap'],['input state fingerprint',v.lineage.input_state_fingerprint||'bootstrap'],['output state fingerprint',v.lineage.output_state_fingerprint],['intended state timestamp',v.intended_state_timestamp],['activation boundaries',v.summary.activation_boundaries],['intended connections',v.summary.intended_connections],['intended resources',v.summary.intended_resources],['reachable / unreachable Routes',`${{v.summary.planned_routes_reachable}} / ${{v.summary.planned_routes_unreachable}}`],['compile duration',v.compile_evidence?`${{Number(v.compile_evidence.duration_seconds).toFixed(6)}} s`:'not recorded']]);operations.innerHTML=Object.entries(v.operation_counts).flatMap(([family,counts])=>Object.entries(counts).map(([verb,count])=>`<span class="chip">${{family}} · ${{verb}} <strong>${{count}}</strong></span>`)).join('');folding.innerHTML=rows(Object.entries(v.semantic_precedence_folding).map(([k,x])=>[k,typeof x==='object'?JSON.stringify(x):x]))}}function updateBoundary(v){{const exact=DATA.realizations[Number(v.realization_index)].boundary_workloads[Number(v.boundary_index)];stamp.textContent=exact?.timestamp||'no boundary';workload.innerHTML=exact?rows([['global boundary index',globalBoundary.value],['owning realization',v.realization_index],['realization boundary index',v.boundary_index],['transition fingerprint',exact.transition_fingerprint],['operations',exact.operation_count],['add',exact.delta_counts.add],['update',exact.delta_counts.update],['remove',exact.delta_counts.remove],...Object.entries(exact.operations).map(([k,x])=>[k,JSON.stringify(x)])]):'<p class="muted">No activation boundary.</p>';workload.dataset.operationCount=String(exact?.operation_count||0);workload.dataset.globalBoundary=globalBoundary.value;workload.dataset.realizationIndex=String(v.realization_index);workload.dataset.boundaryIndex=String(v.boundary_index)}}
const activation=DATA.activation_timeline||DATA.realizations.flatMap(v=>v.boundary_workloads.map((b,i)=>({{realization_index:v.realization_index,boundary_index:i,...b}})));function drawTimeline(){{const c=document.getElementById('compiler-timeline'),box=c.getBoundingClientRect(),d=Math.max(1,devicePixelRatio||1),w=Math.max(700,box.width),h=330,x=c.getContext('2d'),left=70,right=20,top=28,bottom=52,times=activation.map(v=>Date.parse(v.timestamp)),min=Math.min(...times),max=Math.max(...times),span=Math.max(1,max-min),px=t=>left+(w-left-right)*(Date.parse(t)-min)/span,maxOps=Math.max(1,...activation.map(v=>v.operation_count)),durations=DATA.realizations.map(v=>Number(v.compile_evidence?.duration_seconds||0)),maxDuration=Math.max(1,...durations);c.width=Math.round(w*d);c.height=Math.round(h*d);x.setTransform(d,0,0,d,0,0);x.clearRect(0,0,w,h);x.font='11px system-ui';for(let i=0;i<=6;i++){{const q=i/6,p=left+(w-left-right)*q,t=new Date(min+span*q);x.strokeStyle='#294966';x.beginPath();x.moveTo(p,top);x.lineTo(p,h-bottom);x.stroke();x.fillStyle='#9bb5c9';x.fillText(t.toISOString().slice(11,19),p-20,h-28)}}x.fillStyle='#9bb5c9';x.fillText('formal simulation time (UTC)',left,h-8);x.fillText('ops',8,top+8);x.fillText('compile s',8,h-bottom-4);for(const v of DATA.realizations){{const a=px(v.lineage.start_timestamp),b=px(v.lineage.end_timestamp);x.fillStyle='#69a9ff55';x.fillRect(a,top+8,Math.max(2,b-a),18);const duration=Number(v.compile_evidence?.duration_seconds||0),center=(a+b)/2,height=duration/maxDuration*65;x.fillStyle='#ffd166';x.fillRect(center-2,h-bottom-height,4,height)}}activation.forEach((v,i)=>{{const p=px(v.timestamp),height=v.operation_count/maxOps*135;x.strokeStyle=i===Number(globalBoundary.value)?'#ff7474':'#5ee5e5';x.lineWidth=i===Number(globalBoundary.value)?3:1;x.beginPath();x.moveTo(p,top+32);x.lineTo(p,top+32+height);x.stroke()}});x.lineWidth=1;c.dataset.boundaryCount=String(activation.length);c.dataset.realizationCount=String(DATA.realizations.length);c.dataset.axisUnit='UTC simulation time';}}
function drawChartFocus(){{drawCharts();const current=activation[Number(globalBoundary.value)]?.realization_index??0,n=Math.max(1,DATA.trend.length);for(const c of document.querySelectorAll('.chart canvas')){{const d=Math.max(1,devicePixelRatio||1),x=c.getContext('2d'),w=c.width/d,group=(w-60)/n,px=52+(current+.5)*group;x.save();x.fillStyle='#ff747422';x.fillRect(52+current*group,20,Math.max(2,group),168);x.strokeStyle='#ff7474';x.lineWidth=2;x.beginPath();x.moveTo(px,20);x.lineTo(px,188);x.stroke();x.restore();c.dataset.focusedRealization=String(current)}}}}function syncSpatial(){{const iframe=document.getElementById('compiler-spatial-view');if(iframe.getAttribute('src'))iframe.contentWindow.postMessage({{type:'skyseed-focus-boundary',index:Number(globalBoundary.value)}},'*')}}document.getElementById('compiler-spatial-context').ontoggle=e=>{{if(!e.target.open)return;const iframe=document.getElementById('compiler-spatial-view');if(!iframe.getAttribute('src')){{iframe.onload=syncSpatial;iframe.src=iframe.dataset.src}}else syncSpatial()}};function focusGlobal(){{const v=activation[Number(globalBoundary.value)];if(!v)return;select.value=String(v.realization_index);select.dataset.globalBoundary=globalBoundary.value;updateRealization(v.realization_index);updateBoundary(v);globalTime.textContent=`${{v.timestamp}} · ${{v.operation_count}} operations`;drawTimeline();drawChartFocus();syncSpatial()}}document.getElementById('compiler-timeline').onclick=event=>{{const c=event.currentTarget,b=c.getBoundingClientRect(),ratio=Math.max(0,Math.min(1,(event.clientX-b.left-70)/Math.max(1,b.width-90))),times=activation.map(v=>Date.parse(v.timestamp)),targetTime=Math.min(...times)+(Math.max(...times)-Math.min(...times))*ratio;let target=0,distance=Infinity;times.forEach((value,index)=>{{const candidate=Math.abs(value-targetTime);if(candidate<distance){{distance=candidate;target=index}}}});globalBoundary.value=String(target);focusGlobal()}};select.onchange=()=>{{const target=activation.findIndex(v=>v.realization_index===Number(select.value));if(target>=0){{globalBoundary.value=String(target);focusGlobal()}}}};globalBoundary.oninput=focusGlobal;window.onresize=()=>{{drawTimeline();drawChartFocus()}};focusGlobal();
"""


__all__ = ["render_bpf_compiler_page"]
