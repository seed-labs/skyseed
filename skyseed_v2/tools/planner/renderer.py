"""Render final Planner inspection view data as one offline HTML page."""

from __future__ import annotations

from typing import Any

from tools._html import json_for_script, standalone_page


def render_planner_page(data: dict[str, Any]) -> str:
    """Return the primary human-review artifact without formal object reads."""

    if not isinstance(data, dict) or data.get("tool") != "planner-inspection":
        raise TypeError("data must be final Planner inspection view data")
    inventory = data["inventory"]
    eyebrow = (
        f"{inventory['satellites']} Satellites · "
        f"{inventory['ground_stations']} GroundStations · read-only"
    )
    stats = "".join(
        f'<div class="card stat"><span class="muted">{label}</span>'
        f'<strong>{inventory[key]}</strong></div>'
        for key, label in (
            ("real_slices", "Real Slices"),
            ("differences", "Differences"),
            ("plans", "Published Plans"),
            ("connection_transitions", "Connection"),
            ("parameter_transitions", "Published Parameter"),
            ("forwarding_transitions", "Forwarding"),
            ("forwarding_precedences", "Precedences"),
        )
    )
    body = f"""
<style>
.planner-note {{ border-left:4px solid var(--cyan); padding:10px 14px; background:#12334b66; }}
.tabs {{ display:flex; gap:8px; flex-wrap:wrap; margin:18px 0; position:sticky; top:0; z-index:4; padding:9px; background:#07111fe8; }}
.tab.active {{ border-color:var(--cyan); color:var(--cyan); }}
.view {{ display:none; }} .view.active {{ display:block; }}
.scroll {{ overflow-x:auto; }} .timeline {{ min-width:980px; }}
.lane {{ position:relative; height:50px; border-bottom:1px solid var(--line); margin-left:150px; }}
.lane-label {{ position:absolute; left:-150px; width:140px; padding-top:13px; color:var(--muted); }}
.tick {{ position:absolute; inset:0 auto 0 0; width:1px; background:#355672; }}
.tick span {{ position:absolute; top:2px; left:4px; font-size:11px; color:var(--muted); white-space:nowrap; }}
.plan-bar {{ position:absolute; top:8px; height:30px; border:1px solid var(--blue); background:#315f9d88; border-radius:6px; padding:4px; overflow:hidden; white-space:nowrap; }}
.planning-window-bar {{ position:absolute; top:39px; height:5px; background:#ffd16688; }}
.marker {{ position:absolute; top:9px; transform:translateX(-50%); min-width:27px; padding:3px 5px; border:0; border-radius:99px; text-align:center; font-size:11px; font-weight:800; color:#07111f; cursor:pointer; }}
.marker.connection {{ background:var(--cyan); }} .marker.parameter {{ background:var(--warn); }} .marker.forwarding {{ background:var(--pass); }}
.split {{ display:grid; grid-template-columns:minmax(250px,.72fr) minmax(0,2fr); gap:14px; }}
.listbox {{ width:100%; min-height:320px; }}
.occurrence {{ border-left:5px solid var(--cyan); padding:8px 12px; margin:8px 0; background:#102a4366; overflow-wrap:anywhere; }}
.formal {{ border-color:var(--blue); }} .planned {{ border-color:var(--pass); }}
.precedence {{ border-left:4px solid #e879f9; padding:9px 12px; margin:8px 0; background:#351d4966; overflow-wrap:anywhere; }}
.precedence-arrow {{ text-align:center; color:#e879f9; font-size:24px; line-height:1; }}
.dependency-chain {{ display:grid; grid-template-columns:minmax(0,1fr) 46px minmax(0,1fr) 46px minmax(0,1fr); gap:7px; align-items:center; margin:12px 0; }}
.dependency-column {{ display:grid; gap:7px; min-width:0; }}
.dependency-selected {{ border:2px solid var(--cyan); border-radius:9px; padding:10px; background:#12334b99; }}
.dependency-edge {{ color:#e879f9; text-align:center; font-size:25px; }}
.parameter-summary {{ margin-bottom:14px; }}
.chart {{ width:100%; height:270px; background:#081624; border-radius:8px; }}
.config-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:8px; }}
.config-grid div {{ background:#0a192a; padding:10px; border-radius:7px; min-width:0; overflow-wrap:anywhere; }}
.canonical {{ display:block; margin-top:4px; color:var(--muted); font-size:11px; overflow-wrap:anywhere; white-space:normal; }}
.lifetime {{ position:relative; height:46px; margin:8px 0 14px 120px; border-bottom:1px solid #355672; }}
.lifetime-label {{ position:absolute; left:-120px; width:110px; top:11px; color:var(--muted); }}
.life-bar {{ position:absolute; top:10px; height:20px; border-radius:5px; min-width:3px; }}
.life-bar.formal {{ background:#69a9ff99; border:1px solid #69a9ff; }}
.life-bar.planned {{ background:#5dff8b88; border:1px solid #5dff8b; }}
.life-event {{ position:absolute; top:5px; width:2px; height:30px; background:#ffd166; }}
.state-lane {{ position:relative; height:58px; margin:8px 0 16px 105px; border-bottom:1px solid #355672; }}
.state-label {{ position:absolute; left:-105px; top:16px; color:var(--muted); }}
.state-segment {{ position:absolute; top:10px; height:29px; border-radius:5px; background:#315f9d99; border:1px solid #69a9ff; padding:4px 6px; overflow:hidden; white-space:nowrap; font-size:11px; }}
.spatial-grid {{ display:grid; grid-template-columns:minmax(0,2fr) minmax(240px,.65fr); gap:12px; }}
.globe {{ width:100%; min-height:420px; background:#06111d; border-radius:9px; touch-action:none; }}
.spatial-controls {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
.spatial-side.active {{ border-color:var(--cyan); color:var(--cyan); }}
.zero {{ color:var(--muted); }} .domain-count {{ display:inline-block; margin:4px 12px 4px 0; }}
.event-groups {{ display:grid; grid-template-columns:repeat(3,minmax(190px,1fr)); gap:9px; }}
.event-group {{ min-width:0; }} .event-list {{ max-height:190px; overflow-y:auto; }}
.event-button {{ width:100%; margin:3px 0; padding:6px 8px; text-align:left; overflow-wrap:anywhere; }}
.event-button.active {{ border-color:var(--cyan); color:var(--cyan); }}
table td,table th,select,option,h2,h3,p {{ overflow-wrap:anywhere; }}
@media(max-width:850px) {{ .split,.spatial-grid,.event-groups {{ grid-template-columns:1fr; }} .dependency-chain {{ grid-template-columns:1fr; }} .dependency-edge {{ transform:rotate(90deg); }} }}
</style>
<section class="card">
  <span class="status PASS">FORMAL OUTPUT OBSERVED</span>
  <h2 id="scenario-title"></h2>
  <p>This page compares already-produced Slicer/Difference facts with immutable
  Plan publications and retained PlanningDecision evidence. It does not run a
  Model, recompute a Difference, or infer an expected Plan.</p>
  <div class="config-grid" id="configuration"></div>
</section>
<section><h2>Inspection inventory</h2><div class="grid stats">{stats}</div></section>
<section class="card planner-note">
  <strong>Sampled timeline ≠ publication timeline.</strong>
  Plans cover continuous <code>(start,end]</code> segments. Same-time markers
  are grouped; tuple order is not runtime execution order. Purple precedence
  relations are separate same-time semantics.
</section>
<nav class="tabs">
  <button class="tab active" data-view="overview">Global timeline</button>
  <button class="tab" data-view="connections">Connections</button>
  <button class="tab" data-view="parameters">Parameters</button>
  <button class="tab" data-view="forwarding">Forwarding</button>
  <button class="tab" data-view="decisions">Decisions</button>
  <button class="tab" data-view="comparison">Difference → Plan</button>
</nav>
<section class="card" id="spatial-driver">
  <h2 id="spatial-driver-title">Global Timeline selection → shared spatial context</h2>
  <div id="overview-spatial-driver">
    <label>Nominal time <select id="nominal-time-select"></select></label>
    <div id="nominal-summary"></div>
    <div id="nominal-events"></div>
  </div>
  <p class="muted" id="tab-spatial-hint" hidden>Select one item in the current tab below; that selection drives this single shared globe.</p>
</section>
<section class="card" id="spatial-context">
  <div class="spatial-controls">
    <h2 style="margin-right:auto">Formal sampled spatial context</h2>
    <button class="spatial-side active" data-side="before">Before</button>
    <button class="spatial-side" data-side="after">After</button>
    <button id="reset-globe">Reset view</button>
  </div>
  <div class="spatial-grid">
    <canvas class="globe" id="planner-globe" width="1100" height="520"></canvas>
    <div id="spatial-detail"></div>
  </div>
</section>
<section class="view active" id="view-overview">
  <div class="card scroll"><div class="timeline" id="global-timeline"></div></div>
  <div class="card"><h2>Consecutive published Plans</h2><div id="plan-table"></div></div>
</section>
<section class="view" id="view-connections">
  <div class="split"><div class="card"><h2>Logical ConnectionId</h2><select class="listbox" id="connection-select" size="14"></select></div><div class="card" id="connection-detail"></div></div>
</section>
<section class="view" id="view-parameters">
  <div class="card parameter-summary" id="parameter-overview"></div>
  <div class="split"><div class="card"><h2>Connection + direction + field</h2><select class="listbox" id="parameter-select" size="14"></select></div><div class="card" id="parameter-detail"></div></div>
</section>
<section class="view" id="view-forwarding">
  <div class="split"><div class="card"><h2>RouteKey</h2><select class="listbox" id="route-select" size="14"></select></div><div class="card" id="route-detail"></div></div>
</section>
<section class="view" id="view-decisions">
  <div class="grid two"><div class="card"><h2>Recorded PlanningDecision values</h2><div id="decision-list"></div></div><div class="card"><h2>Inspection boundary</h2><div id="decision-boundary"></div></div></div>
</section>
<section class="view" id="view-comparison">
  <div class="card controls"><label>Difference <select id="difference-select"></select></label></div>
  <div class="grid two"><div class="card"><h2>Simulator / Difference</h2><div id="formal-comparison"></div></div><div class="card"><h2>Planner / published transitions</h2><div id="planned-comparison"></div></div></div>
</section>
<details class="card"><summary>Raw deterministic inspection snapshot</summary><pre id="raw-json"></pre></details>
"""
    return standalone_page(
        title="Planner Inspection Tool",
        eyebrow=eyebrow,
        body=body,
        script=_SCRIPT.replace("__PLANNER_DATA__", json_for_script(data)),
        language_strings={
            "Planner Inspection Tool": "Planner 检查工具",
            eyebrow: (
                f"{inventory['satellites']} 颗 Satellite · "
                f"{inventory['ground_stations']} 个 GroundStation · 只读"
            ),
            "FORMAL OUTPUT OBSERVED": "已观测正式输出",
            "Mini Satellite Internet · Planner inspection": "Mini 卫星互联网 · Planner 检查",
            "This page compares already-produced Slicer/Difference facts with immutable Plan publications and retained PlanningDecision evidence. It does not run a Model, recompute a Difference, or infer an expected Plan.": "本页比较已经产生的 Slicer / Difference 事实、不可变 Plan 发布和保留的 PlanningDecision evidence；不会运行 Model、重新计算 Difference 或推断预期 Plan。",
            "Inspection inventory": "检查内容概览",
            "Sampled timeline ≠ publication timeline.": "采样时间轴 ≠ 发布时间轴。",
            "Plans cover continuous (start,end] segments. Same-time markers are grouped; tuple order is not runtime execution order. Purple precedence relations are separate same-time semantics.": "Plan 覆盖连续的 (start,end] 区间。同一时刻的标记会分组；tuple 顺序不是 runtime 执行顺序。紫色 precedence 关系是独立的同一时刻语义。",
            "Global timeline": "全局时间轴",
            "Connections": "连接",
            "Parameters": "参数",
            "Forwarding": "转发",
            "Decisions": "决策",
            "Difference → Plan": "Difference → Plan",
            "Global Timeline selection → shared spatial context": "全局时间轴选择 → 共享空间上下文",
            "Nominal time": "Nominal time",
            "Select one item in the current tab below; that selection drives this single shared globe.": "在当前标签页中选择一个项目；该选择会驱动这一个共享 globe。",
            "Formal sampled spatial context": "正式采样空间上下文",
            "Before": "之前",
            "After": "之后",
            "Consecutive published Plans": "连续发布的 Plan",
            "Logical ConnectionId": "逻辑 ConnectionId",
            "Connection + direction + field": "Connection + 方向 + 字段",
            "Recorded PlanningDecision values": "已记录 PlanningDecision 值",
            "Inspection boundary": "检查边界",
            "Simulator / Difference": "Simulator / Difference",
            "Planner / published transitions": "Planner / 已发布 transition",
            "Raw deterministic inspection snapshot": "原始确定性检查快照",
            "Scenario": "场景",
            "Sampling": "采样",
            "Planning window": "planning window",
            "Publication": "发布",
            "Connection timing": "Connection 时间",
            "Coordination": "协调",
            "Parameter thresholds": "参数阈值",
            "Mini satellite Internet": "Mini 卫星互联网",
            "All generated unpublished Differences": "所有已生成且未发布的 Difference",
            "Midpoint": "中点",
            "Dependency-aware": "依赖感知",
            "Default conservative": "默认保守策略",
            "Accumulated deadband": "累计 deadband",
            "Real Slices": "真实 Slice",
            "Differences": "Difference",
            "Published Plans": "已发布 Plan",
            "Connection": "Connection",
            "Parameter": "参数",
            "Published Parameter": "已发布参数",
            "Precedences": "Precedence",
            "Same-time forwarding dependency": "同一时刻转发依赖",
            "Formal observations vs published parameter targets": "正式观测与已发布参数目标",
            "Formal parameter patches": "正式参数 patch",
            "Published ParameterTransitions": "已发布 ParameterTransition",
            "Not individually published": "未单独发布",
            "Field breakdown": "字段分布",
            "Formal patches": "正式 patch",
            "Published targets": "已发布目标",
            "Configured thresholds": "已配置阈值",
            "Absolute threshold": "绝对阈值",
            "Parameter deadband/coalescing:": "参数 deadband / 合并：",
            "Publication batching:": "发布分批：",
            "Formal sample count": "正式样本数",
            "Published target count": "已发布目标数",
            "Configured threshold": "已配置阈值",
            "Semantic target": "语义目标",
            "Current service/path-terminal satellite": "当前服务 / 路径终点卫星",
            "Semantic target: GroundStation": "语义目标：GroundStation",
            "No formal values in this selection.": "此选择中没有正式值。",
            "Positions are exact retained Slice samples. No midpoint interpolation or position-model rerun is performed.": "Position 是精确保留的 Slice 样本；不会执行中点插值或重跑 position model。",
        },
    )


