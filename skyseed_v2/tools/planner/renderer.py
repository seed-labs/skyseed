"""Render final Planner inspection view data as one offline HTML page."""

from __future__ import annotations

from html import escape
from typing import Any

from tools._html import standalone_page


def render_planner_page(data: dict[str, Any]) -> str:
    """Return the primary human-review artifact without formal object reads."""

    if not isinstance(data, dict) or data.get("tool") != "planner-inspection":
        raise TypeError("data must be final Planner inspection view data")
    inventory = data["inventory"]
    configuration = data["configuration"]
    scenario_display_name = str(
        configuration.get("scenario_display_name")
        or configuration.get("scenario_identity")
        or "Unnamed scenario"
    )
    scenario_heading = f"{scenario_display_name} · Planner inspection"
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
body {{ overflow-anchor:none; }}
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
.spatial-grid {{ display:grid; grid-template-columns:minmax(0,2fr) minmax(240px,.65fr); gap:12px; align-items:start; }}
.globe {{ width:100%; min-height:420px; background:#06111d; border-radius:9px; touch-action:none; }}
.spatial-controls {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
.spatial-side.active {{ border-color:var(--cyan); color:var(--cyan); }}
#spatial-detail {{ max-height:520px; overflow:auto; padding-right:5px; }}
.playback-controls {{ display:grid; grid-template-columns:auto auto auto minmax(180px,1fr) auto auto; gap:8px; align-items:center; margin:10px 0; }}
.playback-controls input[type="range"] {{ width:100%; }}
.playback-status {{ grid-column:1/-1; color:var(--muted); }}
.parameter-axis {{ fill:#a9bfd3; font-size:11px; }}
.parameter-grid {{ stroke:#294966; stroke-width:1; }}
.parameter-legend {{ display:flex; flex-wrap:wrap; gap:14px; color:var(--muted); margin-top:7px; }}
.parameter-legend i {{ display:inline-block; width:18px; height:3px; margin-right:5px; vertical-align:3px; }}
.visual-encoding-legend {{ display:flex; flex-wrap:wrap; gap:8px 15px; margin:8px 0 13px; padding:8px 10px; border:1px solid var(--line); border-radius:8px; color:var(--muted); font-size:12px; }}
.visual-encoding-legend span {{ white-space:nowrap; }}
.encoding-mark {{ display:inline-block; width:18px; height:4px; margin-right:5px; vertical-align:3px; background:#69a9ff; }}
.encoding-mark.planned {{ background:#5dff8b; }}
.encoding-mark.shared {{ background:#e879f9; height:5px; }}
.encoding-mark.formal-only {{ background:#69a9ff; }}
.encoding-mark.planned-only {{ height:0; border-top:3px dashed #5dff8b; background:transparent; }}
.encoding-mark.nominal {{ width:3px; height:17px; background:#ffd166; vertical-align:-4px; }}
.encoding-mark.sample-boundary {{ width:3px; height:17px; background:#69a9ff; vertical-align:-4px; }}
.encoding-mark.parameter-formal {{ width:9px; height:9px; border-radius:50%; background:#69a9ff; }}
.encoding-mark.parameter-planned {{ background:#5dff8b; }}
.encoding-mark.parameter-transition {{ width:9px; height:9px; border-radius:50%; background:#ffd166; }}
.zero {{ color:var(--muted); }} .domain-count {{ display:inline-block; margin:4px 12px 4px 0; }}
.event-groups {{ display:grid; grid-template-columns:repeat(3,minmax(190px,1fr)); gap:9px; height:290px; overflow:hidden; }}
.event-group {{ min-width:0; }} .event-list {{ height:190px; overflow-y:auto; }}
#nominal-events {{ min-height:350px; }}
.event-button {{ width:100%; margin:3px 0; padding:6px 8px; text-align:left; overflow-wrap:anywhere; }}
.event-button.active {{ border-color:var(--cyan); color:var(--cyan); }}
.summary-flow {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(175px,1fr)); gap:10px; margin:12px 0; }}
.summary-node {{ border:1px solid var(--line); border-radius:9px; padding:11px; background:#0a192a; min-width:0; cursor:pointer; }}
.summary-node strong {{ display:block; font-size:24px; margin:4px 0; }}
.ratio-track {{ height:9px; border-radius:99px; background:#07111f; overflow:hidden; margin-top:7px; }}
.ratio-fill {{ display:block; height:100%; background:linear-gradient(90deg,var(--cyan),var(--pass)); }}
.distribution-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:9px; }}
.distribution-card {{ padding:10px; border-left:4px solid #e879f9; background:#151b32; border-radius:6px; }}
.plan-bar.selected {{ border:2px solid #fff; background:#3879c7cc; }}
.decision-marker {{ background:#e879f9; color:#fff; }}
.open-marker {{ position:absolute; top:5px; transform:translateX(-100%); color:#fff; font-weight:900; }}
.visual-primary {{ border:1px solid var(--line); border-radius:9px; padding:12px; background:#091827; }}
table td,table th,select,option,h2,h3,p {{ overflow-wrap:anywhere; }}
@media(max-width:850px) {{ .split,.spatial-grid,.event-groups,.playback-controls {{ grid-template-columns:1fr; }} .event-groups {{ height:auto;overflow:visible; }} .dependency-chain {{ grid-template-columns:1fr; }} .dependency-edge {{ transform:rotate(90deg); }} }}
</style>
<section class="card">
  <span class="status PASS">FORMAL OUTPUT OBSERVED</span>
  <h2 id="scenario-title">{escape(scenario_heading)}</h2>
  <p>This page compares already-produced Slicer/Difference facts with immutable
  Plan publications and retained PlanningDecision evidence. It does not run a
  Model, recompute a Difference, or infer an expected Plan.</p>
  <h3>Current Planner composition</h3>
  <p>Connection planning converts formal Connection evolution into planned episodes; Parameter planning decides which formal field changes require published targets; Forwarding planning chooses FOLLOW or retained planned paths; Coordination keeps the cross-family result dependency-safe. The planning window selects the immutable Difference scope consumed by each publication. The cards below show the concrete models, policy, and window used for this run.</p>
  <div class="config-grid" id="configuration"></div>
</section>
<section><h2>Inspection inventory</h2><div class="grid stats">{stats}</div></section>
<section class="card planner-note">
  <strong>Sampled timeline ≠ publication timeline.</strong>
  Plans cover continuous <code>(start,end]</code> segments. Same-time markers
  are grouped; tuple order is not runtime execution order. Purple precedence
  relations are separate same-time semantics.
  <p><strong>Formal sampled</strong> means an exact retained Slicer sample. <strong>Planned</strong> means immutable state published by Planner. A nominal transition may lie between samples and does not invent an interpolated formal position. In Forwarding detail, Q is the current formal Slicer path and P is the current planned path; no third Route is constructed.</p>
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
    <div class="playback-controls" aria-label="Full-run spatial playback">
      <button id="playback-previous" type="button">Previous</button>
      <button id="playback-toggle" type="button">Play</button>
      <button id="playback-next" type="button">Next</button>
      <input id="playback-scrubber" type="range" min="0" value="0" step="1" aria-label="Nominal event group">
      <label>Speed <select id="playback-speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select></label>
      <label>Globe <select id="playback-mode"><option value="overlay">Formal + planned overlay</option><option value="formal">Formal</option><option value="planned">Planned changes</option></select></label>
      <span id="playback-status" class="playback-status">Choose a nominal event group.</span>
    </div>
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
  <div class="visual-encoding-legend" aria-label="Planner visual encoding legend">
    <span><i class="encoding-mark"></i>Formal sampled state</span>
    <span><i class="encoding-mark planned"></i>Planned published state</span>
    <span><i class="encoding-mark shared"></i>Shared path edge</span>
    <span><i class="encoding-mark formal-only"></i>Formal-only path edge</span>
    <span><i class="encoding-mark planned-only"></i>Planned-only path edge</span>
    <span><i class="encoding-mark nominal"></i>Planner nominal transition</span>
    <span><i class="encoding-mark sample-boundary"></i>Formal sample boundary</span>
    <span><i class="encoding-mark parameter-formal"></i>Parameter formal sample</span>
    <span><i class="encoding-mark parameter-planned"></i>Parameter planned step</span>
    <span><i class="encoding-mark parameter-transition"></i>Published ParameterTransition</span>
  </div>
  <div class="spatial-grid">
    <canvas class="globe" id="planner-globe" width="1100" height="520"></canvas>
    <div id="spatial-detail"></div>
  </div>
</section>
<section class="view active" id="view-overview">
  <div class="card"><h2>Planner transformation summary</h2><p class="muted" id="summary-basis"></p><div id="planner-overview-summary"></div><h3>Behavior distributions</h3><div id="planner-distributions" class="distribution-grid"></div></div>
  <div class="card scroll"><div class="timeline" id="global-timeline"></div></div>
  <div class="card"><h2 id="selected-plan-heading">Selected Plan</h2><div id="selected-plan-summary"></div><details><summary>Exact consecutive Plan table</summary><div id="plan-table"></div></details></div>
</section>
<section class="view" id="view-connections">
  <div class="split"><details class="card"><summary>Connection identity search / audit</summary><h2>Logical ConnectionId</h2><select class="listbox" id="connection-select" size="14"></select></details><div class="card" id="connection-detail"></div></div>
</section>
<section class="view" id="view-parameters">
  <div class="card parameter-summary" id="parameter-overview"></div>
  <div class="split"><details class="card"><summary>Parameter identity search / audit</summary><h2>Connection + direction + field</h2><select class="listbox" id="parameter-select" size="14"></select></details><div class="card" id="parameter-detail"></div></div>
</section>
<section class="view" id="view-forwarding">
  <div class="split"><details class="card"><summary>Route identity search / audit</summary><h2>RouteKey</h2><select class="listbox" id="route-select" size="14"></select></details><div class="card" id="route-detail"></div></div>
</section>
<section class="view" id="view-decisions">
  <div class="grid two"><div class="card"><h2>Recorded PlanningDecision values</h2><div id="decision-list"></div></div><div class="card"><h2>Inspection boundary</h2><div id="decision-boundary"></div></div></div>
</section>
<section class="view" id="view-comparison">
  <div class="card controls"><label>Difference <select id="difference-select"></select></label></div>
  <div class="grid two"><div class="card"><h2>Simulator / Difference</h2><div id="formal-comparison"></div></div><div class="card"><h2>Planner / published transitions</h2><div id="planned-comparison"></div></div></div>
</section>
<details class="card"><summary>Machine-readable artifact layout</summary><p><a href="planner-manifest.json">Compact manifest</a> · selected facts load from deterministic <code>chunks/</code>. No complete pretty-printed run is created in the browser.</p></details>
<script src="planner-manifest.js"></script>
"""
    return standalone_page(
        title="Planner Inspection Tool",
        eyebrow=eyebrow,
        body=body,
        script=_SCRIPT,
        language_strings={
            "Planner Inspection Tool": "Planner 检查工具",
            eyebrow: (
                f"{inventory['satellites']} 颗 Satellite · "
                f"{inventory['ground_stations']} 个 GroundStation · 只读"
            ),
            "FORMAL OUTPUT OBSERVED": "已观测正式输出",
            "This page compares already-produced Slicer/Difference facts with immutable Plan publications and retained PlanningDecision evidence. It does not run a Model, recompute a Difference, or infer an expected Plan.": "本页比较已经产生的 Slicer / Difference 事实、不可变 Plan 发布和保留的 PlanningDecision evidence；不会运行 Model、重新计算 Difference 或推断预期 Plan。",
            "Current Planner composition": "当前 Planner 组合",
            "Connection planning converts formal Connection evolution into planned episodes; Parameter planning decides which formal field changes require published targets; Forwarding planning chooses FOLLOW or retained planned paths; Coordination keeps the cross-family result dependency-safe. The planning window selects the immutable Difference scope consumed by each publication. The cards below show the concrete models, policy, and window used for this run.": "Connection planning 把正式 Connection 演化转换为 planned episode；Parameter planning 决定哪些正式字段变化需要发布目标；Forwarding planning 选择 FOLLOW 或保留既有 planned path；Coordination 保证跨 family 结果满足依赖安全。Planning window 决定每次发布消费的不可变 Difference 范围。下方卡片展示本次运行实际使用的模型、策略和窗口。",
            "Inspection inventory": "检查内容概览",
            "Sampled timeline ≠ publication timeline.": "采样时间轴 ≠ 发布时间轴。",
            "Plans cover continuous (start,end] segments. Same-time markers are grouped; tuple order is not runtime execution order. Purple precedence relations are separate same-time semantics.": "Plan 覆盖连续的 (start,end] 区间。同一时刻的标记会分组；tuple 顺序不是 runtime 执行顺序。紫色 precedence 关系是独立的同一时刻语义。",
            "Formal sampled means an exact retained Slicer sample. Planned means immutable state published by Planner. A nominal transition may lie between samples and does not invent an interpolated formal position. In Forwarding detail, Q is the current formal Slicer path and P is the current planned path; no third Route is constructed.": "Formal sampled 表示精确保留的 Slicer 样本。Planned 表示 Planner 发布的不可变状态。Nominal transition 可位于两个样本之间，但不会创造插值的正式位置。在 Forwarding 详情中，Q 是当前正式 Slicer 路径，P 是当前 planned 路径；不会构造第三条 Route。",
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
            "Machine-readable artifact layout": "机器可读 artifact 布局",
            "Scenario": "场景",
            "Sampling": "采样",
            "Planning window": "planning window",
            "Publication": "发布",
            "Connection timing": "Connection 时间",
            "Coordination": "协调",
            "Parameter thresholds": "参数阈值",
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
const DATA=window.SKYSEED_PLANNER_MANIFEST;
window.__SKYSEED_PLANNER_CHUNKS__=window.__SKYSEED_PLANNER_CHUNKS__||{};
const pendingChunks=new Map();
async function decodeExactChunk(value){if(!value||value.encoding!=="gzip-base64-json")return value;if(typeof DecompressionStream!=="function")throw new Error("This browser lacks gzip chunk support");const binary=atob(value.data),bytes=Uint8Array.from(binary,ch=>ch.charCodeAt(0)),stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip")),text=await new Response(stream).text();if(new TextEncoder().encode(text).length!==value.uncompressed_bytes)throw new Error("Planner chunk size audit failed");return JSON.parse(text);}
function loadChunk(path){const known=window.__SKYSEED_PLANNER_CHUNKS__[path];if(known!==undefined)return decodeExactChunk(known);let pending=pendingChunks.get(path);if(!pending){pending=new Promise((resolve,reject)=>{const element=document.createElement("script");element.src=path;element.onload=async()=>{const value=window.__SKYSEED_PLANNER_CHUNKS__[path];element.remove();if(value===undefined){reject(new Error(`Planner chunk ${path} did not register`));return;}try{resolve(await decodeExactChunk(value));}catch(error){reject(error);}};element.onerror=()=>reject(new Error(`Unable to load offline Planner chunk ${path}`));document.head.append(element);}).finally(()=>pendingChunks.delete(path));pendingChunks.set(path,pending);}return pending;}
function releaseChunk(path){delete window.__SKYSEED_PLANNER_CHUNKS__[path];}
const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const table=(headers,rows)=>rows.length?`<table><thead><tr>${headers.map(v=>`<th>${esc(v)}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table>`:'<p class="muted">No formal values in this selection.</p>';
const fmt=value=>value===null?"open":String(value).replace("T"," ").replace("+00:00","Z");
const aliases=DATA.display_aliases;
const zonesById=Object.fromEntries(DATA.topology.zones.map(value=>[value.zone_id,value]));
const nodeAlias=value=>value===null?"—":aliases.nodes[value]||String(value);
const zoneAlias=value=>aliases.zones[value]||`Z:${value}`;
const targetAlias=target=>target.kind==="G"?nodeAlias(target.ground_station_id):zoneAlias(target.zone_id);
function connectionFromRef(reference){const [kind,endpoint_a,endpoint_b]=String(reference).split("|");return {connection_ref:reference,connection_id:{kind,endpoint_a,endpoint_b}};}
function parameterFromKey(identity){const parts=String(identity).split("|"),field=parts.pop(),direction=parts.pop(),connection_ref=parts.join("|");return {parameter_key:identity,connection_ref,direction,field};}
function routeFromRef(reference){const [source,kind,...identityParts]=String(reference).split("|"),identity=identityParts.join("|"),target=kind==="G"?{kind,ground_station_id:identity}:{kind,zone_id:identity};return {route_ref:reference,source,target};}
const routeAlias=value=>{const group=typeof value==="string"?routeFromRef(value):value;return `${nodeAlias(group.source)} → ${targetAlias(group.target)}`;};
const connectionAlias=value=>{const connection=typeof value==="string"?connectionFromRef(value):value;return `${nodeAlias(connection.connection_id.endpoint_a)} ↔ ${nodeAlias(connection.connection_id.endpoint_b)}`;};
const canonical=value=>`<code class="canonical" title="${esc(value)}">${esc(value)}</code>`;
const loadedCatalogs=new Map(),pendingCatalogs=new Map();
function loadCatalog(name){if(loadedCatalogs.has(name))return Promise.resolve(loadedCatalogs.get(name));if(pendingCatalogs.has(name))return pendingCatalogs.get(name);const descriptors=DATA.catalogs[name].index_chunks;const pending=Promise.all(descriptors.map(value=>loadChunk(value.chunk))).then(pages=>{const entries=pages.flatMap(page=>page.flatMap(group=>group.identities.map(identity=>({identity,chunk:group.chunk}))));descriptors.forEach(value=>releaseChunk(value.chunk));loadedCatalogs.set(name,entries);return entries;}).catch(error=>{descriptors.forEach(value=>releaseChunk(value.chunk));throw error;}).finally(()=>pendingCatalogs.delete(name));pendingCatalogs.set(name,pending);return pending;}
const config=DATA.configuration;
const stableConnection=String(config.connection_model||"").startsWith("MidpointConnection");
const stableForwarding=String(config.forwarding_model||"").startsWith("StableForwarding");
const scenarioDisplayName=config.scenario_display_name||config.scenario_identity||"Unnamed scenario";
document.getElementById("scenario-title").textContent=`${scenarioDisplayName} · Planner inspection`;
const present=value=>value!==undefined&&value!==null&&value!=="";
const compactValue=value=>typeof value==="object"?JSON.stringify(value):String(value);
const configDetail=(keys,fallback="not configured")=>{
  const values=keys.filter(key=>present(config[key])).map(key=>`${key}: ${compactValue(config[key])}`);
  return values.length?values.join(" · "):fallback;
};
const realSliceCount=config.real_slice_count??config.qualification_target_slice_id??config.timeline_real_slice_count;
const publicationValue=present(config.publication_difference_count)
  ?`${config.publication_difference_count} Differences / Plan`
  :present(config.publication_horizon_seconds)
    ?`${config.publication_horizon_seconds} s horizon`
    :"model-selected publication window";
const configCards=[
  ["Scenario",scenarioDisplayName,config.scenario_identity],
  ["Sampling",`${config.slice_interval_seconds} s · ${realSliceCount} real Slices`,"formal retained Slices"],
  ["Planning window",config.planning_window_policy||config.planning_window||"model-selected",configDetail(["planning_window","planning_lookahead_seconds","planning_through_slice_id"])],
  ["Publication",publicationValue,configDetail(["publication_horizon_seconds"],"continuous (start,end] coverage")],
  ["Connection",config.connection_model||"unspecified",configDetail(["connection_timing","minimum_dwell_seconds","connection_episode_omission","connection_episode_candidate_max_seconds_by_kind"])],
  ["Parameter",config.parameter_model||"unspecified",configDetail(["parameter_stability","parameter_thresholds","parameter_delay_us","parameter_bundle_drifted_fields"])],
  ["Forwarding",config.forwarding_model||"unspecified",configDetail(["forwarding_stability"])],
  ["Coordination",config.coordination_policy||"unspecified",configDetail(["profile","profile_role"])]
];
document.getElementById("configuration").innerHTML=configCards.map(([label,value,detail])=>`<div><span class="muted">${esc(label)}</span><br><strong>${esc(value)}</strong><span class="canonical">${esc(detail)}</span></div>`).join("");
const parameterFields=["bandwidth_bps","delay_us","packet_loss_ppm"];
const formalParameterFields=DATA.parameter_overview.formal_fields;
const publishedParameterFields=DATA.parameter_overview.published_fields;
const formalParameterFieldCount=Object.values(formalParameterFields).reduce((total,value)=>total+value,0);
const publishedParameterFieldCount=Object.values(publishedParameterFields).reduce((total,value)=>total+value,0);
function flattenConfiguration(value,prefix=""){
  if(value===null||value===undefined)return [];
  if(typeof value!=="object")return [[prefix,value]];
  return Object.entries(value).flatMap(([name,child])=>flattenConfiguration(child,prefix?`${prefix}.${name}`:name));
}
const parameterConfigurationRows=[
  ...flattenConfiguration(config.parameter_thresholds,"parameter_thresholds"),
  ...flattenConfiguration(config.parameter_delay_us,"parameter_delay_us"),
  ...flattenConfiguration(config.parameter_bundle_drifted_fields,"parameter_bundle_drifted_fields")
];
document.getElementById("parameter-overview").innerHTML=`<h2>Formal observations vs published parameter targets</h2><div class="grid stats"><div class="card stat"><span class="muted">Formal parameter patches</span><strong>${DATA.parameter_overview.formal_patch_count}</strong></div><div class="card stat"><span class="muted">Published ParameterTransitions</span><strong>${DATA.parameter_overview.published_transition_count}</strong></div><div class="card stat"><span class="muted">Not individually published</span><strong>${Math.max(0,formalParameterFieldCount-publishedParameterFieldCount)}</strong></div></div><div class="grid two"><div><h3>Field breakdown</h3>${table(["Field","Formal patches","Published targets"],parameterFields.map(field=>[field,formalParameterFields[field],publishedParameterFields[field]]))}</div><div><h3>Configured Parameter policy</h3>${table(["Setting","Value"],parameterConfigurationRows)}</div></div><p><strong>Parameter planning:</strong> the configuration above is copied from the actual Tool input. Formal patches and published targets remain separate evidence; a patch not individually published is not by itself loss or error.</p><p><strong>Publication batching:</strong> ${realSliceCount} Differences → ${DATA.plans.length} Plans is a separate planning/publication-window concern. A Plan may retain multiple ParameterTransitions at distinct nominal times.</p>`;

const SUMMARY=DATA.planner_summary,whole=SUMMARY.whole_run;
const number=value=>value===null||value===undefined?"unavailable":Number(value).toLocaleString(undefined,{maximumFractionDigits:3});
const ratio=(part,total)=>total>0?part/total:null;
const ratioNode=(label,value,total,detail,view)=>`<button class="summary-node" data-summary-view="${view}"><span class="muted">${esc(label)}</span><strong>${esc(number(value))}</strong><small>${esc(detail)}</small>${total>0&&value!==null&&value!==undefined?`<div class="ratio-track"><i class="ratio-fill" style="width:${Math.min(100,Math.max(0,value/total*100))}%"></i></div>`:""}</button>`;
function wireSummaryNavigation(){document.querySelectorAll("[data-summary-view]").forEach(button=>button.addEventListener("click",()=>document.querySelector(`.tab[data-view="${button.dataset.summaryView}"]`)?.click()));}
function renderWholeSummary(){
  const connection=whole.connection,parameter=whole.parameter,forwarding=whole.forwarding,coordination=whole.coordination,precedence=whole.precedence,parameterReduction=ratio(parameter.held_updates,parameter.raw_patches),forwardingReduction=ratio(forwarding.transition_reduction,forwarding.formal_route_changes);
  const connectionFlow=stableConnection
    ?ratioNode("Formal additions + removals",connection.formal_additions+connection.formal_removals,null,"formal Difference workload","connections")+ratioNode("Episode candidates",connection.episode_candidates,null,"exact configured candidates when retained","connections")+ratioNode("Accepted omissions",connection.accepted_omissions,connection.episode_candidates,connection.episode_candidates?`${number(ratio(connection.accepted_omissions,connection.episode_candidates)*100)}% of candidates`:"rejected count unavailable","connections")+ratioNode("Materialized transitions",connection.materialized_transitions,null,"published logical intents","connections")
    :ratioNode("Formal additions + removals",connection.formal_additions+connection.formal_removals,null,"formal sample-boundary workload","connections")+ratioNode("Published transitions",connection.materialized_transitions,connection.formal_additions+connection.formal_removals,"faithful sample-boundary translation","connections");
  const forwardingFlow=stableForwarding
    ?ratioNode("Formal Route changes",forwarding.formal_route_changes,null,"formal RoutePatch workload","forwarding")+ratioNode("FOLLOW",forwarding.follow,forwarding.formal_route_changes,"exact replay classification","forwarding")+ratioNode("HOLD",forwarding.hold,forwarding.formal_route_changes,"exact replay classification","forwarding")+ratioNode("Emitted transitions",forwarding.emitted_transitions,forwarding.formal_route_changes,forwardingReduction===null?"reduction unavailable":`${number(forwardingReduction*100)}% net reduction`,"forwarding")
    :ratioNode("Formal Route changes",forwarding.formal_route_changes,null,"formal RoutePatch workload","forwarding")+ratioNode("Published transitions",forwarding.emitted_transitions,forwarding.formal_route_changes,"faithful always-FOLLOW translation","forwarding");
  const coordinationFlow=ratioNode("PlanningDecision values",coordination.decision_count,null,JSON.stringify(coordination.decisions_by_result),"decisions")+(stableConnection?ratioNode("Accepted omissions",coordination.accepted_omissions,coordination.candidate_count,"rejected candidates are not invented","decisions"):"")+ratioNode("Precedence edges",precedence.edges,precedence.forwarding_transitions,`${number(precedence.edges_per_forwarding)} edges / forwarding transition`,"forwarding");
  document.getElementById("summary-basis").textContent=SUMMARY.measurement_basis;
  document.getElementById("planner-overview-summary").innerHTML=`<h3>Connection</h3><div class="summary-flow">${connectionFlow}</div><h3>Parameter</h3><div class="summary-flow">${ratioNode("Raw parameter patches",parameter.raw_patches,null,"formal sampled updates","parameters")}${ratioNode("Emitted transitions",parameter.emitted_transitions,parameter.raw_patches,"published targets","parameters")}${ratioNode("Not individually emitted",parameter.held_updates,parameter.raw_patches,parameterReduction===null?"unavailable":`${number(parameterReduction*100)}% reduction`,"parameters")}</div><h3>Forwarding</h3><div class="summary-flow">${forwardingFlow}</div><h3>Coordination + precedence</h3><div class="summary-flow">${coordinationFlow}</div>`;wireSummaryNavigation();
}
function distributionCard(label,value,unit=""){if(!value||!Number(value.count))return `<div class="distribution-card"><strong>${esc(label)}</strong><p class="muted">No retained observations</p></div>`;return `<div class="distribution-card"><strong>${esc(label)}</strong><p>p50 <b>${esc(number(value.median))}${esc(unit)}</b> · p95 <b>${esc(number(value.p95))}${esc(unit)}</b> · max <b>${esc(number(value.maximum))}${esc(unit)}</b></p><small>n=${esc(number(value.count))}</small></div>`;}
const distributions=SUMMARY.distributions,parameterDeviation=distributions.held_parameter_absolute_deviation?.delay_us,holdDuration=distributions.hold_duration_seconds?.completed;
document.getElementById("planner-distributions").innerHTML=distributionCard("Held parameter deviation · delay",parameterDeviation," µs")+(stableForwarding?distributionCard("Completed HOLD duration",holdDuration," s")+distributionCard("Held hop stretch",distributions.held_hop_stretch," hops")+distributionCard("Held directional delay stretch",distributions.held_directional_delay_stretch_us," µs"):"");
renderWholeSummary();

document.querySelectorAll(".tab").forEach(button=>button.addEventListener("click",()=>{
  document.querySelectorAll(".tab").forEach(value=>value.classList.toggle("active",value===button));
  document.querySelectorAll(".view").forEach(value=>value.classList.toggle("active",value.id===`view-${button.dataset.view}`));
  const overview=button.dataset.view==="overview";
  document.getElementById("overview-spatial-driver").hidden=!overview;
  document.getElementById("tab-spatial-hint").hidden=overview;
  document.getElementById("spatial-driver-title").textContent=overview
    ?"Global Timeline selection → shared spatial context"
    :`${button.textContent} selection → shared spatial context`;
  if(button.dataset.view==="connections")ensureConnectionCatalog();
  if(button.dataset.view==="parameters")ensureParameterCatalog();
  if(button.dataset.view==="forwarding")ensureRouteCatalog();
  if(button.dataset.view==="comparison")renderComparison();
}));

const realSlices=DATA.slice_manifest.filter(value=>value.timestamp!==null);
const artifactEnd=DATA.timeline.artifact_end_time||realSlices.at(-1)?.timestamp||DATA.timeline.start_time;
const startMs=Date.parse(DATA.timeline.start_time),endMs=Date.parse(artifactEnd);
const pct=time=>Math.max(0,Math.min(100,(Date.parse(time)-startMs)/(endMs-startMs)*100));
let transitionById={};
let spatialReady=false;
let selectedPlanIndex=0;
const grouped=DATA.nominal_groups.map(value=>({...value,values:[]}));
function ticks(){return realSlices.map(value=>`<div class="tick" style="left:${pct(value.timestamp)}%"><span>S${value.slice_id}</span></div>`).join("");}
function lane(label,content){return `<div class="lane"><div class="lane-label">${esc(label)}</div>${ticks()}${content}</div>`;}
function renderTimeline(){
  const timeline=document.getElementById("global-timeline");
  if(timeline.dataset.ready==="true"){
    timeline.querySelectorAll(".plan-bar[data-plan-index]").forEach(button=>button.classList.toggle("selected",Number(button.dataset.planIndex)===selectedPlanIndex));
    return;
  }
  const windows=new Map((config.planning_windows||[]).map(value=>[value.publication_index,value]));
  const planBars=DATA.plans.map(plan=>{const start=plan.start_timestamp||DATA.timeline.start_time,left=pct(start),right=pct(plan.end_timestamp),window=windows.get(plan.plan_index),planningStart=window?.start_timestamp||start,planningEnd=window?.end_timestamp||plan.end_timestamp,planningLeft=pct(planningStart),planningRight=pct(planningEnd);return `<button class="plan-bar ${plan.plan_index===selectedPlanIndex?"selected":""}" data-plan-index="${plan.plan_index}" title="publication ${esc(plan.coverage)} · planning ${window?`(${window.planning_start_slice_id},${window.planning_end_slice_id}]`:"range unavailable"}" style="left:${left}%;width:${Math.max(1,right-left)}%">${esc(plan.label)} ${esc(plan.coverage)}</button><span class="planning-window-bar" title="actual selected planning window ${window?`(${window.planning_start_slice_id},${window.planning_end_slice_id}]`:"fallback to publication"}" style="left:${planningLeft}%;width:${Math.max(.3,planningRight-planningLeft)}%"></span>`;}).join("");
  const markerLane=family=>grouped.flatMap(group=>{const count=group.counts[family]||0;return count?[`<button class="marker ${family}" data-time="${esc(group.nominal_time)}" title="${fmt(group.nominal_time)} · ${count} grouped" style="left:${pct(group.nominal_time)}%">${count}</button>`]:[];}).join("");
  const decisionMarkers=DATA.decisions.filter(value=>value.timeline_time).map((value,index)=>`<button class="marker decision-marker" data-decision-index="${index}" title="${esc(value.decision)} · ${esc(value.decided_by)}" style="left:${pct(value.timeline_time)}%">${esc(value.decision[0].toUpperCase())}</button>`).join("");
  timeline.innerHTML=[lane("Plan publication",planBars),lane("Connection",markerLane("connection")),lane("Parameter",markerLane("parameter")),lane("Forwarding",markerLane("forwarding")),lane("Decisions",decisionMarkers)].join("")+`<p class="muted">Default x-axis ends at current artifact coverage ${esc(fmt(artifactEnd))}, not unused Scenario time. Yellow under-bars are exact selected planning windows; blue bars are publication coverage.</p>`;
  timeline.dataset.ready="true";
  document.querySelectorAll(".marker[data-time]").forEach(marker=>marker.addEventListener("click",()=>selectPlaybackTime(marker.dataset.time)));
  document.querySelectorAll("[data-plan-index]").forEach(button=>button.addEventListener("click",()=>selectPlan(Number(button.dataset.planIndex))));
  document.querySelectorAll("[data-decision-index]").forEach(button=>button.addEventListener("click",()=>{document.querySelector('.tab[data-view="decisions"]')?.click();const decision=DATA.decisions[Number(button.dataset.decisionIndex)];const differenceId=Math.max(...decision.source_difference_ids);differenceSelect.value=String(differenceId);}));
}
const nominalSelect=document.getElementById("nominal-time-select");
nominalSelect.innerHTML='<option value="">Choose a nominal time</option>'+grouped.map(value=>`<option value="${esc(value.nominal_time)}">${fmt(value.nominal_time)}</option>`).join("");
const playbackScrubber=document.getElementById("playback-scrubber"),playbackToggle=document.getElementById("playback-toggle"),playbackStatus=document.getElementById("playback-status"),playbackMode=document.getElementById("playback-mode"),playbackSpeed=document.getElementById("playback-speed");
playbackScrubber.max=String(Math.max(0,grouped.length-1));playbackScrubber.disabled=!grouped.length;
let playbackIndex=0,playbackTimer=null;
function pausePlayback(){if(playbackTimer!==null){clearInterval(playbackTimer);playbackTimer=null;}playbackToggle.textContent="Play";document.body.dataset.plannerPlayback="paused";}
function selectPlaybackTime(time){const index=grouped.findIndex(value=>value.nominal_time===time);if(index<0)return;pausePlayback();selectPlaybackIndex(index);}
function selectPlaybackIndex(index){if(!grouped.length)return;playbackIndex=Math.max(0,Math.min(grouped.length-1,Number(index)));playbackScrubber.value=String(playbackIndex);nominalSelect.value=grouped[playbackIndex].nominal_time;return renderNominalSummary();}
function stepPlayback(delta){pausePlayback();selectPlaybackIndex(playbackIndex+delta);}
function startPlayback(){if(!grouped.length)return;pausePlayback();playbackToggle.textContent="Pause";document.body.dataset.plannerPlayback="playing";const interval=Math.max(120,1000/Number(playbackSpeed.value||1));playbackTimer=setInterval(()=>{if(playbackIndex>=grouped.length-1){pausePlayback();return;}selectPlaybackIndex(playbackIndex+1);},interval);}
document.getElementById("playback-previous").addEventListener("click",()=>stepPlayback(-1));
document.getElementById("playback-next").addEventListener("click",()=>stepPlayback(1));
playbackToggle.addEventListener("click",()=>playbackTimer===null?startPlayback():pausePlayback());
playbackScrubber.addEventListener("input",()=>{pausePlayback();selectPlaybackIndex(Number(playbackScrubber.value));});
playbackSpeed.addEventListener("change",()=>{if(playbackTimer!==null)startPlayback();});
playbackMode.addEventListener("change",()=>{if(spatial.kind==="group"){spatial={...spatial,playbackMode:playbackMode.value};drawSpatial();}});
function nominalEventLabel(value){
  if(value.family==="connection"){
    return `${value.operation.toUpperCase()} · ${connectionAlias(value.connection_ref)}`;
  }
  if(value.family==="parameter"){
    return `${connectionAlias(value.connection_ref)} · ${value.direction} · ${Object.keys(value.targets).join(", ")}`;
  }
  return `${value.operation.toUpperCase()} · ${routeAlias(value.route_ref)} · ${nodeAlias(value.next_hop)}`;
}
function focusNominalEvent(value){
  pausePlayback();
  document.querySelectorAll(".event-button").forEach(button=>button.classList.toggle("active",button.dataset.transitionId===value.transition_id));
  if(value.family==="forwarding"){
    setRouteSpatial(value,value.transition_id);
    return;
  }
  const connection=connectionFromRef(value.connection_ref);
  setConnectionSpatial(connection,value.transition_id,value.family==="parameter"?"Parameter":"Connection");
}
let nominalGeneration=0;
async function renderNominalSummary(){
  const group=grouped.find(value=>value.nominal_time===nominalSelect.value);if(!group)return;
  playbackIndex=Math.max(0,grouped.indexOf(group));playbackScrubber.value=String(playbackIndex);
  const generation=++nominalGeneration;
  const eventRegion=document.getElementById("nominal-events");
  eventRegion.setAttribute("aria-busy","true");
  playbackStatus.textContent=`Loading group ${playbackIndex+1}/${grouped.length} without changing the current view…`;
  if(!eventRegion.firstElementChild)eventRegion.innerHTML='<div class="event-groups"><section class="event-group"><p class="muted">Loading selected Plan chunks…</p></section><section class="event-group"></section><section class="event-group"></section></div>';
  const plans=await Promise.all(group.plan_indices.map(index=>loadChunk(DATA.plans.find(value=>value.plan_index===index).chunk)));
  if(generation!==nominalGeneration){group.plan_indices.forEach(index=>releaseChunk(DATA.plans.find(value=>value.plan_index===index).chunk));return;}
  group.values=plans.flatMap(plan=>{const ids=new Set((plan.temporal_groups.find(value=>value.nominal_time===group.nominal_time)?.transition_ids)||[]);return [...plan.connection_transitions,...plan.parameter_transitions,...plan.forwarding_transitions].filter(value=>ids.has(value.transition_id)).map(value=>({...value,plan_label:plan.label}));});
  transitionById=Object.fromEntries(group.values.map(value=>[value.transition_id,value]));
  group.plan_indices.forEach(index=>releaseChunk(DATA.plans.find(value=>value.plan_index===index).chunk));
  const families=["connection","parameter","forwarding"];
  const counts=Object.fromEntries(families.map(family=>[family,group.values.filter(value=>value.family===family).length]));
  const labels=group.plan_indices.map(index=>DATA.plans.find(value=>value.plan_index===index).label);
  document.getElementById("nominal-summary").innerHTML=`<p><strong>${fmt(group.nominal_time)}</strong> · ${esc(labels.join(", "))}</p>${families.map(family=>`<span class="domain-count ${counts[family]===0?"zero":""}">${family}: <strong>${counts[family]}</strong></span>`).join("")}${counts.forwarding===0?'<p class="zero">No published forwarding-state change at this nominal time.</p>':""}`;
  const previewLimit=60;
  eventRegion.innerHTML=`<div class="event-groups">${families.map(family=>{const values=group.values.filter(value=>value.family===family),preview=values.slice(0,previewLimit);return `<section class="event-group"><h3>${esc(family)} (${values.length})</h3><div class="event-list">${preview.length?preview.map(value=>`<button class="event-button" data-transition-id="${esc(value.transition_id)}" title="${esc(value.transition_id)}">${esc(nominalEventLabel(value))}</button>`).join(""):'<p class="zero">0 real transitions</p>'}</div>${values.length>previewLimit?`<p class="muted">Showing ${previewLimit} of ${values.length}; use the family identity search for any exact item.</p>`:""}</section>`;}).join("")}</div><p class="muted">The globe shows the complete event group as bounded change context. Select one event for its exact Connection or P/Q Route evidence.</p>`;
  eventRegion.setAttribute("aria-busy","false");
  document.querySelectorAll(".event-button").forEach(button=>button.addEventListener("click",()=>focusNominalEvent(transitionById[button.dataset.transitionId])));
  playbackStatus.textContent=`${labels.join(", ")} · group ${playbackIndex+1}/${grouped.length} · ${fmt(group.nominal_time)} · Formal sample context / ${playbackMode.options[playbackMode.selectedIndex].text}`;
  if(spatialReady)setSpatial({kind:"group",time:group.nominal_time,nodes:[],routeRef:null,target:null,title:`Full-run playback · ${labels.join(", ")} · ${fmt(group.nominal_time)}`,groupValues:group.values,playbackMode:playbackMode.value});
}
nominalSelect.addEventListener("change",()=>selectPlaybackTime(nominalSelect.value));
function selectPlan(index){const summary=SUMMARY.per_plan.find(value=>value.plan_index===index),plan=DATA.plans.find(value=>value.plan_index===index);if(!summary||!plan)return;selectedPlanIndex=index;document.body.dataset.selectedPlan=plan.label;const formal=summary.formal,published=summary.published,formalTotal=formal.connection_additions+formal.connection_removals+formal.parameter_patches+formal.route_patches,publishedTotal=published.connection+published.parameter+published.forwarding;document.getElementById("selected-plan-heading").textContent=`${plan.label} · publication ${plan.coverage}`;document.getElementById("selected-plan-summary").innerHTML=`<div class="summary-flow"><button class="summary-node" data-summary-view="comparison"><span class="muted">Difference input</span><strong>${number(summary.difference_count)} Δ</strong><small>${number(formalTotal)} formal patches</small></button><button class="summary-node" data-summary-view="connections"><span class="muted">Connection planning</span><strong>${number(published.connection)}</strong><small>${number(formal.connection_additions+formal.connection_removals)} formal add/remove</small></button><button class="summary-node" data-summary-view="parameters"><span class="muted">Parameter planning</span><strong>${number(published.parameter)}</strong><small>${number(formal.parameter_patches)} raw patches</small></button><button class="summary-node" data-summary-view="forwarding"><span class="muted">Forwarding planning</span><strong>${number(published.forwarding)}</strong><small>${number(formal.route_patches)} formal Route changes</small></button><button class="summary-node" data-summary-view="decisions"><span class="muted">Coordination / decisions</span><strong>${number(summary.planning_decisions)}</strong><small>exact retained PlanningDecision values touching coverage</small></button><button class="summary-node" data-summary-view="forwarding"><span class="muted">Precedence</span><strong>${number(published.precedence)}</strong><small>${number(summary.precedence_per_forwarding)} edges / F</small></button><div class="summary-node"><span class="muted">Published logical intents</span><strong>${number(publishedTotal)}</strong><small>C + P + F; rollups are not involved</small></div></div>`;wireSummaryNavigation();const times=grouped.filter(value=>Date.parse(value.nominal_time)>(plan.start_timestamp?Date.parse(plan.start_timestamp):-Infinity)&&Date.parse(value.nominal_time)<=Date.parse(plan.end_timestamp));if(times.length)selectPlaybackIndex(grouped.indexOf(times[0]));renderTimeline();}
selectPlan(selectedPlanIndex);
document.getElementById("plan-table").innerHTML=table(["Plan","Coverage","Start","End","C","P","F","Precedence"],DATA.plans.map(plan=>[plan.label,plan.coverage,fmt(plan.start_timestamp),fmt(plan.end_timestamp),plan.connection_transition_count,plan.parameter_transition_count,plan.forwarding_transition_count,plan.forwarding_precedence_count]));

function fillSelect(element,values,key,label,selected){element.innerHTML=values.map(value=>`<option value="${esc(value[key])}" ${value[key]===selected?"selected":""} title="${esc(value[key])}">${esc(label(value))}</option>`).join("");}
function lifetimeLane(label,occurrences,kind){return `<div class="lifetime"><span class="lifetime-label">${esc(label)}</span>${occurrences.map((item,index)=>{const start=item.start_timestamp??item.establish_time,end=item.right_censored?artifactEnd:(item.end_timestamp??item.withdraw_time??artifactEnd),open=item.right_censored===true;return `<span class="life-bar ${kind}" title="Occurrence ${index+1}: ${fmt(start)} → ${open?`observed through ${fmt(artifactEnd)} · OPEN/right-censored`:fmt(end)}" style="left:${pct(start)}%;width:${Math.max(.5,pct(end)-pct(start))}%"></span>${kind==="planned"?`<span class="life-event" title="ESTABLISH ${fmt(start)}" style="left:${pct(start)}%"></span>${open?"":`<span class="life-event" title="WITHDRAW ${fmt(end)}" style="left:${pct(end)}%"></span>`}`:""}${open?`<span class="open-marker" title="OPEN / right-censored" style="left:${pct(end)}%">▷</span>`:""}`;}).join("")}</div>`;}
const connectionSelect=document.getElementById("connection-select");
let connectionCatalog=[],connectionGeneration=0,currentConnection=null;
async function ensureConnectionCatalog(){if(connectionCatalog.length){renderConnection();return;}document.getElementById("connection-detail").innerHTML='<p class="muted">Loading Connection catalog…</p>';connectionCatalog=await loadCatalog("connection");fillSelect(connectionSelect,connectionCatalog,"identity",value=>connectionAlias(value.identity),DATA.representatives.connection_ref);renderConnection();}
function connectionTransitions(reference){return currentConnection?.connection_ref===reference?currentConnection.planned_transitions||[]:[];}
async function openConnectionRef(reference){if(!reference)return;document.querySelector('.tab[data-view="connections"]')?.click();await ensureConnectionCatalog();const entry=connectionCatalog.find(item=>item.identity===reference);if(!entry)return;connectionSelect.value=reference;await renderConnection();}
async function renderConnection(){
  const entry=connectionCatalog.find(item=>item.identity===connectionSelect.value);if(!entry)return;
  const generation=++connectionGeneration;document.getElementById("connection-detail").innerHTML='<p class="muted">Loading selected Connection chunk…</p>';
  const values=await loadChunk(entry.chunk),value=values.find(item=>item.connection_ref===entry.identity);
  if(generation!==connectionGeneration){releaseChunk(entry.chunk);return;}releaseChunk(entry.chunk);if(!value)return;currentConnection=value;
  const changes=value.planned_transitions||[],formal=value.formal_occurrences.map((item,index)=>`<div class="occurrence formal"><strong>Formal sampled period ${index+1}</strong><br>S${item.start_slice_id} → S${item.end_slice_id} · ${item.sample_count} samples<br>${fmt(item.start_timestamp)} → ${item.right_censored?`observed through current artifact end ${fmt(item.observed_through_timestamp)} · OPEN/right-censored`:fmt(item.end_timestamp)}</div>`).join(""),planned=value.planned_occurrences.map((item,index)=>`<div class="occurrence planned"><strong>Planned period ${index+1}</strong><br>ESTABLISH ${fmt(item.establish_time)}<br>WITHDRAW ${item.right_censored?"open · right-censored":fmt(item.withdraw_time)}</div>`).join("");
  const connectionPolicyNote=stableConnection?`Episode-candidate/omission evidence is shown only where retained; rejected candidate status is unavailable.`:`Faithful sample-boundary translation is active: every formal add/remove is published without midpoint timing, episode omission, or cross-family suppression.`;
  document.getElementById("connection-detail").innerHTML=`<h2>${esc(connectionAlias(value))}</h2>${canonical(value.connection_ref)}<h3>Lifetime comparison</h3>${lifetimeLane("Formal sampled",value.formal_occurrences,"formal")}${lifetimeLane("Planned",value.planned_occurrences,"planned")}<p class="muted">Blue boundaries are retained Slice samples; yellow ticks are exact model-emitted nominal transitions. Open triangles stop at current artifact coverage and mean right-censored, never Scenario-end continuation.</p><label>Spatial event <select id="connection-event">${changes.map(item=>`<option value="${item.transition_id}">${esc(item.operation.toUpperCase())} · ${fmt(item.nominal_time)}</option>`).join("")}</select></label><div class="grid two"><div><h3>Formal sampled presence</h3>${formal||'<p class="muted">None</p>'}</div><div><h3>Published planned presence</h3>${planned||'<p class="muted">No published occurrence in this artifact.</p>'}</div></div><p><strong>Configured Connection policy:</strong> ${esc(config.connection_model||"unspecified")} · ${esc(configDetail(["connection_timing","minimum_dwell_seconds","connection_episode_omission","connection_episode_candidate_max_seconds_by_kind"]))}.</p><p class="muted">${esc(connectionPolicyNote)}</p>`;
  const event=document.getElementById("connection-event");if(event){event.addEventListener("change",()=>setConnectionSpatial(value,event.value));setConnectionSpatial(value,event.value);}
}
connectionSelect.addEventListener("change",renderConnection);

function parameterPolylines(samples,xFor,yFor){const groups=[];let group=[];samples.forEach(sample=>{if(group.length&&sample.slice_id!==group[group.length-1].slice_id+1){groups.push(group);group=[];}group.push(sample);});if(group.length)groups.push(group);return groups.map(values=>`<polyline points="${values.map(item=>`${xFor(item.timestamp)},${yFor(item.value)}`).join(" ")}" fill="none" stroke="#69a9ff" stroke-width="2"/>`).join("");}
const parameterUnits={delay_us:"µs",bandwidth_bps:"bps",packet_loss_ppm:"ppm"};
function niceNumericTicks(minimum,maximum,count=5){if(!Number.isFinite(minimum)||!Number.isFinite(maximum))return [0];if(maximum===minimum){const pad=Math.max(1,Math.abs(maximum)*.05);minimum-=pad;maximum+=pad;}const raw=(maximum-minimum)/Math.max(1,count-1),power=10**Math.floor(Math.log10(raw)),fraction=raw/power,nice=(fraction<=1?1:fraction<=2?2:fraction<=5?5:10)*power,start=Math.floor(minimum/nice)*nice,end=Math.ceil(maximum/nice)*nice,result=[];for(let value=start;value<=end+nice*.01;value+=nice)result.push(Number(value.toPrecision(12)));return result;}
function parameterPolicy(field){const configured=config[`parameter_${field}`]||{},legacy=(config.parameter_thresholds||{})[field];return {absolute:present(configured.absolute_deadband)?Number(configured.absolute_deadband):(present(legacy)?Number(legacy):null),relative:present(configured.relative_deadband)?Number(configured.relative_deadband):null,persistence:present(configured.persistence_seconds)?Number(configured.persistence_seconds):null,maxStaleness:present(configured.max_staleness_seconds)?Number(configured.max_staleness_seconds):null};}
function effectiveThreshold(policy,planned){return Math.max(policy.absolute??0,(policy.relative??0)*Math.abs(Number(planned)));}
const parameterSelect=document.getElementById("parameter-select");
let parameterCatalog=[],parameterGeneration=0;
async function ensureParameterCatalog(){if(parameterCatalog.length){renderParameter();return;}document.getElementById("parameter-detail").innerHTML='<p class="muted">Loading Parameter catalog…</p>';parameterCatalog=await loadCatalog("parameter");fillSelect(parameterSelect,parameterCatalog,"identity",value=>{const item=parameterFromKey(value.identity);return `${connectionAlias(item.connection_ref)} · ${item.direction} · ${item.field}`;},DATA.representatives.parameter_key);renderParameter();}
async function renderParameter(){
  const entry=parameterCatalog.find(item=>item.identity===parameterSelect.value);if(!entry)return;const generation=++parameterGeneration;document.getElementById("parameter-detail").innerHTML='<p class="muted">Loading selected parameter chunk…</p>';const values=await loadChunk(entry.chunk),value=values.find(item=>item.parameter_key===entry.identity);if(generation!==parameterGeneration){releaseChunk(entry.chunk);return;}releaseChunk(entry.chunk);if(!value)return;
  const samples=value.samples,targets=value.planned,segments=value.planned_state_occurrences.flatMap(item=>item.segments),policy=parameterPolicy(value.field),segmentAt=time=>[...segments].reverse().find(item=>Date.parse(item.start_time)<=Date.parse(time)&&Date.parse(time)<=Date.parse(item.end_time)),sampleEvidence=samples.map(item=>{const segment=segmentAt(item.timestamp),planned=segment?.value,deviation=planned===undefined?null:Math.abs(Number(item.value)-Number(planned)),threshold=planned===undefined?null:effectiveThreshold(policy,planned),emitted=targets.some(target=>target.nominal_time===item.timestamp);return {...item,planned,deviation,threshold,emitted,held:deviation>0&&!emitted,eligible:threshold!==null&&deviation>=threshold};}),allValues=[...samples.map(item=>Number(item.value)),...segments.map(item=>Number(item.value))],allTimes=[...samples.map(item=>Date.parse(item.timestamp)),...segments.flatMap(item=>[Date.parse(item.start_time),Date.parse(item.end_time)]),...targets.map(item=>Date.parse(item.nominal_time))].filter(Number.isFinite),rawMin=Math.min(...allValues),rawMax=Math.max(...allValues),padding=rawMax===rawMin?Math.max(1,Math.abs(rawMax)*.05):(rawMax-rawMin)*.08,yTicks=niceNumericTicks(rawMin-padding,rawMax+padding,6),min=yTicks[0],max=yTicks.at(-1),width=960,height=360,left=82,right=940,top=22,bottom=302,xMin=Math.min(...allTimes),xMax=Math.max(...allTimes),xSpan=Math.max(1,xMax-xMin),xFor=time=>left+(Date.parse(time)-xMin)/xSpan*(right-left),yFor=value=>bottom-(Number(value)-min)/(max-min)*(bottom-top),xTicks=Array.from({length:6},(_,index)=>xMin+xSpan*index/5),unit=parameterUnits[value.field]||value.field;
  const grid=yTicks.map(tick=>`<line class="parameter-grid" x1="${left}" y1="${yFor(tick)}" x2="${right}" y2="${yFor(tick)}"/><text class="parameter-axis" x="${left-9}" y="${yFor(tick)+4}" text-anchor="end">${esc(number(tick))}</text>`).join("")+xTicks.map(tick=>{const x=left+(tick-xMin)/xSpan*(right-left),label=new Date(tick).toISOString().slice(11,19);return `<line class="parameter-grid" x1="${x}" y1="${top}" x2="${x}" y2="${bottom}"/><text class="parameter-axis" x="${x}" y="${bottom+22}" text-anchor="middle">${esc(label)}</text>`;}).join(""),steps=value.planned_state_occurrences.flatMap(occ=>occ.segments.map((item,index)=>`<line x1="${xFor(item.start_time)}" x2="${xFor(item.end_time)}" y1="${yFor(item.value)}" y2="${yFor(item.value)}" stroke="#5dff8b" stroke-width="3"/>${index?`<line x1="${xFor(item.start_time)}" x2="${xFor(item.start_time)}" y1="${yFor(occ.segments[index-1].value)}" y2="${yFor(item.value)}" stroke="#5dff8b" stroke-width="2"/>`:""}`)).join(""),formalPoints=samples.map(item=>`<circle cx="${xFor(item.timestamp)}" cy="${yFor(item.value)}" r="3" fill="#69a9ff"><title>S${item.slice_id} · ${esc(fmt(item.timestamp))} · ${esc(number(item.value))} ${esc(unit)}</title></circle>`).join(""),targetMarks=targets.map(item=>`<circle cx="${xFor(item.nominal_time)}" cy="${yFor(item.value)}" r="5" fill="#ffd166" stroke="#07111f" stroke-width="2"><title>emitted ${esc(number(item.value))} ${esc(unit)} @ ${esc(item.nominal_time)}</title></circle>`).join(""),held=sampleEvidence.filter(item=>item.held),heldMax=Math.max(0,...held.map(item=>item.deviation));
  const connection=connectionFromRef(value.connection_ref),thresholdLabel=`max(${number(policy.absolute)}, ${number(policy.relative)} × |planned|)`;document.getElementById("parameter-detail").innerHTML=`<h2>${esc(connectionAlias(connection))}</h2>${canonical(value.connection_ref)}<div class="grid stats"><div class="card stat"><span class="muted">Formal samples</span><strong>${samples.length}</strong></div><div class="card stat"><span class="muted">Emitted targets</span><strong>${targets.length}</strong></div><div class="card stat"><span class="muted">Not individually emitted</span><strong>${held.length}</strong></div><div class="card stat"><span class="muted">Max held deviation</span><strong>${number(heldMax)} ${esc(unit)}</strong></div></div><p>${esc(value.direction)} · <strong>${esc(value.field)}</strong></p><div class="visual-primary"><svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Formal and planned ${esc(value.field)} over simulation time">${grid}<line x1="${left}" y1="${bottom}" x2="${right}" y2="${bottom}" stroke="#8aa4ba"/><line x1="${left}" y1="${top}" x2="${left}" y2="${bottom}" stroke="#8aa4ba"/><text class="parameter-axis" x="${(left+right)/2}" y="${height-12}" text-anchor="middle">simulation time (UTC)</text><text class="parameter-axis" x="18" y="${(top+bottom)/2}" text-anchor="middle" transform="rotate(-90 18 ${(top+bottom)/2})">${esc(value.field)} (${esc(unit)})</text>${parameterPolylines(samples,xFor,yFor)}${formalPoints}${steps}${targetMarks}</svg><div class="parameter-legend"><span><i style="background:#69a9ff"></i>formal sampled value</span><span><i style="background:#5dff8b"></i>planned target (step)</span><span><i style="background:#ffd166;height:8px;width:8px;border-radius:50%"></i>emitted ParameterTransition</span></div><p class="muted">Both axes use actual simulation time and ${esc(unit)} values; the planned target is derived only from published Connection/Parameter transitions. Gaps remain disconnected Connection lifetimes.</p></div><details><summary>Show planning details</summary><div class="config-grid"><div><span class="muted">Effective envelope</span><br><strong>${esc(thresholdLabel)}</strong></div><div><span class="muted">Persistence</span><br><strong>${number(policy.persistence)} s</strong></div><div><span class="muted">Drift max-staleness</span><br><strong>${number(policy.maxStaleness)} s</strong></div><div><span class="muted">Bundling</span><br><strong>${esc(String(config.parameter_bundle_drifted_fields??false))}</strong></div></div><p class="muted">Eligibility is projected only from exact samples and the displayed configuration; rejected outputs are not inferred.</p>${table(["Slice","Time","Value","Deviation","Effective threshold","State"],sampleEvidence.map(item=>[`S${item.slice_id}`,fmt(item.timestamp),`${number(item.value)} ${unit}`,number(item.deviation),number(item.threshold),item.emitted?"EMITTED":item.held?"HELD":"ALIGNED"]))}</details><details><summary>Exact emitted transition evidence</summary>${table(["Plan","Nominal time","Target","Bundled fields"],targets.map(item=>[item.plan_label,fmt(item.nominal_time),`${number(item.value)} ${unit}`,item.bundled_fields.join(", ")]))}</details>`;const event=targets[0]?.nominal_time??samples[0]?.timestamp;setSpatial({kind:"connection",time:event,nodes:[connection.connection_id.endpoint_a,connection.connection_id.endpoint_b],title:`Parameter · ${connectionAlias(connection)}`});
}
parameterSelect.addEventListener("change",renderParameter);

function stateSegments(events){let state=null,result=[];events.forEach((event,index)=>{if(event.operation==="removed")state=null;else state=event.next_hop;const end=events[index+1]?.nominal_time??events[index+1]?.timestamp??artifactEnd;const start=event.nominal_time??event.timestamp;if(state!==null)result.push({start,end,state,operation:event.operation});});return result;}
function stateLane(label,events){return `<div class="state-lane"><span class="state-label">${esc(label)}</span>${stateSegments(events).map(item=>`<span class="state-segment" title="${esc(item.operation)} · ${esc(item.state)}" style="left:${pct(item.start)}%;width:${Math.max(.5,pct(item.end)-pct(item.start))}%">${esc(nodeAlias(item.state))}</span>`).join("")}</div>`;}
const routeSelect=document.getElementById("route-select");
let routeCatalog=[],currentRoute=null,routeGeneration=0;
async function ensureRouteCatalog(){if(routeCatalog.length){renderRoute();return;}document.getElementById("route-detail").innerHTML='<p class="muted">Loading Forwarding catalog…</p>';routeCatalog=await loadCatalog("forwarding");fillSelect(routeSelect,routeCatalog,"identity",value=>routeAlias(value.identity),DATA.representatives.route_ref);renderRoute();}
function priorNextHop(route,transition){let state=null;for(const value of route.planned_transitions){if(value.transition_id===transition.transition_id)break;state=value.operation==="removed"?null:value.next_hop;}return state;}
function transitionNextHop(transition){const route=currentRoute?.route_ref===transition.route_ref?currentRoute:null;return transition.operation==="removed"&&route?priorNextHop(route,transition):transition.next_hop;}
function transitionCard(value,selected=false){const nextHop=transitionNextHop(value),context=value.operation==="removed"?`BEFORE next hop: ${nodeAlias(nextHop)}`:`Next hop: ${nodeAlias(nextHop)}`;return `<div class="${selected?"dependency-selected":"precedence"}" data-transition="${esc(value.transition_id)}"><strong title="${esc(value.route_ref)}">${esc(routeAlias(value.route_ref))}</strong><br>${esc(value.operation.toUpperCase())}<br><span class="muted">${esc(context)}</span></div>`;}
let precedenceGeneration=0;
async function renderPrecedence(route,transitionId){const selected=route.planned_transitions.find(item=>item.transition_id===transitionId)??route.planned_transitions[0];if(!selected)return;const generation=++precedenceGeneration,edges=route.precedences.filter(edge=>edge.nominal_time===selected.nominal_time&&(edge.predecessor===selected.transition_id||edge.successor===selected.transition_id));if(!edges.length){document.getElementById("route-precedence").innerHTML=`<div data-selected-precedence="${esc(selected.transition_id)}"><p><strong>${esc(selected.operation.toUpperCase())}</strong> · ${fmt(selected.nominal_time)} · ${esc(routeAlias(selected.route_ref))}</p><p class="muted">No direct same-time precedence relation touches the selected ForwardingTransition.</p></div><p class="muted">Only formal direct same-time ForwardingTransitionPrecedence values touching the selected event are shown. This is Plan semantic dependency, NOT tuple order and NOT BPF/Linux total execution order.</p>`;return;}document.getElementById("route-precedence").innerHTML='<p class="muted">Loading the selected event’s canonical Plan precedence context…</p>';const match=/^p(\d+)-f/.exec(selected.transition_id),planEntry=match?DATA.plans.find(item=>item.plan_index===Number(match[1])):null;if(!planEntry)throw new Error(`No canonical Plan chunk for ${selected.transition_id}`);const plan=await loadChunk(planEntry.chunk);if(generation!==precedenceGeneration){releaseChunk(planEntry.chunk);return;}releaseChunk(planEntry.chunk);transitionById=Object.fromEntries(plan.forwarding_transitions.map(item=>[item.transition_id,{...item,plan_label:plan.label}]));const canonicalSelected=transitionById[selected.transition_id]??selected,incoming=edges.filter(edge=>edge.successor===selected.transition_id),outgoing=edges.filter(edge=>edge.predecessor===selected.transition_id);document.getElementById("route-precedence").innerHTML=`<div data-selected-precedence="${esc(selected.transition_id)}"><p><strong>${esc(selected.operation.toUpperCase())}</strong> · ${fmt(selected.nominal_time)} · ${esc(routeAlias(selected.route_ref))}</p><div class="dependency-chain"><div class="dependency-column">${incoming.map(edge=>transitionCard(transitionById[edge.predecessor])).join("")||'<span class="muted">No direct predecessor</span>'}</div><div class="dependency-edge">→</div>${transitionCard(canonicalSelected,true)}<div class="dependency-edge">→</div><div class="dependency-column">${outgoing.map(edge=>transitionCard(transitionById[edge.successor])).join("")||'<span class="muted">No direct successor</span>'}</div></div></div><p class="muted">Only formal direct same-time ForwardingTransitionPrecedence values touching the selected event are shown. This is Plan semantic dependency, NOT tuple order and NOT BPF/Linux total execution order.</p>`;}
function renderRouteSelection(route,transitionId){renderPrecedence(route,transitionId);setRouteSpatial(route,transitionId);}
async function renderRoute(){const entry=routeCatalog.find(item=>item.identity===routeSelect.value);if(!entry)return;const generation=++routeGeneration;document.getElementById("route-detail").innerHTML='<p class="muted">Loading selected forwarding chunk…</p>';const values=await loadChunk(entry.chunk),raw=values.find(item=>item.route_ref===entry.identity);if(generation!==routeGeneration){releaseChunk(entry.chunk);return;}releaseChunk(entry.chunk);if(!raw)return;const value={...raw,planned_transitions:raw.planned_transitions.map(item=>({...item,route_ref:raw.route_ref,source:raw.source,target:raw.target}))};currentRoute=value;transitionById=Object.fromEntries(value.planned_transitions.map(item=>[item.transition_id,item]));document.getElementById("route-detail").innerHTML=`<h2>${esc(routeAlias(value.route_ref))}</h2>${canonical(value.route_ref)}<h3>Next-hop state timeline</h3>${stateLane("Formal patch",value.formal_patches)}${stateLane("Published",value.planned_transitions)}<label>Spatial event <select id="route-event">${value.planned_transitions.map(item=>`<option value="${item.transition_id}">${esc(item.operation.toUpperCase())} · ${fmt(item.nominal_time)} · ${esc(nodeAlias(transitionNextHop(item)))}</option>`).join("")}</select></label><div class="grid two"><div><h3>Formal RoutePatch evolution</h3>${table(["Δ","Time","Operation","Next hop"],value.formal_patches.map(item=>[`Δ${item.difference_id}`,fmt(item.timestamp),item.operation,nodeAlias(item.next_hop)]))}</div><div><h3>Published ForwardingTransitions</h3>${table(["Plan","Nominal time","Operation","Next hop / BEFORE context"],value.planned_transitions.map(item=>[item.plan_label,fmt(item.nominal_time),item.operation,nodeAlias(transitionNextHop(item))]))}</div></div><h3>Same-time forwarding dependency</h3><div id="route-precedence"></div>`;const event=document.getElementById("route-event");if(event?.value){event.addEventListener("change",()=>renderRouteSelection(value,event.value));renderRouteSelection(value,event.value);}else setRouteSpatial(value,null);}
routeSelect.addEventListener("change",renderRoute);

document.getElementById("decision-list").innerHTML=DATA.decisions.length?DATA.decisions.map(value=>`<div class="occurrence ${value.decision}"><strong>${esc(value.decision.toUpperCase())}</strong> · Δ${esc(value.source_difference_ids.join(", Δ"))}<br><span class="muted">${esc(value.decided_by)}</span><br>${esc(value.reason)}</div>`).join(""):'<p class="muted">No PlanningDecision was retained.</p>';
document.getElementById("decision-boundary").innerHTML=stableConnection?`<p><strong>Accepted suppressions:</strong> ${DATA.observed_behavior.accepted_suppressions}</p><p><strong>Rejected candidates:</strong> unavailable — ${esc(DATA.inspection_limits.rejected_suppression_candidates)}.</p><p>Zero accepted suppressions is valid conservative behavior, not Tool failure.</p>`:`<p><strong>Identity coordination is active.</strong></p><p>Connection suppression is disabled by the selected normal composition; no suppression-candidate panel is applicable.</p>`;
const differenceSelect=document.getElementById("difference-select");
differenceSelect.innerHTML=DATA.difference_catalog.map(value=>`<option value="${value.difference_id}">Δ${value.difference_id} ${esc(value.interval)}</option>`).join("");
function transitionText(value){if(value.family==="connection")return `${value.operation} ${connectionAlias(value.connection_ref)} @ ${fmt(value.nominal_time)}`;if(value.family==="parameter")return `${connectionAlias(value.connection_ref)} ${value.direction} ${JSON.stringify(value.targets)} @ ${fmt(value.nominal_time)}`;return `${value.operation} ${routeAlias(value.route_ref)} → ${nodeAlias(value.next_hop)} @ ${fmt(value.nominal_time)}`;}
let differenceGeneration=0;
async function renderComparison(){const entry=DATA.difference_catalog.find(item=>String(item.difference_id)===differenceSelect.value);if(!entry)return;const generation=++differenceGeneration;document.getElementById("formal-comparison").innerHTML='<p class="muted">Loading exact consumed Difference comparison…</p>';const value=await loadChunk(entry.chunk),planEntries=value.plan_indices.map(index=>DATA.plans.find(plan=>plan.plan_index===index)),plans=await Promise.all(planEntries.map(plan=>loadChunk(plan.chunk)));if(generation!==differenceGeneration){releaseChunk(entry.chunk);planEntries.forEach(plan=>releaseChunk(plan.chunk));return;}releaseChunk(entry.chunk);planEntries.forEach(plan=>releaseChunk(plan.chunk));const ids=new Set(Object.values(value.planned).flat()),planned=Object.fromEntries(["connection","parameter","forwarding"].map(family=>[family,plans.flatMap(plan=>[...plan.connection_transitions,...plan.parameter_transitions,...plan.forwarding_transitions].filter(item=>item.family===family&&ids.has(item.transition_id)).map(item=>({...item,plan_label:plan.label})))])),formalConnection=value.formal.connection_added+value.formal.connection_removed,parameterHeld=Math.max(0,value.formal.parameter_patches-planned.parameter.length),routeDelta=value.formal.route_patches-planned.forwarding.length;document.getElementById("formal-comparison").innerHTML=`<h3>Δ${value.difference_id} ${esc(value.interval)}</h3><p>${fmt(value.previous_timestamp)} → ${fmt(value.current_timestamp)}</p><div class="summary-flow"><button class="summary-node" data-summary-view="connections"><span class="muted">Formal Connection add/remove</span><strong>${number(formalConnection)}</strong><small>→ ${number(planned.connection.length)} emitted Connection intents</small></button><button class="summary-node" data-summary-view="parameters"><span class="muted">Raw Parameter patches</span><strong>${number(value.formal.parameter_patches)}</strong><small>→ ${number(planned.parameter.length)} emitted · ${number(parameterHeld)} not individually emitted</small></button><button class="summary-node" data-summary-view="forwarding"><span class="muted">Formal Route patches</span><strong>${number(value.formal.route_patches)}</strong><small>→ ${number(planned.forwarding.length)} nominal-time intents · net Δ ${number(routeDelta)} (may include delayed FOLLOW)</small></button></div><details><summary>Exact formal category counts</summary>${table(["Formal category","Count"],Object.entries(value.formal))}</details>`;const rows=["connection","parameter","forwarding"].flatMap(family=>planned[family].map(item=>[family,item.plan_label,transitionText(item)]));document.getElementById("planned-comparison").innerHTML=`<h3>Published logical intents</h3><div class="summary-flow">${["connection","parameter","forwarding"].map(family=>ratioNode(family,planned[family].length,null,"click to inspect exact objects",family==="connection"?"connections":family==="parameter"?"parameters":"forwarding")).join("")}</div><details><summary>Exact transition drill-down</summary>${table(["Family","Plan","Published fact"],rows)}</details>`;wireSummaryNavigation();const first=[...planned.connection,...planned.forwarding,...planned.parameter][0];if(first){nominalSelect.value=first.nominal_time;if(grouped.some(group=>group.nominal_time===first.nominal_time))renderNominalSummary();if(first.family==="forwarding")setRouteSpatial(first,first.transition_id);else {const connection=connectionFromRef(first.connection_ref);setSpatial({kind:"connection",time:first.nominal_time,nodes:[connection.connection_id.endpoint_a,connection.connection_id.endpoint_b],title:`Δ${value.difference_id} · ${connectionAlias(connection)}`});}}}
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
function drawBase(){const ctx=canvas.getContext("2d");canvas.dataset.cameraYaw=String(camera.yaw);canvas.dataset.cameraPitch=String(camera.pitch);ctx.clearRect(0,0,canvas.width,canvas.height);const radius=Math.min(canvas.width,canvas.height)*.38,gradient=ctx.createRadialGradient(canvas.width*.45,canvas.height*.42,12,canvas.width/2,canvas.height/2,radius);gradient.addColorStop(0,"#27739d");gradient.addColorStop(1,"#08233a");ctx.fillStyle=gradient;ctx.beginPath();ctx.arc(canvas.width/2,canvas.height/2,radius,0,Math.PI*2);ctx.fill();for(let lat=-60;lat<=60;lat+=30)drawPolyline(Array.from({length:73},(_,i)=>sphere(lat,-180+i*5)),"#8dc6dd44");for(let lon=-150;lon<180;lon+=30)drawPolyline(Array.from({length:37},(_,i)=>sphere(-90+i*5,lon)),"#8dc6dd44");}
function drawZoneFootprint(zone){drawPolyline(zoneBoundary(zone),"#d7f171",4,false,.95);const center=zone.representative_point;drawPoint(sphere(center.latitude_deg,center.longitude_deg,1.012),"#d7f171",7,zoneAlias(zone.zone_id),1);}
function contextSlices(time){const before=[...realSlices].reverse().find(value=>Date.parse(value.timestamp)<Date.parse(time))??realSlices[0];const after=realSlices.find(value=>Date.parse(value.timestamp)>=Date.parse(time))??realSlices.at(-1);return {before,after};}
function setSpatial(value){spatial=value;drawSpatial();}
function useSpatialSide(side){spatialSide=side;document.querySelectorAll(".spatial-side").forEach(value=>value.classList.toggle("active",value.dataset.side===side));}
function setConnectionSpatial(connection,transitionId,family="Connection"){const event=transitionById[transitionId]??connectionTransitions(connection.connection_ref)[0];useSpatialSide("after");setSpatial({kind:"connection",time:event?.nominal_time??realSlices[0].timestamp,nodes:[connection.connection_id.endpoint_a,connection.connection_id.endpoint_b],routeRef:null,target:null,transitionId:event?.transition_id??transitionId,eventOperation:event?.operation??null,title:`${family} · ${connectionAlias(connection)}`});}
function setRouteSpatial(route,transitionId){const event=transitionById[transitionId]??route.planned_transitions?.find(value=>value.transition_id===transitionId)??route.planned_transitions?.[0]??route;useSpatialSide("after");setSpatial({kind:"route",time:event?.nominal_time??realSlices[0].timestamp,nodes:[route.source,event?.next_hop].filter(Boolean),routeRef:route.route_ref,target:route.target,transitionId:event?.transition_id??transitionId,eventOperation:event?.operation??null,title:`Route · ${routeAlias(route.route_ref)}`});}
function restoreSelectedRoutePath(slice,routeRef,nextHopState=null){const [source,targetKind,targetIdentity]=String(routeRef).split("|");if(!source||!targetKind||!targetIdentity)return null;const targetReference=`${targetKind}|${targetIdentity}`,terminal=targetKind==="G"?targetIdentity:slice.zone_service_assignments.find(value=>value.zone_id===targetIdentity)?.service_satellite_id;if(!terminal)return null;const nextHops=nextHopState||new Map(slice.routes.map(([routeSource,target,nextHop])=>[`${routeSource}\u0000${target}`,nextHop])),nodes=[source],seen=new Set(nodes);let current=source;while(current!==terminal){const next=nextHops.get(`${current}\u0000${targetReference}`);if(!next||seen.has(next))return null;nodes.push(next);seen.add(next);current=next;}return {nodes};}
let plannedStateCache={key:null,nextHops:null};
function plannedRouteKey(routeReference){const route=routeFromRef(routeReference),targetReference=`${route.target.kind}|${route.target.kind==="G"?route.target.ground_station_id:route.target.zone_id}`;return `${route.source}\u0000${targetReference}`;}
function applyPlannedForwardingTransition(nextHops,transition){const key=plannedRouteKey(transition.route_ref);if(transition.operation==="removed")nextHops.delete(key);else nextHops.set(key,transition.next_hop);}
async function loadPlannedNextHops(time,boundary="after"){const key=`${time}|${boundary}`;if(plannedStateCache.key===key)return plannedStateCache.nextHops;const nextHops=new Map(),limit=Date.parse(time),includeBoundary=boundary==="after";for(const entry of DATA.plans){if(entry.start_timestamp&&Date.parse(entry.start_timestamp)>limit)break;const plan=await loadChunk(entry.chunk);for(const transition of plan.forwarding_transitions){const transitionTime=Date.parse(transition.nominal_time);if(transitionTime>limit||(!includeBoundary&&transitionTime===limit))continue;applyPlannedForwardingTransition(nextHops,transition);}releaseChunk(entry.chunk);}plannedStateCache={key,nextHops};return nextHops;}
let spatialSlice=null,spatialPlannedState=null,spatialLoadGeneration=0,spatialEdges=[];
function drawSpatial(){const samples=contextSlices(spatial.time),summary=samples[spatialSide],generation=++spatialLoadGeneration;document.getElementById("spatial-detail").innerHTML=`<p class="muted">Loading exact retained formal Slice ${summary.slice_id}${spatial.kind==="route"?` and planned state ${spatialSide} the selected boundary`:""}…</p>`;const slicePromise=spatialSlice?.slice_id===summary.slice_id?Promise.resolve(spatialSlice):loadChunk(summary.chunk),plannedPromise=spatial.kind==="route"?loadPlannedNextHops(spatial.time,spatialSide):Promise.resolve(null);Promise.all([slicePromise,plannedPromise]).then(([slice,planned])=>{if(generation!==spatialLoadGeneration){if(spatialSlice?.slice_id!==summary.slice_id)releaseChunk(summary.chunk);return;}if(spatialSlice?.slice_id!==summary.slice_id)releaseChunk(summary.chunk);spatialSlice=slice;spatialPlannedState=planned;drawSpatialSlice(slice,planned,samples);}).catch(error=>{if(generation===spatialLoadGeneration)document.getElementById("spatial-detail").textContent=`FAILED: ${error.message}`;});}
function connectionForHop(slice,first,second){const matches=slice.connections.filter(value=>{const id=value.connection_id;return (id.endpoint_a===first&&id.endpoint_b===second)||(id.endpoint_a===second&&id.endpoint_b===first);});return matches.length===1?matches[0]:null;}
function pathMetrics(slice,nodes){let delay=0,connections=[];for(let i=1;i<nodes.length;i++){const connection=connectionForHop(slice,nodes[i-1],nodes[i]);if(!connection)return null;connections.push(connection.connection_ref);const forward=connection.connection_id.endpoint_a===nodes[i-1]?connection.endpoint_a_to_b:connection.endpoint_b_to_a;delay+=Number(forward.delay_us);}return {hops:nodes.length-1,delay,connections};}
function drawPathEdge(a,b,color,width,dashed,connectionRef){if(!a||!b)return;drawSegment(a,b,color,width,dashed,.96);if(connectionRef){const p=project(a),q=project(b);spatialEdges.push({p,q,connectionRef});}}
function drawSpatialSlice(slice,plannedNextHops,samples){
  const positions=Object.fromEntries(slice.positions.map(value=>[value.node_id,value])),point=id=>positions[id]?ecef(positions[id].ecef_m):null;
  drawBase();spatialEdges=[];
  DATA.topology.nodes.filter(value=>value.kind==="satellite").forEach(value=>drawPoint(point(value.node_id),"#8cb7d6",2,null,.45));
  let highlighted=[...spatial.nodes],targetDetail="",sourceNode=null,terminalNode=null;
  if(spatial.kind==="group"){
    const values=spatial.groupValues||[],mode=spatial.playbackMode||"overlay",connectionEvents=values.filter(value=>value.family==="connection"),parameterEvents=values.filter(value=>value.family==="parameter"),forwardingEvents=values.filter(value=>value.family==="forwarding"),drawnConnections=new Set(),changedNodes=new Set();
    if(mode==="formal"||mode==="overlay")for(const connection of slice.connections){const id=connection.connection_id;drawSegment(point(id.endpoint_a),point(id.endpoint_b),"#527a9588",1,false,.13);}
    if(mode==="planned"||mode==="overlay")for(const event of [...connectionEvents,...parameterEvents]){const connection=connectionFromRef(event.connection_ref),id=connection.connection_id,color=event.family==="parameter"?"#ffd166":event.operation==="withdraw"?"#ff8f70":"#5ee5e5",formalConnection=connectionForHop(slice,id.endpoint_a,id.endpoint_b);drawPathEdge(point(id.endpoint_a),point(id.endpoint_b),color,3,event.operation==="withdraw",formalConnection?.connection_ref??event.connection_ref);drawnConnections.add(event.connection_ref);changedNodes.add(id.endpoint_a);changedNodes.add(id.endpoint_b);}
    if(mode==="planned"||mode==="overlay")for(const event of forwardingEvents.slice(0,180)){changedNodes.add(event.source);if(event.next_hop){changedNodes.add(event.next_hop);const connection=connectionForHop(slice,event.source,event.next_hop);if(connection&&!drawnConnections.has(connection.connection_ref)){drawPathEdge(point(event.source),point(event.next_hop),"#5dff8b",2,true,connection.connection_ref);drawnConnections.add(connection.connection_ref);}}}
    highlighted=[...changedNodes].slice(0,120);
    const clippedForwarding=Math.max(0,forwardingEvents.length-180);
    targetDetail=`<div class="visual-primary"><p><strong>${esc(mode==="overlay"?"Formal + planned change overlay":mode==="formal"?"Formal retained network":"Planned transition changes")}</strong></p><p>Connection changes: <strong>${number(connectionEvents.length)}</strong> · Parameter targets: <strong>${number(parameterEvents.length)}</strong> · Forwarding changes: <strong>${number(forwardingEvents.length)}</strong></p><p>Highlighted Connection/Parameter edges are exact identities. Forwarding density draws at most 180 exact changed first-hop edges${clippedForwarding?` (${number(clippedForwarding)} additional changes represented by the count)`:""}; select a Route for its complete exact P/Q paths.</p></div>`;
  }else if(spatial.kind==="route"){
    const formalPath=restoreSelectedRoutePath(slice,spatial.routeRef),plannedPath=plannedNextHops?restoreSelectedRoutePath(slice,spatial.routeRef,plannedNextHops):null,formalMetrics=formalPath?pathMetrics(slice,formalPath.nodes):null,plannedMetrics=plannedPath?pathMetrics(slice,plannedPath.nodes):null,formalEdges=new Set(formalMetrics?.connections||[]),plannedEdges=new Set(plannedMetrics?.connections||[]);
    if(formalPath){for(let i=1;i<formalPath.nodes.length;i++){const connection=connectionForHop(slice,formalPath.nodes[i-1],formalPath.nodes[i]),shared=connection&&plannedEdges.has(connection.connection_ref);drawPathEdge(point(formalPath.nodes[i-1]),point(formalPath.nodes[i]),shared?"#e879f9":"#69a9ff",shared?5:3,false,connection?.connection_ref);}}
    if(plannedPath){for(let i=1;i<plannedPath.nodes.length;i++){const connection=connectionForHop(slice,plannedPath.nodes[i-1],plannedPath.nodes[i]);if(connection&&formalEdges.has(connection.connection_ref))continue;drawPathEdge(point(plannedPath.nodes[i-1]),point(plannedPath.nodes[i]),"#5dff8b",3,true,connection?.connection_ref);}}
    highlighted=[...new Set([...(formalPath?.nodes||[]),...(plannedPath?.nodes||[])])];sourceNode=spatial.nodes[0]??formalPath?.nodes?.[0]??plannedPath?.nodes?.[0]??null;terminalNode=formalPath?.nodes?.at(-1)??plannedPath?.nodes?.at(-1)??null;const inferredDecision=!formalPath&&!plannedPath?"REMOVE":formalPath&&plannedPath&&formalMetrics?.connections.join("|")===plannedMetrics?.connections.join("|")?"FOLLOW":plannedPath?"HOLD":"REMOVE",decision=stableForwarding?inferredDecision:(spatial.eventOperation==="removed"?"REMOVE":"FOLLOW"),hopStretch=formalMetrics&&plannedMetrics?plannedMetrics.hops-formalMetrics.hops:null,delayStretch=formalMetrics&&plannedMetrics?plannedMetrics.delay-formalMetrics.delay:null,forwardingConfig=config.forwarding_stability||{},immediateResult=hopStretch!==null&&((present(forwardingConfig.immediate_hop_gain)&&hopStretch>=Number(forwardingConfig.immediate_hop_gain))||(present(forwardingConfig.immediate_delay_gain_us)&&delayStretch>=Number(forwardingConfig.immediate_delay_gain_us))),normalResult=hopStretch!==null&&((present(forwardingConfig.minimum_hop_gain)&&hopStretch>=Number(forwardingConfig.minimum_hop_gain))||(present(forwardingConfig.minimum_delay_gain_us)&&delayStretch>=Number(forwardingConfig.minimum_delay_gain_us)));
    const stableEvidence=stableForwarding?`<p>Immediate threshold met: <strong>${esc(String(immediateResult))}</strong> · normal threshold met: <strong>${esc(String(normalResult))}</strong> · persistence: <strong>${number(forwardingConfig.persistence_seconds)} s</strong> · maximum hold: <strong>${number(forwardingConfig.maximum_hold_seconds)} s</strong></p>`:`<p>Configured behavior: <strong>faithful formal-follow / always FOLLOW</strong>; HOLD thresholds are not active.</p>`;
    targetDetail+=`<div class="visual-primary"><p><strong>${esc(decision)}</strong> · Q formal-only <span style="color:#69a9ff">blue</span> · P planned-only <span style="color:#5dff8b">green dashed</span> · shared <span style="color:#e879f9">purple</span></p><p>Q: ${number(formalMetrics?.hops)} hops / ${number(formalMetrics?.delay)} µs · P: ${number(plannedMetrics?.hops)} hops / ${number(plannedMetrics?.delay)} µs</p><p>hop stretch P−Q: <strong>${number(hopStretch)}</strong> · delay stretch P−Q: <strong>${number(delayStretch)} µs</strong></p>${stableEvidence}</div><details><summary>Exact route-boundary and audit evidence</summary><p>The planned state is reconstructed ${spatialSide==="before"?"immediately before (the selected transition is not applied)":"after applying all transitions at"} ${fmt(spatial.time)}. For ADD/CHANGED/REMOVE this is the exact published event boundary, while globe positions remain the separate retained ${spatialSide} formal sample.</p><p class="muted">Candidate/divergence start timestamps are Planner-private and are not invented. Both paths use retained formal routes and published ForwardingTransitions only. No third Route is constructed.</p></details>`;
    if(spatial.target?.kind==="Z"){
      const zone=zonesById[spatial.target.zone_id];
      if(zone)drawZoneFootprint(zone);
      targetDetail+=`<p><strong>Route source</strong><br>${esc(nodeAlias(sourceNode))}${sourceNode?canonical(sourceNode):""}</p><p><strong>Semantic target</strong><br>${esc(zoneAlias(spatial.target.zone_id))}${canonical(spatial.target.zone_id)}</p><p><strong>Current service/path-terminal satellite</strong><br>${esc(nodeAlias(terminalNode))}${terminalNode?canonical(terminalNode):""}</p><p class="muted">The Zone identity remains the semantic target. The formal Zone rectangle and label are drawn on Earth; the actual path ends at its real node terminal. No fake satellite-to-Zone network link is drawn.</p>`;
    }else if(spatial.target?.kind==="G"){
      if(!highlighted.includes(spatial.target.ground_station_id))highlighted.push(spatial.target.ground_station_id);
      targetDetail+=`<p><strong>Route source</strong><br>${esc(nodeAlias(sourceNode))}${sourceNode?canonical(sourceNode):""}</p><p><strong>Semantic target: GroundStation</strong><br>${esc(nodeAlias(spatial.target.ground_station_id))}${canonical(spatial.target.ground_station_id)}</p><p><strong>Actual path terminal</strong><br>${esc(nodeAlias(terminalNode))}${terminalNode?canonical(terminalNode):""}</p><p class="muted">This RouteKey has a GroundStation semantic target; no Zone footprint is expected.</p>`;
    }
  }else if(highlighted.length>=2){const connection=connectionForHop(slice,highlighted[0],highlighted[1]);drawPathEdge(point(highlighted[0]),point(highlighted[1]),"#ffd166",4,false,connection?.connection_ref);}
  highlighted.forEach(id=>drawPoint(point(id),"#72f1e1",spatial.kind==="group"?4:7,spatial.kind==="group"?null:nodeAlias(id)));
  if(sourceNode)drawPoint(point(sourceNode),"#ff8f70",8,`Source · ${nodeAlias(sourceNode)}`);if(terminalNode)drawPoint(point(terminalNode),"#ffd166",8,`Path terminal · ${nodeAlias(terminalNode)}`);
  document.getElementById("spatial-detail").innerHTML=`<h3>${esc(spatial.title)}</h3><div class="visual-primary"><p><strong>Previous formal Slice sample</strong><br>S${samples.before.slice_id} @ ${fmt(samples.before.timestamp)}</p><p><strong>Planner nominal transition</strong><br>${fmt(spatial.time)}</p><p><strong>Next formal Slice sample</strong><br>S${samples.after.slice_id} @ ${fmt(samples.after.timestamp)}</p></div><p><strong>Globe position mode</strong><br>${spatialSide==="before"?"Before":"After"} retained formal Slice ${slice.slice_id}${spatial.kind==="route"?` · planned state ${spatialSide} event boundary`:""}</p>${targetDetail}<details><summary>Exact sampled spatial identities</summary><p class="muted">Positions are exact retained Slice samples. The Tool never interpolates a midpoint position, reruns a Model, or draws a satellite-to-Zone network edge. Click a highlighted network edge to open its Connection evidence.</p>${highlighted.map(id=>`<span title="${esc(id)}">${esc(nodeAlias(id))}</span>${canonical(id)}`).join("")}</details>`;
}
document.querySelectorAll(".spatial-side").forEach(button=>button.addEventListener("click",()=>{useSpatialSide(button.dataset.side);drawSpatial();}));
document.getElementById("reset-globe").addEventListener("click",()=>{camera={yaw:-.65,pitch:.28,drag:null};drawSpatial();});
function segmentDistance(x,y,p,q){const dx=q[0]-p[0],dy=q[1]-p[1],length=dx*dx+dy*dy,t=length?Math.max(0,Math.min(1,((x-p[0])*dx+(y-p[1])*dy)/length)):0,px=p[0]+t*dx,py=p[1]+t*dy;return Math.hypot(x-px,y-py);}
canvas.addEventListener("pointerdown",event=>{camera.drag=[event.clientX,event.clientY,false];canvas.setPointerCapture(event.pointerId);});
canvas.addEventListener("pointermove",event=>{if(!camera.drag)return;const dx=event.clientX-camera.drag[0],dy=event.clientY-camera.drag[1];if(Math.hypot(dx,dy)>2)camera.drag[2]=true;camera.yaw+=dx*.008;camera.pitch=Math.max(-1.3,Math.min(1.3,camera.pitch+dy*.008));camera.drag[0]=event.clientX;camera.drag[1]=event.clientY;if(camera.drag[2])drawSpatial();});
canvas.addEventListener("pointerup",event=>{const moved=camera.drag?.[2];camera.drag=null;if(moved)return;const bounds=canvas.getBoundingClientRect(),x=(event.clientX-bounds.left)*canvas.width/bounds.width,y=(event.clientY-bounds.top)*canvas.height/bounds.height,match=spatialEdges.map(edge=>({edge,distance:segmentDistance(x,y,edge.p,edge.q)})).sort((a,b)=>a.distance-b.distance)[0];if(match&&match.distance<12)openConnectionRef(match.edge.connectionRef);});

spatialReady=true;drawBase();document.getElementById("nominal-summary").innerHTML='<p class="muted">Choose a nominal time to load only its Plan chunk.</p>';document.getElementById("spatial-detail").innerHTML='<p class="muted">Choose a transition to load one retained formal Slice.</p>';
"""


__all__ = ["render_planner_page"]
