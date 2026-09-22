"""Render the offline Performance inspection page."""

from __future__ import annotations

from html import escape
from typing import Any

from tools.observation._presentation.html import json_for_script, standalone_page


def render_performance_page(data: dict[str, Any]) -> str:
    """Render pipeline, duration, work, resource, and raw invocation views."""

    inventory = data["inventory"]
    cards = "".join(
        _stat_card(row["component"], row["observations"])
        for row in data["components"]
    )
    pipeline_rows = "".join(
        "<tr>"
        f"<td>{escape(str(row['label']))}</td>"
        f"<td>{row['observations']}</td>"
        f"<td>{escape(_stage_refs(row['stages']))}</td>"
        "</tr>"
        for row in data["pipeline_groups"]
    )
    stage_rows = "".join(
        _stage_row(component["component"], stage)
        for component in data["components"]
        for stage in component["stages"]
    )
    top_rows = "".join(
        _stage_row(row["component"], row)
        for row in data["top_level_timing_series"]
    )
    ground_station_rows = "".join(
        _stage_row(row["component"], row)
        for row in data["ground_station_timing"]
    )
    timeline = data["timeline"]
    body = f"""
<style>
.scroll {{ overflow:auto; max-height:520px; }}
.role {{ display:inline-block;padding:2px 7px;border-radius:99px;border:1px solid var(--line); }}
.role.rollup {{ color:var(--cyan);border-color:var(--cyan); }}
.role.sample {{ color:var(--warn);border-color:var(--warn); }}
.numeric {{ text-align:right;font-variant-numeric:tabular-nums; }}
.toolbar {{ display:flex;gap:9px;flex-wrap:wrap;align-items:center;margin:10px 0; }}
#raw-records td {{ vertical-align:top; }}
.outer-grid {{ display:grid;grid-template-columns:minmax(0,.75fr) minmax(0,1.25fr);gap:14px; }}
#outer-chart {{ width:100%;min-height:260px;border:1px solid var(--line);cursor:crosshair; }}
#execution-timeline {{ width:100%;min-height:320px;border:1px solid var(--line);cursor:crosshair; }}
.timeline-controls {{ display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0; }}
.timeline-focus {{ padding:10px;border:1px solid var(--line);border-radius:8px;background:#0a1929; }}
.timeline-legend {{ display:flex;gap:14px;flex-wrap:wrap;font-size:13px; }}
.timeline-legend span::before {{ content:"";display:inline-block;width:16px;border-top:5px solid currentColor;margin-right:5px;vertical-align:middle; }}
.timeline-stage {{ color:#5ee5e5; }} .timeline-rollup {{ color:#ffd166; }} .timeline-sample {{ color:#9bb5c9; }}
@media(max-width:820px) {{ .outer-grid {{ grid-template-columns:1fr; }} }}
</style>
<section class="card">
  <span class="status OBSERVED">OBSERVED</span>
  <h2>Detached execution evidence</h2>
  <p>This Tool reads records emitted by completed execution. It does not run physical projection, planning, compilation, production, runtime, or traffic work.</p>
  <p class="warning">Rollups overlap their child stages and are never added to them. Samples carry resource evidence with zero duration. Every raw row retains its invocation owner, scope, phase, duration role, work, and resources.</p>
  <p>Recorded resource and accounting facts include RSS, input, output, wall duration, and any component-specific numeric fields emitted by the owner.</p>
  <details><summary>Advanced run metadata</summary><pre>{escape(str(data['metadata']))}</pre></details>
</section>
<section><h2>Run summary</h2><div class="grid stats">
  {_stat_card('records', inventory['observations'])}
  {_stat_card('components', inventory['components'])}
  {_stat_card('stages', inventory['stages'])}
  {_stat_card('bootstrap', inventory['bootstrap_observations'])}
  {_stat_card('steady', inventory['steady_observations'])}
  {_stat_card('one-shot', inventory['one_shot_observations'])}
  {cards}
</div></section>
<section class="card"><h2>Formal progression</h2>
  <div class="grid stats">
    {_stat_card('formal transitions', timeline['formal_transition_unit_count'])}
    {_stat_card('PhysicalPositionSnapshots', timeline['physical_frame_unit_count'])}
    {_stat_card('prehistory PhysicalPositionSnapshots', timeline['prehistory_physical_frame_unit_count'])}
    {_stat_card('prediction / other PhysicalPositionSnapshots', timeline['additional_physical_query_unit_count'])}
    {_stat_card('Plans', timeline['plan_publication_unit_count'])}
    {_stat_card('compiled realizations', timeline['compiler_realization_unit_count'])}
    {_stat_card('producer invocations', timeline['producer_invocation_unit_count'])}
  </div>
  <p>{escape(str(timeline['x_axis_semantics']))}</p>
  <pre>start={escape(str(timeline['simulation_start_timestamp']))}\nend={escape(str(timeline['simulation_end_timestamp']))}\nformal_interval_seconds={escape(str(timeline['formal_interval_seconds']))}</pre>
</section>
<section class="card"><h2>Measured execution timeline</h2>
  <p>Each source has its own recorder-relative monotonic start/end evidence. Unrelated origins are never aligned. Expand a component lane to inspect its children; no sequence-number timing is inferred.</p>
  <label>Evidence source <select id="timeline-source"></select></label>
  <div id="execution-stats" class="grid stats"></div>
  <div class="timeline-legend"><span class="timeline-rollup">outer phase / component rollup</span><span class="timeline-stage">internal stage</span><span class="timeline-sample">sample</span></div>
  <div class="timeline-controls"><button id="timeline-reset" type="button">Reset full run</button><button id="timeline-focus-selected" type="button">Focus selected span</button><strong id="timeline-viewport"></strong><span class="muted">wheel: cursor zoom · drag: horizontal pan</span></div>
  <canvas id="execution-timeline" width="1200" height="420"></canvas>
  <div id="timeline-focus" class="timeline-focus"><p class="muted">Select a bar for its exact owner, stage, role, interval, scope, work, and resources.</p></div>
  <details><summary>Advanced concurrency diagnostics</summary><div id="raw-span-stats" class="grid stats"></div><p class="muted">Raw span overlap includes nested parent/child rollups and must not be interpreted as cross-component pipeline parallelism.</p></details>
</section>
<details class="card"><summary><strong>Advanced timing statistics</strong></summary><h2>Actual pipeline groups</h2>
  <p>Zero observations means the component did not emit that stage in this run; it is not inferred from adjacent work.</p>
  <div class="scroll"><table><thead><tr><th>Group</th><th>Rows</th><th>Observed component/stage owners</th></tr></thead><tbody>{pipeline_rows}</tbody></table></div>
</details>
<details class="card"><summary><strong>Independent top-level timing series</strong></summary><h2>Independent top-level timing series</h2>
  <p>Only explicitly recorded top-level rollups appear here.</p>
  <div class="scroll"><table><thead>{_stage_header()}</thead><tbody>{top_rows or '<tr><td colspan="14">No top-level timing rollups recorded.</td></tr>'}</tbody></table></div>
</details>
<details class="card"><summary><strong>GroundStation statistics</strong></summary><h2>GroundStation timing decomposition</h2>
  <p>Selection/ranking, validation, evidence projection, and the planning rollup are shown independently. The rollup overlaps its child stages and is not added to them.</p>
  <div class="scroll"><table><thead>{_stage_header()}</thead><tbody>{ground_station_rows or '<tr><td colspan="14">No GroundStation timing stages recorded.</td></tr>'}</tbody></table></div>
</details>
<script src="../orchestration-timing.js"></script>
<details class="card"><summary><strong>Advanced orchestration timing</strong></summary><h2>End-to-end example orchestration</h2>
  <p>Each phase is positioned from its measured run-relative monotonic start and end on one shared wall-time axis. Per-Tool worker rows are non-additive when they overlap the detached batch.</p>
  <canvas id="outer-chart" width="1200" height="300"></canvas>
  <p id="outer-focus" class="muted">Select a phase for its exact interval and accounting role.</p>
  <div class="outer-grid"><div id="outer-summary" class="grid stats"></div><div class="scroll"><table><thead><tr><th>Phase</th><th>Category</th><th>Interval</th><th>Start–end</th><th>Wall</th><th>Additive</th></tr></thead><tbody id="outer-rows"><tr><td colspan="6">No orchestration timing artifact.</td></tr></tbody></table></div></div>
</details>
<details class="card"><summary><strong>Stage/work/resource statistics</strong></summary><h2>Stage timing, work, and resources</h2>
  <div class="scroll"><table><thead>{_stage_header()}</thead><tbody>{stage_rows}</tbody></table></div>
  <details><summary>Recorded measurement names</summary><pre>work={escape(str(data['work_measurement_names']))}\nresources={escape(str(data['resource_measurement_names']))}</pre></details>
</details>
<details class="card" id="raw-invocation-evidence"><summary><strong>Raw invocation evidence</strong></summary><h2>Raw invocation records</h2>
  <p>The browser loads the exact detached rows from <code>performance-records.js</code>; summary data does not replace raw evidence.</p>
  <div class="toolbar"><label>Component <select id="component-filter"><option value="">all</option></select></label><label>Duration role <select id="role-filter"><option value="">all</option><option value="stage">stage</option><option value="rollup">rollup</option><option value="sample">sample</option></select></label><span id="raw-count"></span></div>
  <div class="scroll"><table id="raw-records"><thead><tr><th>Source / #</th><th>Owner</th><th>Stage</th><th>Phase / role</th><th>Duration</th><th>Scope</th><th>Work</th><th>Resources</th></tr></thead><tbody></tbody></table></div>
</details>
<script>window.SKYSEED_PAGE_ERRORS=[];window.addEventListener("error",event=>window.SKYSEED_PAGE_ERRORS.push({{message:event.message,line:event.lineno,column:event.colno}}));</script>
"""
    script = _script(data)
    return standalone_page(
        title="Performance",
        eyebrow="Detached execution records",
        body=body,
        script=script,
    )


