"""Render BPF Compiler inspection data as one offline human-review page."""

from __future__ import annotations

from typing import Any

from tools._html import json_for_script, standalone_page


def render_bpf_compiler_page(data: dict[str, Any]) -> str:
    """Return the compiled-result-first Compiler inspection UI."""

    if not isinstance(data, dict) or data.get("tool") != (
        "bpf-compiler-inspection"
    ):
        raise TypeError("data must be final BPF Compiler inspection view data")
    inventory = data["inventory"]
    eyebrow = f"{inventory['plans']} continuous Plans · offline · read-only"
    stats = "".join(
        f'<div class="card stat"><span class="muted">{label}</span>'
        f'<strong>{inventory[key]}</strong></div>'
        for key, label in (
            ("plans", "Plan intervals"),
            ("resource_intents", "Resources"),
            ("parameter_intents", "Qdisc intents"),
            ("forwarding_intents", "Forwarding"),
            ("zone_edge_intents", "Zone edges"),
            ("constraints", "Constraints"),
        )
    )
    body = f"""
<style>
.compiler-note {{ border-left:4px solid var(--cyan); padding:11px 14px; background:#12334b66; }}
.tabs {{ display:flex; gap:8px; flex-wrap:wrap; margin:16px 0; position:sticky; top:0; z-index:5; padding:9px; background:#07111ff2; }}
.tab.active,.plan-button.active {{ border-color:var(--cyan); color:var(--cyan); }}
.view {{ display:none; }} .view.active {{ display:block; }}
.plan-track {{ display:flex; align-items:stretch; gap:6px; overflow-x:auto; padding:12px 0; }}
.plan-button {{ flex:1 0 145px; text-align:left; min-height:76px; }}
.plan-button strong {{ display:block; font-size:17px; }}
.interval-controls {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
.summary-grid {{ grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); }}
.summary-card strong {{ font-size:22px; display:block; }}
.operation-stream {{ display:grid; gap:16px; }}
.operation-group {{ border-top:3px solid var(--cyan); }}
.operation-line {{ padding:12px 14px; margin:9px 0; border-radius:9px; background:#10243a; border-left:4px solid var(--pass); overflow-wrap:anywhere; }}
.operation-line.retire,.operation-line.remove {{ border-left-color:var(--fail); }}
.operation-line.change,.operation-line.sparse {{ border-left-color:var(--warn); }}
.opcode {{ display:inline-block; font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-weight:800; color:var(--cyan); margin-right:8px; }}
.attachment-line {{ margin:6px 0 0 18px; color:#c7d8e8; }}
.dependency-summary {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
.lowering-row {{ display:grid; grid-template-columns:minmax(250px,1fr) 54px minmax(320px,1.35fr); gap:10px; align-items:stretch; margin:12px 0; }}
.semantic,.symbolic {{ background:#0a192a; border:1px solid var(--line); border-radius:10px; padding:13px; overflow-wrap:anywhere; }}
.semantic {{ border-left:4px solid var(--blue); }} .symbolic {{ border-left:4px solid var(--pass); }}
.lowering-arrow {{ display:grid; place-items:center; color:var(--cyan); font-size:30px; }}
.intent {{ padding:9px 11px; margin:7px 0; border-radius:8px; background:#112943; overflow-wrap:anywhere; }}
.intent.initial {{ border-left:4px solid var(--cyan); }} .intent.sparse {{ border-left:4px solid var(--warn); }}
.intent.same-hop {{ border:2px solid var(--warn); background:#493b1938; }}
.badge {{ display:inline-block; padding:2px 8px; border-radius:99px; background:#244662; margin:2px 4px 2px 0; font-size:12px; font-weight:750; }}
.badge.changed {{ color:#2f2300; background:var(--warn); }}
.badge.added,.badge.establish {{ color:#042514; background:var(--pass); }}
.badge.removed,.badge.withdraw {{ color:#310707; background:var(--fail); }}
.canonical {{ display:block; color:var(--muted); font-size:11px; overflow-wrap:anywhere; white-space:normal; }}
.connection-card {{ margin:12px 0; }} .direction-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
.constraint {{ display:grid; grid-template-columns:1fr 45px 1fr; gap:8px; align-items:center; margin:8px 0; padding:10px; background:#10243a; border-radius:9px; }}
.constraint-arrow {{ text-align:center; color:#e879f9; font-size:25px; }}
.state-section {{ margin:12px 0; }} .scroll {{ overflow-x:auto; }}
table {{ min-width:650px; }}
@media(max-width:850px) {{ .lowering-row {{ grid-template-columns:1fr; }} .lowering-arrow {{ transform:rotate(90deg); }} .direction-grid,.constraint {{ grid-template-columns:1fr; }} .constraint-arrow {{ transform:rotate(90deg); }} }}
</style>
<section class="card">
  <span class="status PASS">COMPILED OUTPUT OBSERVED</span>
  <h2>Compiled backend operations / symbolic BPF realization</h2>
  <p>This page reads already-produced <code>Plan</code> and
  <code>BpfCompiledRealization</code> values. It does not compile, lower,
  query Simulator state, or infer concrete runtime/kernel bindings.</p>
</section>
<section><h2>Inspection inventory</h2><div class="grid stats">{stats}</div></section>
<section class="card compiler-note">
  <strong>Review boundary:</strong> exact mapping is shown only where current
  compiled fields support it. Zone-edge rows are mechanical Compiler output
  and do not claim a nonexistent Plan transition provenance.
</section>
<section class="card">
  <h2>Continuous Plan intervals</h2>
  <div class="plan-track" id="plan-timeline"></div>
  <div class="interval-controls">
    <label>Selected interval <select id="plan-select"></select></label>
    <span id="coverage-label" class="badge"></span>
  </div>
  <div class="grid summary-grid" id="interval-summary"></div>
</section>
<nav class="tabs">
  <button class="tab active" data-view="operations">Backend operations</button>
  <button class="tab" data-view="lowering">Plan → BPF provenance</button>
  <button class="tab" data-view="resources">Connections / resources</button>
  <button class="tab" data-view="parameters">Parameters / qdisc</button>
  <button class="tab" data-view="forwarding">Forwarding</button>
  <button class="tab" data-view="zones">Zone edges</button>
  <button class="tab" data-view="constraints">Ordering</button>
  <button class="tab" data-view="end-state">Intended end state</button>
</nav>
<section class="view active" id="view-operations"><div id="backend-operations"></div></section>
<section class="view" id="view-lowering"><div id="lowering-list"></div></section>
<section class="view" id="view-resources"><div id="resource-list"></div></section>
<section class="view" id="view-parameters"><div id="parameter-list"></div></section>
<section class="view" id="view-forwarding"><div id="forwarding-list"></div></section>
<section class="view" id="view-zones"><div id="zone-list"></div></section>
<section class="view" id="view-constraints"><div id="constraint-list"></div></section>
<section class="view" id="view-end-state"><div id="end-state"></div></section>
<details class="card"><summary>Raw deterministic inspection snapshot</summary><pre id="raw-json"></pre></details>
"""
    return standalone_page(
        title="BPF Compiler Inspection Tool",
        eyebrow=eyebrow,
        body=body,
        script=_SCRIPT.replace("__BPF_DATA__", json_for_script(data)),
        language_strings={
            "BPF Compiler Inspection Tool": "BPF Compiler 检查工具",
            eyebrow: f"{inventory['plans']} 个连续 Plan · 离线 · 只读",
            "COMPILED OUTPUT OBSERVED": "已观测编译输出",
            "Compiled backend operations / symbolic BPF realization": "已编译 backend operation / 符号化 BPF realization",
            "This page reads already-produced Plan and BpfCompiledRealization values. It does not compile, lower, query Simulator state, or infer concrete runtime/kernel bindings.": "本页读取已经产生的 Plan 与 BpfCompiledRealization；不会执行编译、lowering、查询 Simulator state 或推断具体 runtime / kernel binding。",
            "Inspection inventory": "检查内容概览",
            "Review boundary:": "审核边界：",
            "exact mapping is shown only where current compiled fields support it. Zone-edge rows are mechanical Compiler output and do not claim a nonexistent Plan transition provenance.": "只有当前 compiled field 支持时才展示精确映射。Zone-edge 行是 Compiler 的机械输出，不会声称不存在的 Plan transition provenance。",
            "Continuous Plan intervals": "连续 Plan 区间",
            "Selected interval": "所选区间",
            "Backend operations": "Backend operation",
            "Plan → BPF provenance": "Plan → BPF provenance",
            "Connections / resources": "Connection / resource",
            "Parameters / qdisc": "参数 / qdisc",
            "Forwarding": "转发",
            "Zone edges": "Zone edge",
            "Ordering": "排序约束",
            "Intended end state": "预期结束状态",
            "Raw deterministic inspection snapshot": "原始确定性检查快照",
            "Plan intervals": "Plan 区间",
            "Resources": "资源",
            "Qdisc intents": "Qdisc intent",
            "Constraints": "约束",
            "No values in this interval.": "此区间中没有值。",
            "Initial complete target": "初始完整目标",
            "Sparse parameter update": "稀疏参数更新",
            "Direction": "方向",
            "RouteKey lowering": "RouteKey lowering",
            "Previous next hop": "此前 next hop",
            "New next hop": "新的 next hop",
            "Resolved symbolic egress": "已解析符号 egress",
            "Interval Zone-edge changes": "区间 Zone-edge 变化",
            "Planned Zone edge at interval end": "区间结束时的 planned Zone edge",
            "No compiled Zone-edge change.": "没有 compiled Zone-edge 变化。",
            "No active Zone edge.": "没有 active Zone edge。",
            "Plan forwarding precedence": "Plan forwarding precedence",
            "Exact semantic precedence retained by Compiler.": "Compiler 保留的精确语义 precedence。",
            "Backend technical constraints": "Backend 技术约束",
            "Mechanical readiness, use, retirement, and Zone-edge ordering.": "机械 readiness、use、retirement 与 Zone-edge 排序。",
            "Active logical resources": "Active 逻辑资源",
            "Directional parameter state": "方向参数状态",
            "Forwarding state": "转发状态",
            "Zone edges": "Zone edge",
            "Connection": "Connection",
            "Endpoint A": "端点 A",
            "Endpoint B": "端点 B",
            "Bandwidth": "带宽",
            "Delay": "时延",
            "Loss": "丢包",
            "Source": "源节点",
            "Target": "目标",
            "Next hop": "下一跳",
            "Synthetic identity": "合成 identity",
            "Planned terminal": "Planned terminal",
        },
    )