_SCRIPT = r"""
const DATA=__PLANNER_DATA__;
const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const table=(headers,rows)=>rows.length?`<table><thead><tr>${headers.map(v=>`<th>${esc(v)}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table>`:'<p class="muted">No formal values in this selection.</p>';
const fmt=value=>value===null?"open":String(value).replace("T"," ").replace("+00:00","Z");
const aliases=DATA.display_aliases;
const zonesById=Object.fromEntries(DATA.topology.zones.map(value=>[value.zone_id,value]));
const nodeAlias=value=>value===null?"—":aliases.nodes[value]||String(value);
const zoneAlias=value=>aliases.zones[value]||`Z:${value}`;
const targetAlias=target=>target.kind==="G"?nodeAlias(target.ground_station_id):zoneAlias(target.zone_id);
const routeAlias=value=>{const group=DATA.forwarding.find(item=>item.route_ref===value);return group?`${nodeAlias(group.source)} → ${targetAlias(group.target)}`:String(value);};
const connectionAlias=value=>`${nodeAlias(value.connection_id.endpoint_a)} ↔ ${nodeAlias(value.connection_id.endpoint_b)}`;
const canonical=value=>`<code class="canonical" title="${esc(value)}">${esc(value)}</code>`;
const config=DATA.configuration;
document.getElementById("scenario-title").textContent="Mini Satellite Internet · Planner inspection";
const configCards=[
  ["Scenario","Mini satellite Internet",config.scenario_identity],
  ["Sampling",`${config.slice_interval_seconds} s · ${config.real_slice_count} real Slices`,"formal retained Slices"],
  ["Planning window","All generated unpublished Differences",config.planning_window],
  ["Publication",`${config.publication_difference_count} Differences / Plan`,"continuous (start,end] coverage"],
  ["Connection timing","Midpoint",`minimum dwell ${config.minimum_dwell_seconds} s`],
  ["Forwarding","Dependency-aware",config.forwarding_model],
  ["Coordination","Default conservative",config.coordination_policy],
  ["Parameter thresholds","Accumulated deadband",JSON.stringify(config.parameter_thresholds)]
];
document.getElementById("configuration").innerHTML=configCards.map(([label,value,detail])=>`<div><span class="muted">${esc(label)}</span><br><strong>${esc(value)}</strong><span class="canonical">${esc(detail)}</span></div>`).join("");
const formalParameterPatches=DATA.slices.flatMap(value=>value.difference_from_previous?.connection_parameter_patches||[]);
const publishedParameterTransitions=DATA.plans.flatMap(value=>value.parameter_transitions);
const parameterFields=["bandwidth_bps","delay_us","packet_loss_ppm"];
const formalParameterFields=Object.fromEntries(parameterFields.map(field=>[field,formalParameterPatches.reduce((count,patch)=>count+(Object.hasOwn(patch.changed,field)?1:0),0)]));
const publishedParameterFields=Object.fromEntries(parameterFields.map(field=>[field,publishedParameterTransitions.reduce((count,transition)=>count+(Object.hasOwn(transition.targets,field)?1:0),0)]));
const formalParameterFieldCount=Object.values(formalParameterFields).reduce((total,value)=>total+value,0);
const publishedParameterFieldCount=Object.values(publishedParameterFields).reduce((total,value)=>total+value,0);
document.getElementById("parameter-overview").innerHTML=`<h2>Formal observations vs published parameter targets</h2><div class="grid stats"><div class="card stat"><span class="muted">Formal parameter patches</span><strong>${formalParameterPatches.length}</strong></div><div class="card stat"><span class="muted">Published ParameterTransitions</span><strong>${publishedParameterTransitions.length}</strong></div><div class="card stat"><span class="muted">Not individually published</span><strong>${Math.max(0,formalParameterFieldCount-publishedParameterFieldCount)}</strong></div></div><div class="grid two"><div><h3>Field breakdown</h3>${table(["Field","Formal patches","Published targets"],parameterFields.map(field=>[field,formalParameterFields[field],publishedParameterFields[field]]))}</div><div><h3>Configured thresholds</h3>${table(["Field","Absolute threshold"],parameterFields.map(field=>[field,config.parameter_thresholds[field]]))}</div></div><p><strong>Parameter deadband/coalescing:</strong> a patch not individually published is not loss or error; its drift remains accumulated against the last planned target until the configured threshold is reached or re-establishment resets the baseline.</p><p><strong>Publication batching:</strong> ${config.real_slice_count} Differences → ${DATA.plans.length} Plans is a separate planning/publication-window concern. A Plan may retain multiple ParameterTransitions at distinct nominal times.</p>`;

document.querySelectorAll(".tab").forEach(button=>button.addEventListener("click",()=>{
  document.querySelectorAll(".tab").forEach(value=>value.classList.toggle("active",value===button));
  document.querySelectorAll(".view").forEach(value=>value.classList.toggle("active",value.id===`view-${button.dataset.view}`));
  const overview=button.dataset.view==="overview";
  document.getElementById("overview-spatial-driver").hidden=!overview;
  document.getElementById("tab-spatial-hint").hidden=overview;
  document.getElementById("spatial-driver-title").textContent=overview
    ?"Global Timeline selection → shared spatial context"
    :`${button.textContent} selection → shared spatial context`;
}));

const realSlices=DATA.slices.filter(value=>value.timestamp!==null);
const startMs=Date.parse(DATA.timeline.start_time),endMs=Date.parse(DATA.timeline.end_time);
const pct=time=>Math.max(0,Math.min(100,(Date.parse(time)-startMs)/(endMs-startMs)*100));
const transitions=DATA.plans.flatMap(plan=>[...plan.connection_transitions,...plan.parameter_transitions,...plan.forwarding_transitions]);
const transitionById=Object.fromEntries(transitions.map(value=>[value.transition_id,value]));
let spatialReady=false;
const grouped=Object.values(DATA.plans.flatMap(plan=>plan.temporal_groups.map(group=>({plan,group}))).reduce((result,item)=>{
  const key=item.group.nominal_time;
  const record=result[key]??={nominal_time:key,plans:[],values:[]};
  record.plans.push(item.plan.label);record.values.push(...item.group.transition_ids.map(id=>transitionById[id]));return result;
},{})).sort((a,b)=>a.nominal_time.localeCompare(b.nominal_time));
function ticks(){return realSlices.map(value=>`<div class="tick" style="left:${pct(value.timestamp)}%"><span>S${value.slice_id}</span></div>`).join("");}
function lane(label,content){return `<div class="lane"><div class="lane-label">${esc(label)}</div>${ticks()}${content}</div>`;}
function renderTimeline(){
  const planBars=DATA.plans.map(plan=>{const start=plan.start_timestamp||DATA.timeline.start_time,left=pct(start),right=pct(plan.end_timestamp),remaining=Math.max(0,100-right);return `<div class="plan-bar" title="${esc(plan.coverage)}" style="left:${left}%;width:${Math.max(1,right-left)}%">${esc(plan.label)} ${esc(plan.coverage)}</div>${remaining?`<div class="planning-window-bar" title="unpublished planning window" style="left:${right}%;width:${remaining}%"></div>`:""}`;}).join("");
  const markerLane=family=>grouped.flatMap(group=>{const count=group.values.filter(value=>value.family===family).length;return count?[`<button class="marker ${family}" data-time="${esc(group.nominal_time)}" title="${fmt(group.nominal_time)} · ${count} grouped" style="left:${pct(group.nominal_time)}%">${count}</button>`]:[];}).join("");
  document.getElementById("global-timeline").innerHTML=[lane("Plan coverage",planBars),lane("Connection",markerLane("connection")),lane("Parameter",markerLane("parameter")),lane("Forwarding",markerLane("forwarding"))].join("")+`<p class="muted">Blank lane positions mean zero published transitions in that domain, not a render failure. Yellow under-bars show the unpublished planning window beyond each publication end.</p>`;
  document.querySelectorAll(".marker").forEach(marker=>marker.addEventListener("click",()=>{nominalSelect.value=marker.dataset.time;renderNominalSummary();}));
}
const nominalSelect=document.getElementById("nominal-time-select");
nominalSelect.innerHTML=grouped.map(value=>`<option value="${esc(value.nominal_time)}">${fmt(value.nominal_time)}</option>`).join("");
function nominalEventLabel(value){
  if(value.family==="connection"){
    const connection=DATA.connections.find(item=>item.connection_ref===value.connection_ref);
    return `${value.operation.toUpperCase()} · ${connectionAlias(connection)}`;
  }
  if(value.family==="parameter"){
    const connection=DATA.connections.find(item=>item.connection_ref===value.connection_ref);
    return `${connectionAlias(connection)} · ${value.direction} · ${Object.keys(value.targets).join(", ")}`;
  }
  return `${value.operation.toUpperCase()} · ${routeAlias(value.route_ref)} · ${nodeAlias(value.next_hop)}`;
}
function focusNominalEvent(value){
  document.querySelectorAll(".event-button").forEach(button=>button.classList.toggle("active",button.dataset.transitionId===value.transition_id));
  if(value.family==="forwarding"){
    setRouteSpatial(DATA.forwarding.find(item=>item.route_ref===value.route_ref),value.transition_id);
    return;
  }
  const connection=DATA.connections.find(item=>item.connection_ref===value.connection_ref);
  setSpatial({kind:"connection",time:value.nominal_time,nodes:[connection.connection_id.endpoint_a,connection.connection_id.endpoint_b],routeRef:null,target:null,title:`${value.family==="parameter"?"Parameter":"Connection"} · ${connectionAlias(connection)}`});
}
function renderNominalSummary(){
  const group=grouped.find(value=>value.nominal_time===nominalSelect.value);if(!group)return;
  const families=["connection","parameter","forwarding"];
  const counts=Object.fromEntries(families.map(family=>[family,group.values.filter(value=>value.family===family).length]));
  document.getElementById("nominal-summary").innerHTML=`<p><strong>${fmt(group.nominal_time)}</strong> · ${esc(group.plans.join(", "))}</p>${families.map(family=>`<span class="domain-count ${counts[family]===0?"zero":""}">${family}: <strong>${counts[family]}</strong></span>`).join("")}${counts.forwarding===0?'<p class="zero">No published forwarding-state change at this nominal time.</p>':""}`;
  document.getElementById("nominal-events").innerHTML=`<div class="event-groups">${families.map(family=>{const values=group.values.filter(value=>value.family===family);return `<section class="event-group"><h3>${esc(family)} (${values.length})</h3><div class="event-list">${values.length?values.map(value=>`<button class="event-button" data-transition-id="${esc(value.transition_id)}" title="${esc(value.transition_id)}">${esc(nominalEventLabel(value))}</button>`).join(""):'<p class="zero">0 real transitions</p>'}</div></section>`;}).join("")}</div><p class="muted">Only the selected concrete event is drawn on the globe; the complete list remains scrollable.</p>`;
  document.querySelectorAll(".event-button").forEach(button=>button.addEventListener("click",()=>focusNominalEvent(transitionById[button.dataset.transitionId])));
  if(spatialReady)setSpatial({kind:"none",time:group.nominal_time,nodes:[],routeRef:null,target:null,title:`Nominal time · ${fmt(group.nominal_time)} · select one event`});
}
nominalSelect.addEventListener("change",renderNominalSummary);renderTimeline();renderNominalSummary();
document.getElementById("plan-table").innerHTML=table(["Plan","Coverage","Start","End","C","P","F","Precedence"],DATA.plans.map(plan=>[plan.label,plan.coverage,fmt(plan.start_timestamp),fmt(plan.end_timestamp),plan.connection_transitions.length,plan.parameter_transitions.length,plan.forwarding_transitions.length,plan.forwarding_precedences.length]));

function fillSelect(element,values,key,label,selected){element.innerHTML=values.map(value=>`<option value="${esc(value[key])}" ${value[key]===selected?"selected":""} title="${esc(value[key])}">${esc(label(value))}</option>`).join("");}
function lifetimeLane(label,occurrences,kind){return `<div class="lifetime"><span class="lifetime-label">${esc(label)}</span>${occurrences.map((item,index)=>{const start=item.start_timestamp??item.establish_time,end=item.end_timestamp??item.withdraw_time??DATA.timeline.end_time;return `<span class="life-bar ${kind}" title="Occurrence ${index+1}: ${fmt(start)} → ${fmt(end)}" style="left:${pct(start)}%;width:${Math.max(.5,pct(end)-pct(start))}%"></span>${kind==="planned"?`<span class="life-event" title="ESTABLISH ${fmt(start)}" style="left:${pct(start)}%"></span><span class="life-event" title="WITHDRAW ${fmt(end)}" style="left:${pct(end)}%"></span>`:""}`;}).join("")}</div>`;}
const connectionSelect=document.getElementById("connection-select");
fillSelect(connectionSelect,DATA.connections,"connection_ref",value=>`${value.formal_occurrences.length>1?"↻ ":""}${connectionAlias(value)}`,DATA.representatives.connection_ref);
function connectionTransitions(reference){return transitions.filter(value=>value.family==="connection"&&value.connection_ref===reference);}
function renderConnection(){const value=DATA.connections.find(item=>item.connection_ref===connectionSelect.value);if(!value)return;const changes=connectionTransitions(value.connection_ref);const formal=value.formal_occurrences.map((item,index)=>`<div class="occurrence formal"><strong>Formal sampled period ${index+1}</strong><br>S${item.start_slice_id} → S${item.end_slice_id} · ${item.sample_count} samples<br>${fmt(item.start_timestamp)} → ${fmt(item.end_timestamp)}</div>`).join("");const planned=value.planned_occurrences.map((item,index)=>`<div class="occurrence planned"><strong>Planned period ${index+1}</strong><br>ESTABLISH ${fmt(item.establish_time)}<br>WITHDRAW ${fmt(item.withdraw_time)}</div>`).join("");document.getElementById("connection-detail").innerHTML=`<h2>${esc(connectionAlias(value))}</h2>${canonical(value.connection_ref)}<h3>Lifetime comparison</h3>${lifetimeLane("Formal sampled",value.formal_occurrences,"formal")}${lifetimeLane("Planned",value.planned_occurrences,"planned")}<p class="muted">Blue boundaries are retained Slice samples; yellow ticks are actual midpoint-capable nominal transitions. Separate bars preserve repeated lifetimes.</p><label>Spatial event <select id="connection-event">${changes.map(item=>`<option value="${item.transition_id}">${esc(item.operation.toUpperCase())} · ${fmt(item.nominal_time)}</option>`).join("")}</select></label><div class="grid two"><div><h3>Formal sampled presence</h3>${formal||'<p class="muted">None</p>'}</div><div><h3>Published planned presence</h3>${planned||'<p class="muted">No published occurrence; suppression cannot be inferred.</p>'}</div></div><p><strong>Minimum dwell:</strong> ${esc(config.minimum_dwell_seconds)} s. Rejected candidate status is unavailable.</p>`;const event=document.getElementById("connection-event");if(event){event.addEventListener("change",()=>setConnectionSpatial(value,event.value));setConnectionSpatial(value,event.value);}}
connectionSelect.addEventListener("change",renderConnection);

function parameterPolylines(samples,min,span){const groups=[];let group=[];samples.forEach(sample=>{if(group.length&&sample.slice_id!==group[group.length-1].slice_id+1){groups.push(group);group=[];}group.push(sample);});if(group.length)groups.push(group);return groups.map(values=>`<polyline points="${values.map(item=>`${pct(item.timestamp)},${90-(Number(item.value)-min)/span*75}`).join(" ")}" fill="none" stroke="#69a9ff" stroke-width="1.4" vector-effect="non-scaling-stroke"/>`).join("");}
const parameterSelect=document.getElementById("parameter-select");
fillSelect(parameterSelect,DATA.parameter_series,"parameter_key",value=>`${connectionAlias(DATA.connections.find(item=>item.connection_ref===value.connection_ref))} · ${value.direction} · ${value.field}`,DATA.representatives.parameter_key);
function renderParameter(){const value=DATA.parameter_series.find(item=>item.parameter_key===parameterSelect.value);if(!value)return;const samples=value.samples,targets=value.planned,segments=value.planned_state_occurrences.flatMap(item=>item.segments);const threshold=Number((config.parameter_thresholds||{})[value.field]??0);const all=[...samples.map(item=>Number(item.value)),...segments.map(item=>Number(item.value)),...segments.flatMap(item=>[Number(item.value)-threshold,Number(item.value)+threshold])];const min=Math.min(...all),max=Math.max(...all),span=Math.max(1,max-min);const y=v=>90-(Number(v)-min)/span*75;const bands=segments.map(item=>`<rect x="${pct(item.start_time)}" width="${Math.max(.2,pct(item.end_time)-pct(item.start_time))}" y="${Math.min(y(Number(item.value)+threshold),y(Number(item.value)-threshold))}" height="${Math.abs(y(Number(item.value)+threshold)-y(Number(item.value)-threshold))}" fill="#ffd16618"/>`).join("");const steps=value.planned_state_occurrences.flatMap(occ=>occ.segments.map((item,index)=>`<line x1="${pct(item.start_time)}" x2="${pct(item.end_time)}" y1="${y(item.value)}" y2="${y(item.value)}" stroke="#5dff8b" stroke-width="2" vector-effect="non-scaling-stroke"/>${index?`<line x1="${pct(item.start_time)}" x2="${pct(item.start_time)}" y1="${y(occ.segments[index-1].value)}" y2="${y(item.value)}" stroke="#5dff8b" stroke-width="1" vector-effect="non-scaling-stroke"/>`:""}`)).join("");const targetMarks=targets.map(item=>`<circle cx="${pct(item.nominal_time)}" cy="${y(item.value)}" r="2.4" fill="#ffd166"><title>${esc(item.value)} @ ${esc(item.nominal_time)}</title></circle>`).join("");const connection=DATA.connections.find(item=>item.connection_ref===value.connection_ref);document.getElementById("parameter-detail").innerHTML=`<h2>${esc(connectionAlias(connection))}</h2>${canonical(value.connection_ref)}<div class="grid stats"><div class="card stat"><span class="muted">Formal sample count</span><strong>${samples.length}</strong></div><div class="card stat"><span class="muted">Published target count</span><strong>${targets.length}</strong></div><div class="card stat"><span class="muted">Configured threshold</span><strong>${esc(threshold)}</strong></div></div><p>${esc(value.direction)} · <strong>${esc(value.field)}</strong></p><svg class="chart" viewBox="0 0 100 100" preserveAspectRatio="none">${bands}${parameterPolylines(samples,min,span)}${steps}${targetMarks}</svg><p class="muted">Blue: formal samples. Green step: planned target derived only from published ESTABLISH parameters and ParameterTransitions. Yellow band: configured threshold around each planned target. Gaps separate disconnected lifetimes; every re-establishment resets from its published initial parameters.</p><div class="grid two"><div><h3>Sampled values</h3>${table(["Slice","Time","Value"],samples.map(item=>[`S${item.slice_id}`,fmt(item.timestamp),item.value]))}</div><div><h3>Published transitions</h3>${table(["Plan","Nominal time","Target"],targets.map(item=>[item.plan_label,fmt(item.nominal_time),item.value]))}</div></div>`;const event=targets[0]?.nominal_time??samples[0]?.timestamp;setSpatial({kind:"connection",time:event,nodes:[connection.connection_id.endpoint_a,connection.connection_id.endpoint_b],title:`Parameter · ${connectionAlias(connection)}`});}
parameterSelect.addEventListener("change",renderParameter);

function stateSegments(events){let state=null,result=[];events.forEach((event,index)=>{if(event.operation==="removed")state=null;else state=event.next_hop;const end=events[index+1]?.nominal_time??events[index+1]?.timestamp??DATA.timeline.end_time;const start=event.nominal_time??event.timestamp;if(state!==null)result.push({start,end,state,operation:event.operation});});return result;}
function stateLane(label,events){return `<div class="state-lane"><span class="state-label">${esc(label)}</span>${stateSegments(events).map(item=>`<span class="state-segment" title="${esc(item.operation)} · ${esc(item.state)}" style="left:${pct(item.start)}%;width:${Math.max(.5,pct(item.end)-pct(item.start))}%">${esc(nodeAlias(item.state))}</span>`).join("")}</div>`;}
const routeSelect=document.getElementById("route-select");
fillSelect(routeSelect,DATA.forwarding,"route_ref",value=>routeAlias(value.route_ref),DATA.representatives.route_ref);
function priorNextHop(route,transition){let state=null;for(const value of route.planned_transitions){if(value.transition_id===transition.transition_id)break;state=value.operation==="removed"?null:value.next_hop;}return state;}
function transitionNextHop(transition){const route=DATA.forwarding.find(item=>item.route_ref===transition.route_ref);return transition.operation==="removed"&&route?priorNextHop(route,transition):transition.next_hop;}
function transitionCard(value,selected=false){const nextHop=transitionNextHop(value),context=value.operation==="removed"?`BEFORE next hop: ${nodeAlias(nextHop)}`:`Next hop: ${nodeAlias(nextHop)}`;return `<div class="${selected?"dependency-selected":"precedence"}" data-transition="${esc(value.transition_id)}"><strong title="${esc(value.route_ref)}">${esc(routeAlias(value.route_ref))}</strong><br>${esc(value.operation.toUpperCase())}<br><span class="muted">${esc(context)}</span></div>`;}
function renderPrecedence(route,transitionId){const selected=transitionById[transitionId]??route.planned_transitions[0],edges=route.precedences.filter(edge=>edge.nominal_time===selected.nominal_time&&(edge.predecessor===selected.transition_id||edge.successor===selected.transition_id)),incoming=edges.filter(edge=>edge.successor===selected.transition_id),outgoing=edges.filter(edge=>edge.predecessor===selected.transition_id);document.getElementById("route-precedence").innerHTML=`<div data-selected-precedence="${esc(selected.transition_id)}"><p><strong>${esc(selected.operation.toUpperCase())}</strong> · ${fmt(selected.nominal_time)} · ${esc(routeAlias(selected.route_ref))}</p>${edges.length?`<div class="dependency-chain"><div class="dependency-column">${incoming.map(edge=>transitionCard(edge.predecessor_transition)).join("")||'<span class="muted">No direct predecessor</span>'}</div><div class="dependency-edge">→</div>${transitionCard(selected,true)}<div class="dependency-edge">→</div><div class="dependency-column">${outgoing.map(edge=>transitionCard(edge.successor_transition)).join("")||'<span class="muted">No direct successor</span>'}</div></div>`:'<p class="muted">No direct same-time precedence relation touches the selected ForwardingTransition.</p>'}</div><p class="muted">Only formal direct same-time ForwardingTransitionPrecedence values touching the selected event are shown. This is Plan semantic dependency, NOT tuple order and NOT BPF/Linux total execution order.</p>`;}
function renderRouteSelection(route,transitionId){renderPrecedence(route,transitionId);setRouteSpatial(route,transitionId);}
function renderRoute(){const value=DATA.forwarding.find(item=>item.route_ref===routeSelect.value);if(!value)return;document.getElementById("route-detail").innerHTML=`<h2>${esc(routeAlias(value.route_ref))}</h2>${canonical(value.route_ref)}<h3>Next-hop state timeline</h3>${stateLane("Formal patch",value.formal_patches)}${stateLane("Published",value.planned_transitions)}<label>Spatial event <select id="route-event">${value.planned_transitions.map(item=>`<option value="${item.transition_id}">${esc(item.operation.toUpperCase())} · ${fmt(item.nominal_time)} · ${esc(nodeAlias(transitionNextHop(item)))}</option>`).join("")}</select></label><div class="grid two"><div><h3>Formal RoutePatch evolution</h3>${table(["Δ","Time","Operation","Next hop"],value.formal_patches.map(item=>[`Δ${item.difference_id}`,fmt(item.timestamp),item.operation,nodeAlias(item.next_hop)]))}</div><div><h3>Published ForwardingTransitions</h3>${table(["Plan","Nominal time","Operation","Next hop / BEFORE context"],value.planned_transitions.map(item=>[item.plan_label,fmt(item.nominal_time),item.operation,nodeAlias(transitionNextHop(item))]))}</div></div><h3>Same-time forwarding dependency</h3><div id="route-precedence"></div>`;const event=document.getElementById("route-event");if(event){event.addEventListener("change",()=>renderRouteSelection(value,event.value));renderRouteSelection(value,event.value);}}
routeSelect.addEventListener("change",renderRoute);

document.getElementById("decision-list").innerHTML=DATA.decisions.length?DATA.decisions.map(value=>`<div class="occurrence ${value.decision}"><strong>${esc(value.decision.toUpperCase())}</strong> · Δ${esc(value.source_difference_ids.join(", Δ"))}<br><span class="muted">${esc(value.decided_by)}</span><br>${esc(value.reason)}</div>`).join(""):'<p class="muted">No PlanningDecision was retained.</p>';
document.getElementById("decision-boundary").innerHTML=`<p><strong>Accepted suppressions:</strong> ${DATA.observed_behavior.accepted_suppressions}</p><p><strong>Rejected candidates:</strong> unavailable — ${esc(DATA.inspection_limits.rejected_suppression_candidates)}.</p><p>Zero accepted suppressions is valid conservative behavior, not Tool failure.</p>`;
const differenceSelect=document.getElementById("difference-select");
differenceSelect.innerHTML=DATA.difference_comparisons.map(value=>`<option value="${value.difference_id}">Δ${value.difference_id} ${esc(value.interval)}</option>`).join("");
function transitionText(value){if(value.family==="connection")return `${value.operation} ${connectionAlias(DATA.connections.find(item=>item.connection_ref===value.connection_ref))} @ ${fmt(value.nominal_time)}`;if(value.family==="parameter")return `${connectionAlias(DATA.connections.find(item=>item.connection_ref===value.connection_ref))} ${value.direction} ${JSON.stringify(value.targets)} @ ${fmt(value.nominal_time)}`;return `${value.operation} ${routeAlias(value.route_ref)} → ${nodeAlias(value.next_hop)} @ ${fmt(value.nominal_time)}`;}
function renderComparison(){const value=DATA.difference_comparisons.find(item=>String(item.difference_id)===differenceSelect.value);if(!value)return;document.getElementById("formal-comparison").innerHTML=`<h3>Δ${value.difference_id} ${esc(value.interval)}</h3><p>${fmt(value.previous_timestamp)} → ${fmt(value.current_timestamp)}</p>${table(["Formal category","Count"],Object.entries(value.formal))}`;const rows=["connection","parameter","forwarding"].flatMap(family=>value.planned[family].map(item=>[family,item.plan_label,transitionText(item)]));document.getElementById("planned-comparison").innerHTML=table(["Family","Plan","Published fact"],rows);const first=[...value.planned.connection,...value.planned.forwarding,...value.planned.parameter][0];if(first){nominalSelect.value=first.nominal_time;if(grouped.some(group=>group.nominal_time===first.nominal_time))renderNominalSummary();if(first.family==="forwarding")setRouteSpatial(DATA.forwarding.find(item=>item.route_ref===first.route_ref),first.transition_id);else {const connection=DATA.connections.find(item=>item.connection_ref===first.connection_ref);setSpatial({kind:"connection",time:first.nominal_time,nodes:[connection.connection_id.endpoint_a,connection.connection_id.endpoint_b],title:`Δ${value.difference_id} · ${connectionAlias(connection)}`});}}}
differenceSelect.addEventListener("change",renderComparison);

const canvas=document.getElementById("planner-globe"),WGS84=6378137;
let camera={yaw:-.65,pitch:.28,drag:null},spatialSide="before",spatial={kind:"none",time:realSlices[0].timestamp,nodes:[],routeRef:null,title:"Select a transition"};
function rotate(point){const [x,y,z]=point,cy=Math.cos(camera.yaw),sy=Math.sin(camera.yaw),cp=Math.cos(camera.pitch),sp=Math.sin(camera.pitch),x1=x*cy+z*sy,z1=-x*sy+z*cy;return [x1,y*cp-z1*sp,y*sp+z1*cp];}
function sphere(lat,lon,r=1){const a=lat*Math.PI/180,b=lon*Math.PI/180;return [r*Math.cos(a)*Math.cos(b),r*Math.sin(a),r*Math.cos(a)*Math.sin(b)];}
function zoneBoundary(zone){const g=zone.geometry,values=[];let east=g.east_longitude_deg;if(east<=g.west_longitude_deg)east+=360;for(let i=0;i<=20;i++)values.push([g.south_latitude_deg,g.west_longitude_deg+(east-g.west_longitude_deg)*i/20]);for(let i=1;i<=12;i++)values.push([g.south_latitude_deg+(g.north_latitude_deg-g.south_latitude_deg)*i/12,east]);for(let i=1;i<=20;i++)values.push([g.north_latitude_deg,east-(east-g.west_longitude_deg)*i/20]);for(let i=1;i<=12;i++)values.push([g.north_latitude_deg-(g.north_latitude_deg-g.south_latitude_deg)*i/12,g.west_longitude_deg]);return values.map(([lat,lon])=>sphere(lat,lon,1.008));}
function project(point){const [x,y,z]=rotate(point),scale=Math.min(canvas.width,canvas.height)*.38;return [canvas.width/2+x*scale,canvas.height/2-y*scale,z];}
function ecef(vector){return [Number(vector.x)/WGS84,Number(vector.z)/WGS84,Number(vector.y)/WGS84];}
function drawPolyline(values,color,width=1,dashed=false,opacity=1){const ctx=canvas.getContext("2d");ctx.save();ctx.globalAlpha=opacity;ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dashed?[8,6]:[]);ctx.beginPath();let open=false;values.forEach(value=>{const point=project(value);if(point[2]<0){open=false;return;}if(!open){ctx.moveTo(point[0],point[1]);open=true;}else ctx.lineTo(point[0],point[1]);});ctx.stroke();ctx.restore();}
function drawSegment(a,b,color,width=2,dashed=false,opacity=1){if(!a||!b)return;const ctx=canvas.getContext("2d"),p=project(a),q=project(b);if(p[2]<-.2&&q[2]<-.2)return;ctx.save();ctx.globalAlpha=opacity;ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dashed?[8,6]:[]);ctx.beginPath();ctx.moveTo(p[0],p[1]);ctx.lineTo(q[0],q[1]);ctx.stroke();ctx.restore();}
function drawPoint(point,color,r=5,label=null,opacity=1){if(!point)return;const ctx=canvas.getContext("2d"),p=project(point);if(p[2]<0)return;ctx.save();ctx.globalAlpha=opacity;ctx.fillStyle=color;ctx.beginPath();ctx.arc(p[0],p[1],r,0,Math.PI*2);ctx.fill();if(label){ctx.fillStyle="#edf6ff";ctx.font="12px system-ui";ctx.fillText(label,p[0]+8,p[1]-7);}ctx.restore();}
function drawBase(){const ctx=canvas.getContext("2d");ctx.clearRect(0,0,canvas.width,canvas.height);const radius=Math.min(canvas.width,canvas.height)*.38,gradient=ctx.createRadialGradient(canvas.width*.45,canvas.height*.42,12,canvas.width/2,canvas.height/2,radius);gradient.addColorStop(0,"#27739d");gradient.addColorStop(1,"#08233a");ctx.fillStyle=gradient;ctx.beginPath();ctx.arc(canvas.width/2,canvas.height/2,radius,0,Math.PI*2);ctx.fill();for(let lat=-60;lat<=60;lat+=30)drawPolyline(Array.from({length:73},(_,i)=>sphere(lat,-180+i*5)),"#8dc6dd44");for(let lon=-150;lon<180;lon+=30)drawPolyline(Array.from({length:37},(_,i)=>sphere(-90+i*5,lon)),"#8dc6dd44");}
function drawZoneFootprint(zone){drawPolyline(zoneBoundary(zone),"#d7f171",4,false,.95);const center=zone.representative_point;drawPoint(sphere(center.latitude_deg,center.longitude_deg,1.012),"#d7f171",7,zoneAlias(zone.zone_id),1);}
function contextSlices(time){const before=[...realSlices].reverse().find(value=>Date.parse(value.timestamp)<Date.parse(time))??realSlices[0];const after=realSlices.find(value=>Date.parse(value.timestamp)>=Date.parse(time))??realSlices.at(-1);return {before,after};}
function setSpatial(value){spatial=value;drawSpatial();}
function setConnectionSpatial(connection,transitionId){const event=transitionById[transitionId]??connectionTransitions(connection.connection_ref)[0];setSpatial({kind:"connection",time:event?.nominal_time??realSlices[0].timestamp,nodes:[connection.connection_id.endpoint_a,connection.connection_id.endpoint_b],routeRef:null,target:null,title:`Connection · ${connectionAlias(connection)}`});}
function setRouteSpatial(route,transitionId){const event=transitionById[transitionId]??route.planned_transitions[0];setSpatial({kind:"route",time:event?.nominal_time??realSlices[0].timestamp,nodes:[route.source,event?.next_hop].filter(Boolean),routeRef:route.route_ref,target:route.target,title:`Route · ${routeAlias(route.route_ref)}`});}
function drawSpatial(){
  const contexts=contextSlices(spatial.time),slice=contexts[spatialSide],positions=Object.fromEntries(slice.positions.map(value=>[value.node_id,value])),point=id=>positions[id]?ecef(positions[id].ecef_m):null;
  drawBase();
  DATA.topology.nodes.filter(value=>value.kind==="satellite").forEach(value=>drawPoint(point(value.node_id),"#8cb7d6",2,null,.45));
  let highlighted=[...spatial.nodes],targetDetail="";
  if(spatial.kind==="route"){
    const path=slice.paths.find(value=>value.path_id===spatial.routeRef);
    if(path){highlighted=path.nodes;for(let i=1;i<path.nodes.length;i++)drawSegment(point(path.nodes[i-1]),point(path.nodes[i]),"#e879f9",3,false,.9);}
    if(spatial.target?.kind==="Z"){
      const zone=zonesById[spatial.target.zone_id];
      if(zone)drawZoneFootprint(zone);
      const terminal=path?.nodes?.at(-1)??null;
      targetDetail=`<p><strong>Semantic target</strong><br>${esc(zoneAlias(spatial.target.zone_id))}${canonical(spatial.target.zone_id)}</p><p><strong>Current service/path-terminal satellite</strong><br>${esc(nodeAlias(terminal))}${terminal?canonical(terminal):""}</p><p class="muted">The Zone identity remains the semantic target. The formal Zone rectangle and label are drawn on Earth; the actual path ends at its real node terminal. No fake satellite-to-Zone network link is drawn.</p>`;
    }else if(spatial.target?.kind==="G"){
      if(!highlighted.includes(spatial.target.ground_station_id))highlighted.push(spatial.target.ground_station_id);
      targetDetail=`<p><strong>Semantic target: GroundStation</strong><br>${esc(nodeAlias(spatial.target.ground_station_id))}${canonical(spatial.target.ground_station_id)}</p><p class="muted">This RouteKey has a GroundStation semantic target; no Zone footprint is expected.</p>`;
    }
  }else if(highlighted.length>=2)drawSegment(point(highlighted[0]),point(highlighted[1]),"#ffd166",4);
  highlighted.forEach(id=>drawPoint(point(id),"#72f1e1",7,nodeAlias(id)));
  document.getElementById("spatial-detail").innerHTML=`<h3>${esc(spatial.title)}</h3><p><strong>Planner nominal transition</strong><br>${fmt(spatial.time)}</p><p><strong>${spatialSide==="before"?"Before":"After"} formal sample</strong><br>Slice ${slice.slice_id} @ ${fmt(slice.timestamp)}</p>${targetDetail}<p class="muted">Positions are exact retained Slice samples. No midpoint interpolation or position-model rerun is performed.</p>${highlighted.map(id=>`<span title="${esc(id)}">${esc(nodeAlias(id))}</span>${canonical(id)}`).join("")}`;
}
document.querySelectorAll(".spatial-side").forEach(button=>button.addEventListener("click",()=>{spatialSide=button.dataset.side;document.querySelectorAll(".spatial-side").forEach(value=>value.classList.toggle("active",value===button));drawSpatial();}));
document.getElementById("reset-globe").addEventListener("click",()=>{camera={yaw:-.65,pitch:.28,drag:null};drawSpatial();});
canvas.addEventListener("pointerdown",event=>{camera.drag=[event.clientX,event.clientY];canvas.setPointerCapture(event.pointerId);});
canvas.addEventListener("pointermove",event=>{if(!camera.drag)return;camera.yaw+=(event.clientX-camera.drag[0])*.008;camera.pitch=Math.max(-1.3,Math.min(1.3,camera.pitch+(event.clientY-camera.drag[1])*.008));camera.drag=[event.clientX,event.clientY];drawSpatial();});
canvas.addEventListener("pointerup",()=>{camera.drag=null;});

renderConnection();renderParameter();renderRoute();renderComparison();spatialReady=true;renderNominalSummary();drawSpatial();
document.getElementById("raw-json").textContent=JSON.stringify(DATA,null,2);
"""


__all__ = ["render_planner_page"]