def _stat_card(label: object, value: object) -> str:
    return (
        '<div class="card stat"><span class="muted">'
        f"{escape(str(label))}</span><strong>{escape(str(value))}</strong></div>"
    )


def _stage_refs(values: object) -> str:
    assert isinstance(values, list)
    return ", ".join(
        f"{value['component']} / {value['stage']}" for value in values
    ) or "not observed"


def _stage_header() -> str:
    return """<tr><th>Component</th><th>Stage</th><th>Role</th><th>Rows</th>
<th>Bootstrap</th><th>Steady p50</th><th>Steady p95</th><th>Steady max</th>
<th>One-shot p50</th><th>One-shot p95</th><th>One-shot max</th><th>Total</th><th>Work totals</th><th>Resource maxima</th></tr>"""


def _stage_row(component: object, row: dict[str, Any]) -> str:
    return f"""<tr><td>{escape(str(component))}</td><td>{escape(str(row['label']))}</td>
<td><span class="role {escape(str(row['duration_role']))}">{escape(str(row['duration_role']))}</span></td>
<td class="numeric">{row['observations']}</td>
<td class="numeric">{_duration(row['bootstrap_run_seconds'])}</td>
<td class="numeric">{_duration(row['steady_p50_seconds'])}</td>
<td class="numeric">{_duration(row['steady_p95_seconds'])}</td>
<td class="numeric">{_duration(row['steady_max_seconds'])}</td>
<td class="numeric">{_duration(row['one_shot_p50_seconds'])}</td>
<td class="numeric">{_duration(row['one_shot_p95_seconds'])}</td>
<td class="numeric">{_duration(row['one_shot_max_seconds'])}</td>
<td class="numeric">{_duration(row['run_seconds'])}</td>
<td><code>{escape(str(row['work_totals']))}</code></td>
<td><code>{escape(str(row['resource_maxima']))}</code></td></tr>"""