_SCRIPT = r"""
const DATA=__BPF_DATA__;
const esc=value=>String(value??"—").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const aliases=DATA.display_aliases;
const node=value=>value===null||value===undefined?"—":aliases.nodes[value]||String(value);
const zone=value=>aliases.zones[value]||`Z:${value}`;
const canonical=value=>`<code class="canonical">${esc(value)}</code>`;
const operation=value=>`<span class="badge ${esc(value)}">${esc(String(value).toUpperCase())}</span>`;
const connection=value=>`${esc(node(value.endpoint_a))} ↔ ${esc(node(value.endpoint_b))}${canonical(`${value.kind}|${value.endpoint_a}|${value.endpoint_b}`)}`;
const direction=value=>`${esc(node(value.source))} → ${esc(node(value.target))}`;
const target=value=>value.kind==="zone"?zone(value.zone_id):node(value.ground_station_id);
const route=value=>`${esc(node(value.source))} → ${esc(target(value.target))}${canonical(value.source)}`;
const fmt=value=>value===null?"synthetic baseline":String(value).replace("T"," ").replace("+00:00","Z");
const empty=message=>`<div class="card"><p class="muted">${esc(message)}</p></div>`;
const table=(headers,rows)=>rows.length?`<div class="scroll"><table><thead><tr>${headers.map(value=>`<th>${esc(value)}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(value=>`<td>${esc(value)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`:'<p class="muted">No values in this interval.</p>';
const parameterTargets=targets=>["bandwidth_bps","delay_us","packet_loss_ppm"].map(field=>`<span class="badge">${esc(field)}: ${targets[field]===null||targets[field]===undefined?"unchanged":esc(targets[field])}</span>`).join("");
const binding=value=>value?`${connection(value.connection_id)}<p><strong>Next hop</strong> ${esc(node(value.next_hop))}<br><strong>Direction</strong> ${esc(value.direction)}<br><strong>Packet identity</strong> ${esc(value.packet_prefix??"not applicable")}</p>`:'<span class="muted">No binding</span>';
let selected=0;
const current=()=>DATA.intervals[selected];

const planSelect=document.getElementById("plan-select");
planSelect.innerHTML=DATA.intervals.map(value=>`<option value="${value.plan_index}">${esc(value.label)} ${esc(value.coverage)}</option>`).join("");
document.getElementById("plan-timeline").innerHTML=DATA.intervals.map(value=>`<button class="plan-button" data-plan="${value.plan_index}"><strong>${esc(value.label)}</strong><span>${esc(value.coverage)}</span><br><span class="muted">${fmt(value.start_timestamp)} → ${fmt(value.end_timestamp)}</span></button>`).join("");
document.querySelectorAll(".plan-button").forEach(button=>button.addEventListener("click",()=>{planSelect.value=button.dataset.plan;selected=Number(button.dataset.plan);renderAll();}));
planSelect.addEventListener("change",()=>{selected=Number(planSelect.value);renderAll();});

document.querySelectorAll(".tab").forEach(button=>button.addEventListener("click",()=>{
  document.querySelectorAll(".tab").forEach(value=>value.classList.toggle("active",value===button));
  document.querySelectorAll(".view").forEach(value=>value.classList.toggle("active",value.id===`view-${button.dataset.view}`));
}));

function renderSummary(interval){
  const s=interval.summary;
  const cards=[
    ["Resources",`${s.resources_establish} establish · ${s.resources_retire} retire`,`${s.resources_active} active at end`],
    ["Parameters",`${s.parameter_changes} qdisc intents`,"initial complete + sparse updates"],
    ["Forwarding",`${s.forwarding_add} add · ${s.forwarding_change} change · ${s.forwarding_remove} remove`,"formal operations retained"],
    ["Zones",`${s.zone_changes} changes`,`${s.zone_edges_active} active edges at end`],
    ["Constraints",`${s.plan_precedence} Plan precedence`,`${s.technical_constraints} backend technical`]
  ];
  document.getElementById("interval-summary").innerHTML=cards.map(([label,value,detail])=>`<div class="card summary-card"><span class="muted">${esc(label)}</span><strong>${esc(value)}</strong><span>${esc(detail)}</span></div>`).join("");
}

function resourceOperation(value){
  const verb=value.operation==="establish"?"RESOURCE ESTABLISH":"RESOURCE RETIRE";
  const style=value.operation==="establish"?"":"retire";
  const attachmentVerb=value.operation==="establish"?"ATTACHMENT REQUIRE":"ATTACHMENT RELEASE";
  const attachments=value.attachments.map(item=>`<div class="attachment-line"><span class="opcode">${attachmentVerb}</span>${direction(item)} · ${esc(item.direction)}</div>`).join("");
  return `<div class="operation-line ${style}"><span class="opcode">${verb}</span>${connection(value.connection_id)}<p>One logical bidirectional resource.</p>${attachments}${canonical(value.intent_id)}</div>`;
}
function qdiscOperation(value){
  const initial=value.reason==="connection-establish";
  return `<div class="operation-line ${initial?"":"sparse"}"><span class="opcode">${initial?"QDISC TARGET INITIAL":"QDISC UPDATE SPARSE"}</span>${direction(value)}<p>${connection(value.connection_id)}</p>${parameterTargets(value.targets)}${canonical(value.intent_id)}</div>`;
}
function forwardingOperation(value){
  const verbs={added:"FORWARD ADD",changed:"FORWARD CHANGE",removed:"FORWARD REMOVE"};
  const bindingValue=value.current??value.previous;
  const egressLabel=value.operation==="removed"?"Released symbolic egress":"Resolved symbolic egress";
  const special=value.same_next_hop_changed?'<span class="badge changed">SAME-NEXT-HOP CHANGED — NOT A NO-OP</span>':'';
  return `<div class="operation-line ${value.operation==='removed'?'remove':value.operation==='changed'?'change':''}"><span class="opcode">${verbs[value.operation]}</span>${special}${route(value.route_key)}<div class="grid two"><div><strong>Previous next hop</strong><br>${esc(node(value.previous?.next_hop??null))}</div><div><strong>New next hop</strong><br>${esc(node(value.current?.next_hop??null))}</div></div><h3>${egressLabel}</h3>${binding(bindingValue)}${canonical(value.intent_id)}</div>`;
}
function zoneOperation(value){
  const verbs={added:"ZONE EDGE ACTIVATE",changed:"ZONE EDGE RETARGET",removed:"ZONE EDGE REMOVE"};
  return `<div class="operation-line ${value.operation==='removed'?'remove':value.operation==='changed'?'change':''}"><span class="opcode">${verbs[value.operation]}</span><strong>${esc(zone(value.zone_id))}</strong><p>Symbolic packet identity <strong>${esc(value.packet_prefix)}</strong><br>Previous terminal ${esc(node(value.previous_service_satellite))}<br>Planned terminal ${esc(node(value.service_satellite))}</p>${canonical(value.intent_id)}</div>`;
}
function operationGroup(title,description,values,renderer){
  return `<section class="card operation-group"><h2>${esc(title)}</h2><p class="muted">${esc(description)}</p>${values.length?values.map(renderer).join(""):'<p class="muted">No operations in this interval.</p>'}</section>`;
}
function renderBackendOperations(interval){
  const compiled=interval.compiled;
  const planConstraints=interval.constraint_groups["plan-forwarding-precedence"];
  const technicalConstraints=interval.constraint_groups["backend-technical"];
  document.getElementById("backend-operations").innerHTML=`<section class="card compiler-note"><strong>BpfCompiledRealization operation inventory.</strong><br>Read these symbolic operations without consulting Plan provenance. Tuple presentation is not an execution order; only the dependency relationships below express ordering.</section><div class="operation-stream">${operationGroup("Resource lifecycle + attachments","Logical resource establish/retire with its real directional symbolic attachment requirements.",compiled.resources,resourceOperation)}${operationGroup("Directional qdisc operations","Initial complete targets and later sparse updates, exactly as compiled.",compiled.parameters,qdiscOperation)}${operationGroup("Forwarding operations","Add/change/remove plus the compiled resolved or released symbolic egress.",compiled.forwarding,forwardingOperation)}${operationGroup("Zone-edge operations","Synthetic Zone packet identity and planned service terminal activation, retarget, or removal.",compiled.zone_edges,zoneOperation)}<section class="card operation-group"><h2>Backend ordering / dependency relationships</h2><div class="grid dependency-summary"><div><strong>${planConstraints.length}</strong><br>retained Plan forwarding precedence</div><div><strong>${technicalConstraints.length}</strong><br>backend lifecycle dependencies</div></div><details><summary>Retained Plan forwarding precedence</summary>${constraintRows(planConstraints)}</details><details><summary>Backend technical dependencies</summary>${constraintRows(technicalConstraints)}</details></section></div>`;
}

function semanticHtml(row){
  const value=row.plan_transition;
  if(value===null)return `<h3>No direct Plan transition claimed</h3><p class="muted">Zone edge is a mechanical compiled realization.</p>`;
  if(row.family==="connection")return `<h3>Plan · Connection</h3>${operation(value.operation)}${connection(value.connection_id)}<p>Nominal ${fmt(value.nominal_time)}</p>`;
  if(row.family==="parameter")return `<h3>Plan · Parameter</h3>${connection(value.connection_id)}<p>${esc(value.direction)} · ${fmt(value.nominal_time)}</p>${parameterTargets(value.targets)}`;
  return `<h3>Plan · Forwarding</h3>${operation(value.operation)}${route(value.route_key)}<p><strong>Plan next hop</strong> ${esc(node(value.next_hop))}<br>Nominal ${fmt(value.nominal_time)}</p>`;
}
function intentHtml(value){
  if(value.intent_id.includes("-resource-"))return `<div class="intent"><strong>Symbolic resource demand</strong> ${operation(value.operation)}${connection(value.connection_id)}<p>One logical Connection; ${value.attachments.length} directional attachment requirements.</p></div>`;
  if(value.intent_id.includes("-parameter-"))return `<div class="intent ${value.reason==="connection-establish"?"initial":"sparse"}"><strong>Directional qdisc intent</strong><br>${direction(value)}<br>${parameterTargets(value.targets)}<p class="muted">${value.reason==="connection-establish"?"Initial complete target":"Sparse ParameterTransition update"}</p></div>`;
  if(value.intent_id.includes("-forwarding-"))return forwardingHtml(value);
  return `<div class="intent"><strong>${esc(zone(value.zone_id))}</strong> ${operation(value.operation)}<p>Synthetic identity <strong>${esc(value.packet_prefix)}</strong><br>Planned terminal ${esc(node(value.service_satellite))}</p></div>`;
}
function renderLowering(interval){
  document.getElementById("lowering-list").innerHTML=interval.lowering.length?interval.lowering.map(row=>`<div class="lowering-row"><div class="semantic">${semanticHtml(row)}</div><div class="lowering-arrow">→</div><div class="symbolic"><h3>BPF Compiler lowering</h3>${row.compiled_intents.length?row.compiled_intents.map(intentHtml).join(""):'<p class="muted">No compiled intent mapped to this semantic transition.</p>'}<span class="canonical">mapping: ${esc(row.mapping)}</span></div></div>`).join(""):empty("No Plan or compiled changes in this interval.");
}

function renderResources(interval){
  const values=interval.compiled.resources;
  document.getElementById("resource-list").innerHTML=values.length?values.map(value=>`<div class="card connection-card"><h2>${operation(value.operation)} Logical Connection</h2>${connection(value.connection_id)}<p><strong>Backend requirement:</strong> dynamic bidirectional symbolic attachment.</p><div class="grid direction-grid">${value.attachments.map(item=>`<div class="intent"><strong>${esc(item.direction)}</strong><br>${direction(item)}</div>`).join("")}</div><p class="muted">These directions belong to one logical Connection, not two physical Connections.</p></div>`).join(""):empty("No resource lifecycle intent in this interval.");
}

function renderParameters(interval){
  const values=interval.compiled.parameters;
  document.getElementById("parameter-list").innerHTML=values.length?values.map(value=>`<div class="card"><h2>${value.reason==="connection-establish"?"Initial complete target":"Sparse parameter update"}</h2>${connection(value.connection_id)}<p><strong>Direction</strong> ${direction(value)} · ${fmt(value.nominal_time)}</p>${parameterTargets(value.targets)}<p class="muted">${esc(value.reason)}</p></div>`).join(""):empty("No qdisc intent in this interval.");
}

function forwardingHtml(value){
  const oldHop=value.previous?.next_hop??null,newHop=value.current?.next_hop??null;
  const special=value.same_next_hop_changed?'<span class="badge changed">SAME-NEXT-HOP CHANGED — NOT A NO-OP</span>':'';
  return `<div class="intent ${value.same_next_hop_changed?"same-hop":""}">${operation(value.operation)}${special}${route(value.route_key)}<div class="grid two"><div><strong>Previous next hop</strong><br>${esc(node(oldHop))}</div><div><strong>New next hop</strong><br>${esc(node(newHop))}</div></div><h3>Resolved symbolic egress</h3>${binding(value.current??value.previous)}</div>`;
}
function renderForwarding(interval){
  const values=interval.compiled.forwarding;
  document.getElementById("forwarding-list").innerHTML=values.length?values.map(value=>`<div class="card"><h2>RouteKey lowering</h2>${forwardingHtml(value)}</div>`).join(""):empty("No forwarding realization in this interval.");
}

function renderZones(interval){
  const changes=interval.compiled.zone_edges,state=interval.end_state.zone_edges;
  document.getElementById("zone-list").innerHTML=`<div class="grid two"><div class="card"><h2>Interval Zone-edge changes</h2>${changes.length?changes.map(intentHtml).join(""):'<p class="muted">No compiled Zone-edge change.</p>'}</div><div class="card"><h2>Planned Zone edge at interval end</h2>${state.length?state.map(value=>`<div class="intent"><strong>${esc(zone(value.zone_id))}</strong><p>Synthetic identity ${esc(value.packet_prefix)}<br>Planned terminal ${esc(node(value.service_satellite))}</p></div>`).join(""):'<p class="muted">No active Zone edge.</p>'}</div></div><p class="compiler-note">This view uses only compiled Zone-edge values. It does not scan raw Slice assignments.</p>`;
}

function constraintRows(values){return values.length?values.map(value=>`<div class="constraint"><div><strong>${esc(value.predecessor.label)}</strong>${canonical(value.predecessor.intent_id)}</div><div class="constraint-arrow">↓</div><div><strong>${esc(value.successor.label)}</strong>${canonical(value.successor.intent_id)}<span class="badge">${esc(value.reason)}</span></div></div>`).join(""):'<p class="muted">None in this interval.</p>';}
function renderConstraints(interval){
  document.getElementById("constraint-list").innerHTML=`<div class="grid two"><div class="card"><h2>Plan forwarding precedence</h2><p>Exact semantic precedence retained by Compiler.</p>${constraintRows(interval.constraint_groups["plan-forwarding-precedence"])}</div><div class="card"><h2>Backend technical constraints</h2><p>Mechanical readiness, use, retirement, and Zone-edge ordering.</p>${constraintRows(interval.constraint_groups["backend-technical"])}</div></div>`;
}

function renderEndState(interval){
  const state=interval.end_state;
  document.getElementById("end-state").innerHTML=`<section class="card compiler-note"><strong>Previous intended baseline + interval work → new intended end state.</strong><br>This immutable result is the next deterministic compilation baseline, not a long-lived state authority.</section><div class="grid two"><div class="card state-section"><h2>Active logical resources</h2>${table(["Connection","Endpoint A","Endpoint B"],state.resources.map(value=>[value.connection_id.kind,node(value.connection_id.endpoint_a),node(value.connection_id.endpoint_b)]))}</div><div class="card state-section"><h2>Directional parameter state</h2>${table(["Connection","Direction","Bandwidth","Delay","Loss"],state.parameters.map(value=>[value.connection_ref,value.direction,value.targets.bandwidth_bps,value.targets.delay_us,value.targets.packet_loss_ppm]))}</div><div class="card state-section"><h2>Forwarding state</h2>${table(["Source","Target","Next hop","Connection","Direction"],state.forwarding.map(value=>[node(value.route_key.source),target(value.route_key.target),node(value.next_hop),value.connection_ref,value.direction]))}</div><div class="card state-section"><h2>Zone edges</h2>${table(["Zone","Synthetic identity","Planned terminal"],state.zone_edges.map(value=>[zone(value.zone_id),value.packet_prefix,node(value.service_satellite)]))}</div></div>`;
}

function renderAll(){
  const interval=current();
  planSelect.value=String(selected);
  document.querySelectorAll(".plan-button").forEach(value=>value.classList.toggle("active",Number(value.dataset.plan)===selected));
  document.getElementById("coverage-label").textContent=`${interval.label} ${interval.coverage}`;
  renderSummary(interval);renderBackendOperations(interval);renderLowering(interval);renderResources(interval);renderParameters(interval);renderForwarding(interval);renderZones(interval);renderConstraints(interval);renderEndState(interval);
}
document.getElementById("raw-json").textContent=JSON.stringify(DATA,null,2);
renderAll();
"""


__all__ = ["render_bpf_compiler_page"]