def _duration(value: object) -> str:
    return "—" if value is None else f"{float(value):.6g} s"


def _script(data: dict[str, Any]) -> str:
    summary = {
        "components": [row["component"] for row in data["components"]],
        "execution_timeline": data["execution_timeline"],
        "execution_timelines": data["execution_timelines"],
    }
    return f"""
const OBSERVATION_SUMMARY={json_for_script(summary)},expandedComponents=new Set();let timelineView=null,selectedTimeline=null,timelineDrag=null,timelineHits=[];
const sourceFilter=document.getElementById("timeline-source");
for(const [index,source] of OBSERVATION_SUMMARY.execution_timelines.entries()){{const option=document.createElement("option");option.value=String(index);option.textContent=source.source_id;sourceFilter.append(option)}}
if(OBSERVATION_SUMMARY.execution_timelines.length)OBSERVATION_SUMMARY.execution_timeline=OBSERVATION_SUMMARY.execution_timelines[0];
sourceFilter.onchange=()=>{{OBSERVATION_SUMMARY.execution_timeline=OBSERVATION_SUMMARY.execution_timelines[Number(sourceFilter.value)];timelineView=null;selectedTimeline=null;expandedComponents.clear();renderExecutionTimeline()}};
const componentFilter=document.getElementById("component-filter");
for(const value of OBSERVATION_SUMMARY.components){{
  const option=document.createElement("option");option.value=value;option.textContent=value;componentFilter.append(option);
}}
const roleFilter=document.getElementById("role-filter");
const escaped=value=>{{const node=document.createElement("span");node.textContent=String(value);return node.innerHTML;}};
function renderRaw(){{
  const source=window.__SKYSEED_EXECUTION_OBSERVATIONS__;
  if(!source)return;
  const rows=source.records.filter(record=>(!componentFilter.value||record.component===componentFilter.value)&&(!roleFilter.value||(record.duration_role||"stage")===roleFilter.value));
  document.getElementById("raw-count").textContent=`${{rows.length}} invocation rows`;
  document.querySelector("#raw-records tbody").innerHTML=rows.map(record=>`<tr><td>${{escaped(record.source_id)}} / ${{record.sequence}}</td><td>${{escaped(record.component)}}</td><td>${{escaped(record.stage)}}</td><td>${{escaped(record.phase)}} / ${{escaped(record.duration_role||"stage")}}</td><td>${{Number(record.duration_seconds).toPrecision(6)}} s</td><td><code>${{escaped(JSON.stringify(record.scope))}}</code></td><td><code>${{escaped(JSON.stringify(record.work))}}</code></td><td><code>${{escaped(JSON.stringify(record.resources))}}</code></td></tr>`).join("");
}}
function renderOuter(){{
  const value=window.SKYSEED_ORCHESTRATION_TIMING;if(!value)return;
  const summary=document.getElementById("outer-summary");summary.innerHTML=`<div class="card stat"><span class="muted">total wall</span><strong>${{Number(value.total_wall_seconds).toFixed(3)}} s</strong></div><div class="card stat"><span class="muted">unattributed residual</span><strong>${{Number(value.unattributed_residual_seconds).toFixed(3)}} s</strong></div>`;
  const rows=value.records.filter(row=>Number.isFinite(Number(row.run_relative_start_seconds))&&Number.isFinite(Number(row.run_relative_end_seconds))),records=rows.filter(row=>(row.interval_kind||"span")!=="aggregate");
  document.getElementById("outer-rows").innerHTML=rows.map(row=>`<tr><td>${{escaped(row.phase)}}</td><td>${{escaped(row.category)}}</td><td>${{escaped(row.interval_kind||"span")}}</td><td>${{(row.interval_kind||"span")==="aggregate"?"interleaved aggregate":`${{Number(row.run_relative_start_seconds).toFixed(3)}}–${{Number(row.run_relative_end_seconds).toFixed(3)}} s`}}</td><td>${{Number(row.wall_seconds).toFixed(6)}} s</td><td>${{row.additive?"yes":"overlap"}}</td></tr>`).join("");
  const canvas=document.getElementById("outer-chart"),ctx=canvas.getContext("2d"),box=canvas.getBoundingClientRect(),dpr=Math.max(1,window.devicePixelRatio||1),left=218,right=20,top=34,rowHeight=27,width=Math.max(720,box.width),height=Math.max(190,top+records.length*rowHeight+38),end=Math.max(Number(value.total_wall_seconds)||0,...records.map(row=>Number(row.run_relative_end_seconds))),span=Math.max(.000001,end);canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);canvas.style.height=`${{height}}px`;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);ctx.font="12px system-ui";ctx.fillStyle="#9bb5c9";ctx.fillText("run-relative monotonic wall time",left,17);for(let index=0;index<=6;index++){{const x=left+(width-left-right)*index/6;ctx.strokeStyle="#294966";ctx.beginPath();ctx.moveTo(x,top-8);ctx.lineTo(x,height-24);ctx.stroke();ctx.fillStyle="#9bb5c9";ctx.fillText(`${{(span*index/6).toFixed(1)}} s`,x-18,height-7)}}const colors={{"semantic-core":"#5ee5e5","inspection-evidence-capture":"#69a9ff","domain-evidence-persistence":"#7ab8ff","detached-inspection":"#ffd166","runtime-packaging":"#62d894","sealing-finalization":"#d798ff"}},hits=[];records.forEach((record,index)=>{{const y=top+index*rowHeight,start=Number(record.run_relative_start_seconds),finish=Number(record.run_relative_end_seconds),x1=left+(width-left-right)*start/span,x2=left+(width-left-right)*finish/span,bar=Math.max(2,x2-x1);ctx.fillStyle="#edf6ff";ctx.fillText(record.phase.length>31?record.phase.slice(0,29)+"…":record.phase,6,y+15);ctx.globalAlpha=record.additive?.9:.5;ctx.fillStyle=colors[record.category]||"#9bb5c9";ctx.fillRect(x1,y+3,bar,13);ctx.globalAlpha=1;hits.push({{x1,x2:x1+bar,y1:y+2,y2:y+18,record}})}});canvas.dataset.phaseCount=String(records.length);canvas.dataset.aggregateCount=String(rows.length-records.length);canvas.onclick=event=>{{const box=canvas.getBoundingClientRect(),x=event.clientX-box.left,y=event.clientY-box.top,hit=[...hits].reverse().find(value=>x>=value.x1&&x<=value.x2&&y>=value.y1&&y<=value.y2),focus=document.getElementById("outer-focus");focus.textContent=hit?`${{hit.record.phase}} · ${{hit.record.category}} · ${{Number(hit.record.run_relative_start_seconds).toFixed(6)}}–${{Number(hit.record.run_relative_end_seconds).toFixed(6)}} s · ${{Number(hit.record.wall_seconds).toFixed(6)}} s · ${{hit.record.additive?"additive":"overlapping/non-additive"}}`:"No phase at this point.";}};
}}
function orchestrationSpans(){{return []}}
function timelineBounds(value){{const outer=orchestrationSpans(),starts=[value.start_seconds,...outer.map(v=>v.start_seconds)],ends=[value.end_seconds,...outer.map(v=>v.end_seconds)];return{{start:Math.min(...starts),end:Math.max(...ends)}}}}
function timelineSelectionHtml(hit){{const v=hit.value,owner=hit.outer?"outer orchestration":hit.lane;return'<h3>Selected exact span</h3>'+[["owner / component",owner],["stage / phase",`${{v.stage}} / ${{v.phase}}`],["role",v.duration_role],["start",`${{Number(v.start_seconds).toFixed(6)}} s`],["end",`${{Number(v.end_seconds).toFixed(6)}} s`],["duration",`${{Number(v.duration_seconds).toFixed(6)}} s`],["scope",`<code>${{escaped(JSON.stringify(v.scope||{{}}))}}</code>`],["work",`<code>${{escaped(JSON.stringify(v.work||{{}}))}}</code>`],["resources",`<code>${{escaped(JSON.stringify(v.resources||{{}}))}}</code>`]].map(row=>`<div class="kv"><dt>${{row[0]}}</dt><dd>${{row[1]}}</dd></div>`).join("")}}
function selectTimelineHit(hit){{if(!hit)return;selectedTimeline=hit;document.getElementById("timeline-focus").innerHTML=timelineSelectionHtml(hit);if(hit.level==="component"&&!expandedComponents.has(hit.lane))expandedComponents.add(hit.lane);renderExecutionTimeline()}}
function clampTimelineView(bounds){{const full=Math.max(.000001,bounds.end-bounds.start),minimum=Math.max(.000001,full/2000);if(!timelineView)timelineView={{start:bounds.start,end:bounds.end}};let span=Math.max(minimum,Math.min(full,timelineView.end-timelineView.start));let start=Math.max(bounds.start,Math.min(bounds.end-span,timelineView.start));timelineView={{start,end:start+span}}}}
function renderExecutionTimeline(){{
  const value=OBSERVATION_SUMMARY.execution_timeline,canvas=document.getElementById("execution-timeline"),stats=document.getElementById("execution-stats");
  canvas.hidden=false;
  if(!value.available){{stats.innerHTML=`<p class="muted">${{escaped(value.reason)}}</p>`;canvas.hidden=true;return;}}
  const s=value.statistics,componentWall=Object.entries(s.component_active_wall_seconds||{{}}).map(([component,seconds])=>`${{component}}: ${{Number(seconds).toFixed(3)}} s`).join(" · ");
  stats.innerHTML=[["recorded busy union",`${{Number(s.busy_union_seconds).toFixed(3)}} s`],["wall with ≥2 distinct components",`${{Number(s.cross_component_overlap_seconds).toFixed(3)}} s`],["cross-component overlap ratio",`${{(100*Number(s.cross_component_overlap_ratio)).toFixed(2)}}%`],["max active distinct components",s.maximum_active_distinct_components],["recorded pipeline gaps",`${{Number(s.idle_gap_seconds).toFixed(3)}} s`],["component active wall",componentWall||"none"]].map(v=>`<div class="card stat"><span class="muted">${{v[0]}}</span><strong>${{v[1]}}</strong></div>`).join("");
  document.getElementById("raw-span-stats").innerHTML=[["recorded spans",s.recorded_span_count],["raw span overlap including nested rollups",`${{Number(s.overlap_seconds).toFixed(3)}} s`],["raw nested overlap ratio",`${{(100*Number(s.overlap_ratio)).toFixed(2)}}%`],["raw maximum span concurrency",s.maximum_concurrency],["summed span duration",`${{Number(s.summed_duration_seconds).toFixed(3)}} s`]].map(v=>`<div class="card stat"><span class="muted">${{v[0]}}</span><strong>${{v[1]}}</strong></div>`).join("");
  const bounds=timelineBounds(value);clampTimelineView(bounds);const rows=[],outer=orchestrationSpans();if(outer.length)rows.push({{component:"outer orchestration",label:"◆ outer orchestration phases",spans:outer,level:"outer",outer:true}});for(const lane of value.lanes){{const rollups=lane.spans.filter(span=>span.duration_role==="rollup");rows.push({{component:lane.component,label:`${{expandedComponents.has(lane.component)?"▾":"▸"}} ${{lane.component}}`,spans:rollups.length?rollups:lane.spans,level:"component"}});if(expandedComponents.has(lane.component)){{const stages=new Map();for(const span of lane.spans){{const key=`${{span.stage}} · ${{span.duration_role}}`;if(!stages.has(key))stages.set(key,[]);stages.get(key).push(span)}}for(const[label,spans]of stages)rows.push({{component:lane.component,label:`↳ ${{label}}`,spans,level:"internal"}})}}}}
  const box=canvas.getBoundingClientRect(),dpr=Math.max(1,window.devicePixelRatio||1),left=230,right=18,top=34,row=28,width=Math.max(640,box.width),height=Math.max(180,top+rows.length*row+38),span=Math.max(.000001,timelineView.end-timelineView.start),plotWidth=width-left-right;canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);canvas.style.height=`${{height}}px`;const x=canvas.getContext("2d");x.setTransform(dpr,0,0,dpr,0,0);x.clearRect(0,0,width,height);x.font="12px system-ui";x.fillStyle="#9bb5c9";x.fillText("run-relative monotonic seconds",left,17);for(let i=0;i<=6;i++){{const px=left+plotWidth*i/6,t=timelineView.start+span*i/6;x.strokeStyle="#294966";x.beginPath();x.moveTo(px,top-8);x.lineTo(px,height-24);x.stroke();x.fillStyle="#9bb5c9";x.fillText(`${{t.toFixed(span<10?3:1)}} s`,px-18,height-7)}}
  timelineHits=[];rows.forEach((lane,rowIndex)=>{{const y=top+rowIndex*row;x.fillStyle="#edf6ff";x.fillText(lane.label.length>34?lane.label.slice(0,32)+"…":lane.label,6,y+15);x.strokeStyle="#203a52";x.beginPath();x.moveTo(left,y+18);x.lineTo(width-right,y+18);x.stroke();timelineHits.push({{x1:0,x2:left-4,y1:y,y2:y+20,lane:lane.component,level:lane.level,label:true,outer:lane.outer,value:lane.spans[0]}});lane.spans.forEach(v=>{{if(v.end_seconds<timelineView.start||v.start_seconds>timelineView.end)return;const x1=left+(v.start_seconds-timelineView.start)/span*plotWidth,x2=left+(v.end_seconds-timelineView.start)/span*plotWidth,bar=Math.max(2,x2-x1),color=lane.outer?"#69a9ff":v.duration_role==="rollup"?"#ffd166":v.duration_role==="sample"?"#9bb5c9":"#5ee5e5",selected=selectedTimeline&&selectedTimeline.lane===lane.component&&selectedTimeline.value?.sequence===v.sequence;x.globalAlpha=v.duration_role==="rollup"?.55:.9;x.fillStyle=color;x.fillRect(x1,y+3,bar,12);x.globalAlpha=1;if(selected){{x.strokeStyle="#ff7474";x.lineWidth=2;x.strokeRect(x1-1,y+2,bar+2,14);x.lineWidth=1}}timelineHits.push({{x1,x2:x1+bar,y1:y+2,y2:y+17,lane:lane.component,level:lane.level,outer:lane.outer,value:v}})}})}});
  const first=timelineHits.find(v=>!v.label&&v.level==="component");canvas.dataset.laneCount=String(rows.length);canvas.dataset.componentCount=String(value.lanes.length);canvas.dataset.outerSpanCount=String(outer.length);canvas.dataset.expandedComponentCount=String(expandedComponents.size);canvas.dataset.spanCount=String(s.recorded_span_count);canvas.dataset.firstSpanX=first?String((first.x1+first.x2)/2):"";canvas.dataset.firstSpanY=first?String((first.y1+first.y2)/2):"";canvas.dataset.viewStart=String(timelineView.start);canvas.dataset.viewEnd=String(timelineView.end);canvas.dataset.selectedSequence=selectedTimeline?.value?.sequence??"";document.getElementById("timeline-viewport").textContent=`${{timelineView.start.toFixed(3)}}–${{timelineView.end.toFixed(3)}} s / full ${{bounds.start.toFixed(3)}}–${{bounds.end.toFixed(3)}} s`;
}}
function hitAt(event){{const canvas=document.getElementById("execution-timeline"),b=canvas.getBoundingClientRect(),px=event.clientX-b.left,py=event.clientY-b.top;return[...timelineHits].reverse().find(v=>px>=v.x1&&px<=v.x2&&py>=v.y1&&py<=v.y2)}}
componentFilter.addEventListener("change",renderRaw);roleFilter.addEventListener("change",renderRaw);
const recordScript=document.createElement("script");recordScript.src="performance-records.js";recordScript.onload=renderRaw;recordScript.onerror=()=>{{document.getElementById("raw-count").textContent="Unable to load raw records";}};document.body.appendChild(recordScript);
const timelineCanvas=document.getElementById("execution-timeline");timelineCanvas.onpointerdown=event=>{{timelineDrag={{x:event.clientX,start:timelineView.start,end:timelineView.end,moved:false}};timelineCanvas.setPointerCapture(event.pointerId)}};timelineCanvas.onpointermove=event=>{{if(!timelineDrag)return;const delta=event.clientX-timelineDrag.x;if(Math.abs(delta)>3)timelineDrag.moved=true;if(timelineDrag.moved){{const width=Math.max(1,timelineCanvas.getBoundingClientRect().width-248),seconds=-(delta/width)*(timelineDrag.end-timelineDrag.start);timelineView={{start:timelineDrag.start+seconds,end:timelineDrag.end+seconds}};clampTimelineView(timelineBounds(OBSERVATION_SUMMARY.execution_timeline));renderExecutionTimeline()}}}};timelineCanvas.onpointerup=event=>{{const drag=timelineDrag;timelineDrag=null;if(drag&&!drag.moved){{const hit=hitAt(event);if(!hit){{document.getElementById("timeline-focus").innerHTML='<p class="muted">No span at this point.</p>';return}}if(hit.label&&hit.level==="component"){{if(expandedComponents.has(hit.lane))expandedComponents.delete(hit.lane);else expandedComponents.add(hit.lane);renderExecutionTimeline();return}}if(!hit.label)selectTimelineHit(hit)}}}};timelineCanvas.onwheel=event=>{{event.preventDefault();const bounds=timelineBounds(OBSERVATION_SUMMARY.execution_timeline),box=timelineCanvas.getBoundingClientRect(),ratio=Math.max(0,Math.min(1,(event.clientX-box.left-230)/Math.max(1,box.width-248))),old=timelineView.end-timelineView.start,factor=Math.exp(event.deltaY*.0015),full=bounds.end-bounds.start,next=Math.max(full/2000,Math.min(full,old*factor)),anchor=timelineView.start+old*ratio;timelineView={{start:anchor-next*ratio,end:anchor+next*(1-ratio)}};clampTimelineView(bounds);renderExecutionTimeline()}};document.getElementById("timeline-reset").onclick=()=>{{const bounds=timelineBounds(OBSERVATION_SUMMARY.execution_timeline);timelineView={{start:bounds.start,end:bounds.end}};renderExecutionTimeline()}};document.getElementById("timeline-focus-selected").onclick=()=>{{if(!selectedTimeline)return;const bounds=timelineBounds(OBSERVATION_SUMMARY.execution_timeline),v=selectedTimeline.value,full=bounds.end-bounds.start,duration=Math.max(v.end_seconds-v.start_seconds,full/1000),padding=duration*.25;timelineView={{start:v.start_seconds-padding,end:v.end_seconds+padding}};clampTimelineView(bounds);renderExecutionTimeline()}};
function renderAllTimelines(){{renderOuter();renderExecutionTimeline()}}renderAllTimelines();window.addEventListener("resize",renderAllTimelines);
"""


__all__ = ["render_performance_page"]
