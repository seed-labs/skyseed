"""Render the curated Runner projection as one standalone offline page."""

from __future__ import annotations

from typing import Any

from tools._html import json_for_script, standalone_page


def render_runner_page(data: dict[str, Any]) -> str:
    """Return dependency-free bilingual HTML for one Runner inspection."""

    body = r"""
<style>
.language-switch { display:flex; justify-content:flex-end; gap:6px; margin:-8px 0 14px; }
.language-switch button { min-width:58px; }
.language-switch button.active { border-color:var(--cyan); background:#17415c; }
.runner-note { border-left:4px solid var(--cyan); }
.overview { grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); }
.overview .stat strong { font-size:20px; overflow-wrap:anywhere; }
.outcome-completed { color:#042514; background:var(--pass); }
.outcome-interrupted { color:#2f2300; background:var(--warn); }
.outcome-failed, .outcome-cleanup-failed { color:#310707; background:var(--fail); }
.reading-guide { margin:18px 0 0; }
.guide-priority { color:var(--cyan); }
.guide-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:10px 18px; margin-top:14px; }
.guide-entry { border-left:2px solid var(--line); padding-left:12px; }
.guide-entry dt { font-family:ui-monospace,monospace; font-weight:800; color:var(--blue); }
.guide-entry dd { margin:3px 0 0; color:var(--muted); }
.graph-card { overflow:hidden; }
.graph-scroll { overflow-x:auto; padding-bottom:8px; }
.graph-content { min-width:1040px; }
.graph-row { display:grid; grid-template-columns:116px minmax(860px,1fr); align-items:stretch; }
.graph-group-title { margin:16px 0 4px; padding:6px 10px; border-left:3px solid var(--cyan); color:var(--ink); font-weight:800; background:#132a4366; }
.graph-axis-label, .graph-lane-label { padding:8px 12px 8px 0; color:var(--muted); font-weight:750; text-align:right; }
.graph-axis { position:relative; height:42px; border-bottom:1px solid var(--line); }
.graph-tick { position:absolute; bottom:0; height:100%; border-left:1px solid var(--line); }
.graph-tick span { position:absolute; left:0; bottom:5px; transform:translateX(-50%); color:var(--muted); font:12px ui-monospace,monospace; white-space:nowrap; }
.graph-tick.tick-first span { transform:none; }
.graph-tick.tick-last span { transform:translateX(-100%); }
.graph-plot { position:relative; min-height:54px; border-bottom:1px solid #29496680; background-image:linear-gradient(90deg,#29496645 1px,transparent 1px); background-size:20% 100%; }
.graph-bar { position:absolute; z-index:2; overflow:visible; white-space:nowrap; text-align:left; color:var(--ink); font:10px/1.2 ui-monospace,monospace; box-shadow:0 3px 12px #0008; }
.graph-bar.phase { top:10px; height:32px; padding:8px; border:1px solid var(--blue); border-radius:9px; background:#315b7b; }
.graph-bar.work { top:17px; height:20px; padding:3px 6px; border:1px solid var(--cyan); border-radius:5px; background:#624a88; }
.graph-bar.work.micro-span { min-width:0; padding:0; border:0; border-radius:1px; }
.graph-bar.kind-main_activation { background:#8b5d25; }
.graph-foundation-notch { position:absolute; top:10px; z-index:4; width:12px; height:32px; padding:0; border:0; border-radius:0; background:transparent; transform:translateX(-50%); }
.graph-foundation-notch::before { content:""; position:absolute; left:5px; top:2px; bottom:2px; border-left:2px solid var(--warn); }
.graph-foundation-notch::after { content:""; position:absolute; left:2px; top:0; border-left:4px solid transparent; border-right:4px solid transparent; border-top:6px solid var(--warn); }
.graph-point.graph-end-cap { position:absolute; top:14px; z-index:4; width:16px; height:26px; padding:0; border:0; border-radius:0; background:transparent; box-shadow:none; transform:translateX(-14px); overflow:visible; }
.graph-point.graph-end-cap::before { content:""; position:absolute; right:2px; top:3px; width:3px; height:20px; border-radius:2px; background:var(--cyan); box-shadow:0 0 0 1px var(--bg); }
.graph-bar:hover, .graph-bar:focus, .graph-point:hover, .graph-point:focus, .graph-foundation-notch:hover, .graph-foundation-notch:focus { z-index:6; outline:2px solid var(--cyan); }
.graph-legend { color:var(--muted); font-size:13px; margin:8px 0 12px; }
.graph-legend ul { margin:0; padding-left:20px; display:grid; gap:4px; }
.graph-legend strong { color:var(--ink); }
.graph-local-detail { margin-top:12px; min-height:150px; border:1px solid var(--line); border-radius:10px; padding:12px; background:#091827; }
.graph-local-detail-head { display:flex; flex-wrap:wrap; justify-content:space-between; gap:10px; align-items:center; }
.graph-local-detail-actions { display:flex; gap:7px; align-items:center; }
.graph-local-detail-actions button { padding:4px 8px; }
.graph-local-facts { display:flex; flex-wrap:wrap; gap:8px 18px; margin:9px 0; color:var(--muted); font-size:13px; }
.graph-local-facts strong { color:var(--ink); }
.graph-local-axis { position:relative; height:54px; margin:8px 2px 0; border-top:1px solid var(--line); background-image:linear-gradient(90deg,#29496645 1px,transparent 1px); background-size:25% 100%; }
.graph-local-axis-label { position:absolute; top:31px; color:var(--muted); font:11px ui-monospace,monospace; white-space:nowrap; }
.graph-local-axis-label.start { left:0; }
.graph-local-axis-label.end { right:0; }
.graph-local-bar { position:absolute; top:10px; height:16px; min-width:0; border-radius:3px; background:#624a88; }
.graph-local-bar.phase { height:20px; background:#315b7b; border:1px solid var(--blue); }
.graph-local-bar.main_activation { background:#8b5d25; }
.graph-local-point { position:absolute; top:7px; width:2px; height:24px; background:var(--cyan); transform:translateX(-1px); }
.graph-local-point.foundation { background:var(--warn); }
.binding-tree { display:grid; gap:10px; }
.binding-tree details { margin:0; }
.binding-group { border:1px solid var(--line); border-radius:10px; padding:8px 10px; background:#0a192a; }
.binding-group .binding-group { margin:8px 0 0 12px; background:#0d1e31; }
.binding-group-summary { display:flex; gap:10px; justify-content:space-between; align-items:center; }
.binding-count { color:var(--muted); font-size:12px; white-space:nowrap; }
.binding-node-list { display:grid; gap:6px; margin-top:8px; }
.binding-node { border-top:1px solid #29496680; padding:6px 0 0 10px; }
.binding-node-summary { display:grid; grid-template-columns:minmax(180px,1.2fr) repeat(3,minmax(90px,1fr)); gap:8px; align-items:center; }
.binding-node-short { color:var(--cyan); font-family:ui-monospace,monospace; overflow-wrap:anywhere; }
.binding-node-value { font-family:ui-monospace,monospace; overflow-wrap:anywhere; }
.timeline { position:relative; margin-left:10px; }
.timeline::before { content:""; position:absolute; left:8px; top:4px; bottom:4px; width:2px; background:var(--line); }
.timeline-item { position:relative; margin:0 0 12px 30px; padding:12px 14px; background:var(--panel); border:1px solid var(--line); border-radius:10px; scroll-margin-top:18px; }
.timeline-item::before { content:""; position:absolute; left:-27px; top:18px; width:10px; height:10px; border-radius:50%; background:var(--cyan); box-shadow:0 0 0 4px var(--bg); }
.timeline-item.timeline-highlight { border-color:var(--warn); box-shadow:0 0 0 3px #ffd16645; }
.timeline-head { display:flex; flex-wrap:wrap; gap:8px; align-items:center; justify-content:space-between; }
.category { display:inline-block; min-width:86px; padding:2px 8px; border:1px solid var(--line); border-radius:99px; color:var(--cyan); font-size:12px; font-weight:800; text-align:center; }
.timeline-time { color:var(--muted); font-family:ui-monospace,monospace; }
.timeline-boundary { overflow-wrap:anywhere; color:var(--blue); }
.filters button.active { border-color:var(--cyan); background:#17415c; }
.scroll { overflow:auto; }
.metric { font-variant-numeric:tabular-nums; }
.scope-grid { grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); }
.evidence pre { max-height:340px; overflow:auto; }
.binding-facts { display:grid; grid-template-columns:max-content minmax(220px,1fr); gap:4px 12px; margin:10px 0 0; }
.binding-facts dt { color:var(--muted); }
.binding-facts dd { margin:0; font-family:ui-monospace,monospace; overflow-wrap:anywhere; }
.cleaned-note { color:var(--muted); font-size:13px; }
@media (max-width:700px) {
  .graph-row { grid-template-columns:92px minmax(860px,1fr); }
  .graph-axis-label, .graph-lane-label { font-size:12px; }
  .binding-node-summary { grid-template-columns:1fr 1fr; }
}
</style>

<nav class="language-switch" aria-label="Presentation language">
  <button type="button" data-language="zh" class="active">中文</button>
  <button type="button" data-language="en">EN</button>
</nav>

<section class="card runner-note">
  <span id="outcome-badge" class="status"></span>
  <h2 id="intro-title"></h2>
  <p id="intro-copy"></p>
  <p class="cleaned-note" id="cleaned-note"></p>
</section>

<details class="card reading-guide">
  <summary><strong id="guide-title"></strong></summary>
  <div id="reading-guide"></div>
</details>

<section><h2 id="overview-title"></h2><div class="grid overview" id="overview"></div></section>

<section>
  <h2 id="timeline-title"></h2>
  <p class="muted" id="timeline-intro"></p>
  <div class="card graph-card">
    <h3 id="graph-title"></h3>
    <div class="graph-legend" id="graph-legend"></div>
    <div class="graph-scroll"><div id="graphical-timeline"></div></div>
    <div class="graph-local-detail" id="graph-local-detail" aria-live="polite"></div>
  </div>
</section>

<section>
  <h2 id="foundation-title"></h2>
  <p class="muted" id="foundation-intro"></p>
  <div class="grid overview" id="foundation-summary"></div>
</section>
<section class="card scroll"><h3 id="bindings-title"></h3><div id="bindings-table"></div></section>

<section><h2 id="timing-title"></h2><div class="grid stats" id="timing-stats"></div></section>
<section class="card scroll"><h3 id="activation-title"></h3><div id="activation-table"></div></section>
<section class="card scroll">
  <h3>Detailed timing / 详细计时</h3>
  <p class="muted">Formal Main only. Operation-family wall time includes request/ACK waits. Command, helper, barriers and operations are nested; their durations must not be added together.</p>
  <div id="operation-timing-table"></div>
  <h4>Parent / helper envelope (inclusive where named)</h4><div id="envelope-timing-table"></div>
  <h4>Pacing and unattributed residuals</h4><div id="pacing-timing-table"></div><div id="residual-timing-table"></div>
  <details><summary>Per-boundary counts, timestamps and lifecycle accounting</summary><div id="detailed-timing-evidence"></div></details>
</section>

<section class="card scroll" id="delivery-section"><h2>Delivery and Maintenance / 交付与后台维护</h2><p>One B0 and one fixed runtime origin. Main activates every formal boundary. Bounded Maintenance loads sealed successors and reclaims eligible retired data; retained historical evidence remains readable.</p><div id="delivery-summary"></div><div id="delivery-events"></div></section>

<section>
  <h2>Scale safety / 规模安全</h2>
  <p class="muted">Compact derived views; exact events remain in the durable JSONL source.</p>
  <div class="grid scope-grid" id="scale-safety"></div>
</section>

<section>
  <h2 id="dataplane-title"></h2>
  <div class="warning" id="dataplane-warning"></div>
  <div class="grid scope-grid" id="dataplane"></div>
</section>

<section>
  <h2 id="failures-title"></h2>
  <div class="grid scope-grid" id="failures-cleanup"></div>
</section>

<section>
  <h2 id="detailed-timeline-title"></h2>
  <p class="muted" id="detailed-timeline-intro"></p>
  <div class="controls filters" id="timeline-filters"></div>
  <div class="timeline" id="timeline"></div>
</section>

<section class="card runner-note">
  <h2 id="boundaries-title"></h2>
  <div class="grid scope-grid" id="evidence-boundaries"></div>
</section>

<details class="card"><summary id="raw-title"></summary><pre id="raw-json"></pre></details>
"""
    return standalone_page(
        title="BPF Runner 检查工具",
        eyebrow="运行后 · 离线 · 只读",
        body=body,
        script=_SCRIPT.replace("__RUNNER_DATA__", json_for_script(data)),
    )


_SCRIPT = r"""
const DATA=__RUNNER_DATA__;
const STRINGS={
  zh:{
    lang:"zh-CN",title:"BPF Runner 检查工具",eyebrow:"运行后 · 离线 · 只读",footer:"Skyseed v2 仓库内离线检查 artifact",
    introTitle:"Runner 生命周期运行后证据",introCopy:"本静态页面组合了 detached 的最终/最后已知 Runner 摘要与完整记录事件历史；它不是实时视图，也不具有控制行为。",cleanedNote:"cleanup 完成后，frontier 值是历史事实：“Last active” 不表示网络当前仍处于 active 状态。",
    guideTitle:"如何阅读 / How to read",guideIntro:"普通用户建议依次阅读 Overview、图形执行时间轴、Timing 和 Cleanup；raw JSON 与深层 evidence 主要用于 debugging。",
    guideTerms:[
      ["ACTIVE","最后已经真正生效的 nominal network state。cleanup 后它只是一项历史事实。"],
      ["Runner artifact","生成时检查序列关系并密封的有限交付；入口核对身份/hash，Preparation 或 Maintenance 打开并完整解码该批。"],
      ["Headroom","准备完成时距 nominal boundary 尚余的时间；正值越大，准备余量越多。"],
      ["Runtime foundation","Runner 已冻结并 attach 的具体 live Compose deployment instances。"],
      ["PID / netns binding","节点容器 incarnation 的进程与 network namespace identity，用于 runtime correctness 与 stale detection。"],
      ["Foundation token","由 Compose project 与全部 frozen binding facts 派生的本次 Runner foundation identity。"],
      ["Main","唯一负责在 boundary 改变 active network semantics 的执行角色。"],
      ["Generation","current default tc-BPF dataplane 的内部转发版本机制，不是通用 Runner architecture concept。"],
      ["Physical lifecycle","新 veth 的创建与撤销 veth 的删除都由 Main 在 activation boundary 执行。"],
      ["Start lag / Completion lag","Main 相对 nominal boundary 开始激活/完成激活的实际时间偏差。"]
    ],
    overviewTitle:"概览",timelineTitle:"图形执行时间轴",timelineIntro:"横轴始终按 Runner 实际经过时间绘制，便于同时理解生命周期阶段与真正发生的工作。",graphTitle:"实际执行时间与 nominal time",graphGuide:["Lifecycle 横条表示 Runner 当时处于什么阶段，不表示 CPU 一直忙碌。","Work 横条的位置和长度表示工作实际发生的时间和耗时。","Preparation 完成时 boundary 0 已实际激活；之后 Main 从 boundary 1 开始。","Preparation 内的分隔表示 runtime foundation 已建立完成，后续开始提前准备未来状态。","很短的 work 在全局比例下可能只是一条细线；悬停或点击可在局部细节中查看。"],detailedTimelineTitle:"详细 Timeline 事件",detailedTimelineIntro:"用于 drill-down/debug；图形项的局部细节可继续定位到对应事件。",
    timingTitle:"Timing",activationTitle:"Main 激活 timing",foundationTitle:"Runtime foundation / 运行期绑定",foundationIntro:"Runner 在 Preparation 中冻结的 live deployment instance bindings；本 section 不重新查询 Docker 或 kernel，并与 default tc-BPF dataplane realization evidence 分开。",bindingsTitle:"Frozen node bindings",
    dataplaneTitle:"当前 default tc-BPF dataplane",dataplaneWarning:"仅展示正常 Runner lifecycle 已记录的证据。本 Tool 不运行 <code>ip</code>、<code>tc</code>、<code>bpftool</code>、Docker inspection、packet capture 或 traffic generation。",
    failuresTitle:"失败与 cleanup",boundariesTitle:"证据边界",rawTitle:"Raw detached Runner snapshot 与完整 event history",
    overviewLabels:["Runtime 结果","Lifecycle 状态","执行模式","执行契约","Last active frontier","Artifact end","Artifact boundaries","正式 Main activations","初始 preparation","Cleanup"],
    overviewDetails:{historical:"cleanup 后的历史事实",lastActivation:"最后一次成功 activation",exit:v=>`exit ${v}`},
    timingLabels:["初始 preparation","最近 Main activation","最大 Main activation","Cleanup"],
    activationHeaders:["Nominal boundary","Start lag","Duration","Completion lag","已完成"],
    foundationLabels:["Compose project","Foundation token","Expected nodes","Bound nodes"],bindingHeaders:["Node","PID","netns","Container"],bindingFields:{nodeId:"完整 NodeId",service:"Service",containerId:"Container ID",containerName:"Container name",imageId:"Image ID",startedAt:"StartedAt"},bindingGroups:{groundStations:"地面站",satellites:"卫星",dynamicShell:"Dynamic Shell",other:"其他节点",orbit:id=>`轨道 ${id}`,count:n=>`${n} 个节点`},noBindings:"没有 frozen runtime bindings。",
    dataplaneCards:["Dataplane foundation evidence","最新 capacity audit","最新 activation evidence"],
    failureCards:["Primary runtime error","Runtime cleanup 结果","Runner failure events","Capacity failures","Dataplane cleanup evidence"],
    boundaryCards:[
      ["Runner Inspection","Preparation、Main、frontiers、timing、capacity、failure 与 cleanup。"],
      ["Kernel/dataplane inspection","不进行新的 kernel reconstruction；只展示 Runner 工作期间已经返回的证据。"],
      ["Traffic telemetry","本 Tool 不收集 packet/byte/flow/path telemetry。"],
      ["Active validation","本 Tool 不生成 synthetic traffic，也不执行 expected-vs-actual assertion。"]
    ],
    categories:{Maintenance:"Maintenance",All:"全部",Preparation:"Preparation",Main:"Main",Failure:"Failure",Cleanup:"Cleanup"},
    graphGroups:{lifecycle:"Lifecycle",work:"Runner work"},
    graphRows:{batch_loading:"Load / prewrite batch",maintenance_reclaim:"Reclaim eligible retired data",preparation_phase:"Preparation",automatic_run_phase:"Automatic run",cleanup_phase:"Cleanup",main_activation:"切换当前网络状态"},
    graphKinds:{batch_loading:"Background batch load / prewrite",maintenance_reclaim:"Bounded Maintenance reclaim",preparation_phase:"Preparation 生命周期阶段",automatic_run_phase:"Automatic scheduling/waiting 生命周期阶段",cleanup_phase:"Cleanup 生命周期阶段",main_activation:"Main activation 工作",runtime_foundation_ready:"Runtime foundation 已就绪"},
    localDetail:{title:"局部细节",hint:"悬停图中的 work、foundation 分隔；点击可固定。",pinned:"已固定",unpin:"取消固定",event:"查看详细事件",window:"局部时间窗口",actual:"实际事件时间",duration:"工作耗时",boundary:"Nominal time",generation:"Generation",none:"—"},
    eventHeadlines:{preparation_start:"Preparation 开始",preparation_end:"初始 preparation 完成",runtime_foundation_ready:"Runtime foundation ready",automatic_run_start:"Automatic execution 开始",automatic_run_end:"Automatic execution 完成",boundary_scheduled:"Nominal boundary 已调度",activation_end:"Main activation 完成",capacity_audit_failure:"Capacity audit 失败",failure:"Runner failure 已记录",runtime_cleanup_end:"Dataplane cleanup 完成",cleanup_end:"Runner cleanup 完成"},
    words:{yes:"是",no:"否",none:"无",unknown:"未知",bootstrap:"bootstrap",boundary:"Boundary",nominalBoundary:"Nominal boundary",predecessor:"predecessor",headroom:"headroom",duration:"Duration",connections:"connections",nextAction:"下一动作",lastActive:"Last active frontier",recorded:"已记录的 Runner lifecycle evidence",in:"用时",generation:"Generation",details:"记录详情",empty:"没有记录值。",noEvents:"此分类没有事件。",bounded:"bounded full range",notAvailable:"尚不可用",actual:"实际"},
    enums:{completed:"已完成",interrupted:"已中断",failed:"失败","cleanup-failed":"cleanup 失败",cleaned:"已清理",prepared:"已准备",automatic:"automatic",manual:"manual","sealed-precomputed-only":"sealed precomputed only","exact-next-safe":"exact-next safe"}
  },
  en:{
    lang:"en",title:"BPF Runner Inspection Tool",eyebrow:"Post-run · offline · read-only",footer:"Skyseed v2 repository-local offline inspection artifact",
    introTitle:"Post-run Runner lifecycle evidence",introCopy:"This static page combines a detached final/last-known Runner summary with its complete recorded event history. It is not live and has no controller behavior.",cleanedNote:"After cleanup, frontier values are historical: “Last active” never means that the network is still active.",
    guideTitle:"How to read / 如何阅读",guideIntro:"Most users should read Overview, the graphical timeline, Timing, and Cleanup first. Raw JSON and deep evidence are mainly for debugging.",
    guideTerms:[
      ["ACTIVE","The last nominal network state that actually became active. After cleanup it is historical only."],
      ["Runner artifact","A finite sealed delivery: the writer checks sequence relationships; entry verifies identity/hash; Preparation or Maintenance opens and fully decodes the batch."],
      ["Headroom","Time remaining before the nominal boundary when preparation completed; a larger positive value means more margin."],
      ["Runtime foundation","The concrete live Compose deployment instances frozen and attached by the Runner."],
      ["PID / netns binding","The process and network-namespace identity of a node container incarnation, used for runtime correctness and stale detection."],
      ["Foundation token","This Runner foundation identity derived from the Compose project and all frozen binding facts."],
      ["Main","The sole execution role that changes active network semantics at a boundary."],
      ["Generation","An internal forwarding-version mechanism of the current default tc-BPF dataplane, not a general Runner architecture concept."],
      ["Physical lifecycle","Main creates new veths and deletes withdrawn veths inside the activation boundary."],
      ["Start lag / Completion lag","The actual timing offset between the nominal boundary and Main starting/completing activation."]
    ],
    overviewTitle:"Overview",timelineTitle:"Graphical execution timeline",timelineIntro:"The horizontal axis always preserves actual Runner elapsed time, showing lifecycle phases alongside work that really ran.",graphTitle:"Actual execution time and nominal time",graphGuide:["Lifecycle bars show which phase the Runner was in; they do not mean the CPU was continuously busy.","A work bar's position and length show when the work ran and how long it took.","Preparation completes with boundary 0 active; formal Main begins at boundary 1.","The separator inside Preparation means the runtime foundation is complete and advance preparation of future state can begin.","Very short work may appear as a thin line at the global scale; hover or click to inspect it in Local detail."],detailedTimelineTitle:"Detailed Timeline events",detailedTimelineIntro:"A drill-down/debug view. Local detail for a graphical item can continue to its recorded event.",
    timingTitle:"Timing",activationTitle:"Main activation timing",foundationTitle:"Runtime foundation / 运行期绑定",foundationIntro:"Live deployment instance bindings frozen by Runner Preparation. This section makes no fresh Docker or kernel query and remains separate from default tc-BPF dataplane realization evidence.",bindingsTitle:"Frozen node bindings",
    dataplaneTitle:"Current default tc-BPF dataplane",dataplaneWarning:"Recorded lifecycle evidence only. This Tool does not run <code>ip</code>, <code>tc</code>, <code>bpftool</code>, Docker inspection, packet capture, or traffic generation.",
    failuresTitle:"Failures & cleanup",boundariesTitle:"Evidence boundaries",rawTitle:"Raw detached Runner snapshot and event history",
    overviewLabels:["Runtime outcome","Lifecycle state","Execution mode","Execution contract","Last active frontier","Artifact end","Artifact boundaries","Formal Main activations","Initial preparation","Cleanup"],
    overviewDetails:{historical:"historical after cleanup",lastActivation:"last successful activation",exit:v=>`exit ${v}`},
    timingLabels:["Initial preparation","Recent Main activation","Max Main activation","Cleanup"],
    activationHeaders:["Nominal boundary","Start lag","Duration","Completion lag","Completed"],
    foundationLabels:["Compose project","Foundation token","Expected nodes","Bound nodes"],bindingHeaders:["Node","PID","netns","Container"],bindingFields:{nodeId:"Full NodeId",service:"Service",containerId:"Container ID",containerName:"Container name",imageId:"Image ID",startedAt:"StartedAt"},bindingGroups:{groundStations:"Ground Stations",satellites:"Satellites",dynamicShell:"Dynamic Shell",other:"Other nodes",orbit:id=>`Orbit ${id}`,count:n=>`${n} node${n===1?"":"s"}`},noBindings:"No frozen runtime bindings were recorded.",
    dataplaneCards:["Dataplane foundation evidence","Latest capacity audit","Latest activation evidence"],
    failureCards:["Primary runtime error","Runtime cleanup outcome","Runner failure events","Capacity failures","Dataplane cleanup evidence"],
    boundaryCards:[
      ["Runner Inspection","Preparation, Main, frontiers, timing, capacity, failure, and cleanup."],
      ["Kernel/dataplane inspection","No fresh kernel reconstruction; only evidence already returned during Runner work."],
      ["Traffic telemetry","This Tool collects no packet/byte/flow/path telemetry."],
      ["Active validation","This Tool performs no synthetic traffic or expected-vs-actual assertion."]
    ],
    categories:{Maintenance:"Maintenance",All:"All",Preparation:"Preparation",Main:"Main",Failure:"Failure",Cleanup:"Cleanup"},
    graphGroups:{lifecycle:"Lifecycle",work:"Runner work"},
    graphRows:{batch_loading:"Load / prewrite batch",maintenance_reclaim:"Reclaim eligible retired data",preparation_phase:"Preparation",automatic_run_phase:"Automatic run",cleanup_phase:"Cleanup",main_activation:"Activate current network state"},
    graphKinds:{batch_loading:"Background batch load / prewrite",maintenance_reclaim:"Bounded Maintenance reclaim",preparation_phase:"Preparation lifecycle phase",automatic_run_phase:"Automatic scheduling/waiting lifecycle phase",cleanup_phase:"Cleanup lifecycle phase",main_activation:"Main activation work",runtime_foundation_ready:"Runtime foundation ready"},
    localDetail:{title:"Local detail",hint:"Hover over work, the foundation separator; click to pin it.",pinned:"Pinned",unpin:"Unpin",event:"View detailed event",window:"Local time window",actual:"Actual event time",duration:"Work duration",boundary:"Nominal time",generation:"Generation",none:"—"},
    eventHeadlines:{preparation_start:"Preparation started",preparation_end:"Initial preparation completed",runtime_foundation_ready:"Runtime foundation ready",automatic_run_start:"Automatic execution started",automatic_run_end:"Automatic execution completed",boundary_scheduled:"Nominal boundary scheduled",activation_end:"Main activation completed",capacity_audit_failure:"Capacity audit failed",failure:"Runner failure recorded",runtime_cleanup_end:"Dataplane cleanup completed",cleanup_end:"Runner cleanup completed"},
    words:{yes:"yes",no:"no",none:"none",unknown:"unknown",bootstrap:"bootstrap",boundary:"Boundary",nominalBoundary:"Nominal boundary",predecessor:"predecessor",headroom:"headroom",duration:"Duration",connections:"connections",nextAction:"Next action",lastActive:"Last active frontier",recorded:"Recorded Runner lifecycle evidence",in:"in",generation:"Generation",details:"Recorded details",empty:"No recorded values.",noEvents:"No events in this category.",bounded:"bounded full range",notAvailable:"not yet available",actual:"actual"},
    enums:{completed:"completed",interrupted:"interrupted",failed:"failed","cleanup-failed":"cleanup failed",cleaned:"cleaned",prepared:"prepared",automatic:"automatic",manual:"manual","sealed-precomputed-only":"sealed precomputed only","exact-next-safe":"exact-next safe"}
  }
};

let currentLanguage="zh";
let selectedCategory="All";
let pinnedGraphDetail=null;
const esc=value=>String(value??"—").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtSeconds=value=>typeof value==="number"?`${value.toFixed(3)}s`:"—";
const fmtTime=value=>value===null||value===undefined?"—":String(value).replace("T"," ").replace("+00:00","Z");
const pretty=value=>JSON.stringify(value??null,null,2);
const s=()=>STRINGS[currentLanguage];
const enumText=value=>s().enums[value]??value??"—";
const yesNo=value=>value===true?s().words.yes:value===false?s().words.no:"—";
const card=(label,value,detail="")=>`<div class="card stat"><span class="muted">${esc(label)}</span><strong>${esc(value??"—")}</strong>${detail?`<span class="muted">${esc(detail)}</span>`:""}</div>`;
const table=(headers,rows)=>rows.length?`<table><thead><tr>${headers.map(value=>`<th>${esc(value)}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(value=>`<td>${esc(value??"—")}</td>`).join("")}</tr>`).join("")}</tbody></table>`:`<p class="muted">${esc(s().words.empty)}</p>`;
const shortBoundary=value=>value?fmtTime(value).split(" ").pop():null;

function setStaticText(){
  const text={"intro-title":s().introTitle,"intro-copy":s().introCopy,"cleaned-note":s().cleanedNote,"guide-title":s().guideTitle,"overview-title":s().overviewTitle,"timeline-title":s().timelineTitle,"timeline-intro":s().timelineIntro,"graph-title":s().graphTitle,"detailed-timeline-title":s().detailedTimelineTitle,"detailed-timeline-intro":s().detailedTimelineIntro,"timing-title":s().timingTitle,"activation-title":s().activationTitle,"foundation-title":s().foundationTitle,"foundation-intro":s().foundationIntro,"bindings-title":s().bindingsTitle,"dataplane-title":s().dataplaneTitle,"failures-title":s().failuresTitle,"boundaries-title":s().boundariesTitle,"raw-title":s().rawTitle};
  Object.entries(text).forEach(([id,value])=>document.getElementById(id).textContent=value);
  document.documentElement.lang=s().lang;
  document.title=s().title;
  document.querySelector("header h1").textContent=s().title;
  document.querySelector("header .eyebrow").textContent=s().eyebrow;
  document.querySelector("footer").textContent=s().footer;
  document.querySelector(".language-switch").setAttribute("aria-label",currentLanguage==="zh"?"显示语言":"Presentation language");
  document.getElementById("dataplane-warning").innerHTML=s().dataplaneWarning;
}

function renderGuide(){
  document.getElementById("reading-guide").innerHTML=`<p class="guide-priority">${esc(s().guideIntro)}</p><dl class="guide-grid">${s().guideTerms.map(([term,description])=>`<div class="guide-entry"><dt>${esc(term)}</dt><dd>${esc(description)}</dd></div>`).join("")}</dl>`;
}

function renderOverview(){
  const overview=DATA.overview;
  const badge=document.getElementById("outcome-badge");
  badge.textContent=enumText(overview.outcome).toUpperCase();
  badge.className=`status outcome-${overview.outcome}`;
  const l=s().overviewLabels;
  document.getElementById("overview").innerHTML=[
    card(l[0],enumText(overview.outcome),s().overviewDetails.exit(overview.exit_code)),card(l[1],enumText(overview.lifecycle_state)),card(l[2],enumText(overview.execution_mode)),
    card(l[3],enumText(overview.execution_contract)),card(l[4],fmtTime(overview.frontiers.last_active),overview.lifecycle_state==="cleaned"?s().overviewDetails.historical:s().overviewDetails.lastActivation),
    card(l[5],fmtTime(overview.frontiers.artifact_end)),
    card(l[6],overview.known_boundary_count??overview.artifact.boundary_count),card(l[7],overview.activation_count),card(l[8],fmtSeconds(overview.preparation_duration_seconds)),card(l[9],enumText(overview.cleanup_status),fmtSeconds(overview.cleanup_duration_seconds))
  ].join("");
}

function eventSummary(value){
  const d=value.details||{};const boundary=value.boundary_timestamp;const w=s().words;
  if(value.event==="boundary_scheduled")return `${w.boundary} ${boundary||w.unknown}`;
  if(value.event==="activation_end")return `${w.boundary} ${boundary||w.unknown}; ${w.duration} ${fmtSeconds(d.main_seconds)}`;
  if(value.event==="failure")return `${d.phase||w.unknown}: ${d.error_type||"Error"}: ${d.error||""}`;
  if(value.event==="capacity_audit_failure")return `${d.scope||w.unknown}: ${d.reason||""}`;
  if(value.event==="preparation_end"||value.event==="cleanup_end")return `${w.duration} ${fmtSeconds(d.duration_seconds)}`;
  if(value.event==="automatic_run_end")return `${w.lastActive} ${d.active_frontier||w.none}`;
  return w.recorded;
}

function renderTimelineFilters(){
  const categories=["All",...new Set(DATA.timeline.map(value=>value.category))];const filters=document.getElementById("timeline-filters");
  filters.innerHTML=categories.map(value=>`<button type="button" data-category="${esc(value)}" class="${value===selectedCategory?"active":""}">${esc(s().categories[value]??value)}</button>`).join("");
  filters.querySelectorAll("button").forEach(button=>button.addEventListener("click",()=>{selectedCategory=button.dataset.category;renderTimelineFilters();renderDetailedTimeline();}));
}

function renderDetailedTimeline(){
  const values=DATA.timeline.filter(value=>selectedCategory==="All"||value.category===selectedCategory);
  document.getElementById("timeline").innerHTML=values.length?values.map(value=>`<article id="timeline-event-${value.timeline_index}" class="timeline-item"><div class="timeline-head"><div><span class="category">${esc(s().categories[value.category]??value.category)}</span> <strong>${esc(s().eventHeadlines[value.event]??value.event)}</strong></div><span class="timeline-time">+${fmtSeconds(value.elapsed_seconds)}</span></div>${value.boundary_timestamp?`<div class="timeline-boundary">${esc(s().words.nominalBoundary)}: ${esc(fmtTime(value.boundary_timestamp))}</div>`:""}<p>${esc(eventSummary(value))}</p><details><summary>${esc(s().words.details)}</summary><pre>${esc(pretty(value.details))}</pre></details></article>`).join(""):`<p class="muted">${esc(s().words.noEvents)}</p>`;
}

function graphLabel(item){
  const time=shortBoundary(item.boundary_timestamp);const boundary=time?`B${time.slice(0,5)}`:null;const generation=item.generation!==null&&item.generation!==undefined?`G${item.generation}`:null;
  if(item.kind==="preparation_phase")return "prepare()";
  if(item.kind==="automatic_run_phase"||item.kind==="automatic_run_start")return "run()";
  if(item.kind==="cleanup_phase")return "cleanup()";
  if(item.kind==="runtime_foundation_ready")return "foundation";
  if(item.kind==="main_activation")return boundary||"activation";
  if(item.kind.includes("generation"))return generation||"generation";
  if(item.kind.includes("resources")||item.kind.includes("physical"))return "physical";
  return boundary||generation||item.kind;
}

function renderLocalDetail(payload=pinnedGraphDetail){
  const host=document.getElementById("graph-local-detail");const text=s().localDetail;
  if(!payload){host.innerHTML=`<strong>${esc(text.title)}</strong><p class="muted">${esc(text.hint)}</p>`;return;}
  const item=payload.item;const point=payload.point||null;const start=Math.max(0,Number(item.elapsed_start_seconds)||0);const end=Math.max(start,Number(item.elapsed_end_seconds)||start);const pointTime=point&&typeof point.elapsed_seconds==="number"?point.elapsed_seconds:null;const itemDuration=Math.max(0,end-start);
  let windowStart;let windowEnd;
  if(payload.role==="foundation"&&pointTime!==null){const radius=Math.max(.05,itemDuration*.12);windowStart=Math.max(start,pointTime-radius);windowEnd=Math.min(end,pointTime+radius);}
  else{const contentEnd=Math.max(end,pointTime??end);const span=Math.max(contentEnd-start,.001);const padding=Math.max(.002,span*.35);windowStart=Math.max(0,start-padding);windowEnd=contentEnd+padding;}
  const windowDuration=Math.max(windowEnd-windowStart,.000001);const visibleStart=Math.max(start,windowStart);const visibleEnd=Math.min(end,windowEnd);const barLeft=(visibleStart-windowStart)/windowDuration*100;const barWidth=Math.max(0,(visibleEnd-visibleStart)/windowDuration*100);const pointLeft=pointTime===null?null:Math.max(0,Math.min(100,(pointTime-windowStart)/windowDuration*100));
  const detailItem=point||item;const boundary=detailItem.boundary_timestamp||item.boundary_timestamp;const generation=detailItem.generation??item.generation;const target=detailItem.target_timeline_index??item.target_timeline_index;const title=s().graphKinds[detailItem.kind]??detailItem.kind;const actual=pointTime===null?`+${fmtSeconds(start)} – +${fmtSeconds(end)}`:`+${fmtSeconds(pointTime)}`;const localBarClass=`graph-local-bar ${item.visual} ${item.kind}`;
  host.innerHTML=`<div class="graph-local-detail-head"><div><strong>${esc(text.title)} · ${esc(title)}</strong>${pinnedGraphDetail?` <span class="status OBSERVED">${esc(text.pinned)}</span>`:""}</div><div class="graph-local-detail-actions">${pinnedGraphDetail?`<button type="button" data-local-unpin>${esc(text.unpin)}</button>`:""}<button type="button" data-local-event>${esc(text.event)}</button></div></div><div class="graph-local-facts"><span><strong>${esc(text.actual)}:</strong> ${esc(actual)}</span><span><strong>${esc(text.duration)}:</strong> ${esc(fmtSeconds(itemDuration))}</span><span><strong>${esc(text.boundary)}:</strong> ${esc(fmtTime(boundary))}</span><span><strong>${esc(text.generation)}:</strong> ${esc(generation??text.none)}</span><span><strong>${esc(text.window)}:</strong> +${esc(fmtSeconds(windowStart))} – +${esc(fmtSeconds(windowEnd))}</span></div><div class="graph-local-axis"><span class="${localBarClass}" style="left:${barLeft}%;width:${barWidth}%"></span>${pointLeft===null?"":`<span class="graph-local-point ${payload.role==="foundation"?"foundation":""}" style="left:${pointLeft}%"></span>`}<span class="graph-local-axis-label start">+${esc(fmtSeconds(windowStart))}</span><span class="graph-local-axis-label end">+${esc(fmtSeconds(windowEnd))}</span></div>`;
  host.querySelector("[data-local-event]").addEventListener("click",()=>focusTimelineItem(Number(target)));
  const unpin=host.querySelector("[data-local-unpin]");if(unpin)unpin.addEventListener("click",()=>{pinnedGraphDetail=null;renderLocalDetail(null);});
}

function bindGraphDetail(element,payload){
  element.addEventListener("mouseenter",()=>{if(!pinnedGraphDetail)renderLocalDetail(payload);});
  element.addEventListener("mouseleave",()=>{if(!pinnedGraphDetail)renderLocalDetail(null);});
  element.addEventListener("focus",()=>{if(!pinnedGraphDetail)renderLocalDetail(payload);});
  element.addEventListener("blur",()=>{if(!pinnedGraphDetail)renderLocalDetail(null);});
  element.addEventListener("click",()=>{pinnedGraphDetail=payload;renderLocalDetail(payload);});
}

function renderGraph(){
  const graph=DATA.graphical_timeline;const duration=Math.max(Number(graph.duration_seconds)||0,0.001);const ticks=Array.from({length:6},(_,index)=>duration*index/5);const detailItems=[];
  const tickHtml=ticks.map((value,index)=>`<div class="graph-tick ${index===0?"tick-first":index===ticks.length-1?"tick-last":""}" style="left:${value/duration*100}%"><span>+${fmtSeconds(value)}</span></div>`).join("");
  const groups=graph.groups.map(group=>{
    const rows=group.rows.map(row=>{
      const items=row.items.map(item=>{
        const start=Math.max(0,Number(item.elapsed_start_seconds)||0);const end=Math.max(start,Number(item.elapsed_end_seconds)||start);const left=Math.min(100,start/duration*100);const width=Math.max(0,(end-start)/duration*100);const actual=start===end?`+${fmtSeconds(start)}`:`+${fmtSeconds(start)} – +${fmtSeconds(end)}`;const title=`${s().graphKinds[item.kind]??item.kind} | ${s().words.actual}: ${actual}${item.boundary_timestamp?` | ${s().words.nominalBoundary}: ${fmtTime(item.boundary_timestamp)}`:""}`;
        const label=graphLabel(item);const showLabel=width>=Math.max(1.5,label.length*.68);const microClass=item.visual==="work"&&width<1?" micro-span":"";
        const attached=[];const barDetail=detailItems.push({item,point:null,role:item.visual})-1;
        if(item.foundation_marker){const point=item.foundation_marker;const pointLeft=Math.min(100,Math.max(0,Number(point.elapsed_seconds)||0)/duration*100);const pointTitle=`${s().graphKinds[point.kind]??point.kind} | ${s().words.actual}: +${fmtSeconds(point.elapsed_seconds)}`;const pointDetail=detailItems.push({item,point,role:"foundation"})-1;attached.push(`<button type="button" class="graph-foundation-notch kind-${point.kind}" style="left:${pointLeft}%" data-detail="${pointDetail}" title="${esc(pointTitle)}" aria-label="${esc(pointTitle)}"></button>`);}
        return `<button type="button" class="graph-bar ${item.visual}${microClass} kind-${item.kind}" style="left:${left}%;width:${width}%" data-detail="${barDetail}" title="${esc(title)}" aria-label="${esc(title)}">${showLabel?`<span>${esc(label)}</span>`:""}</button>${attached.join("")}`;
      }).join("");
      return `<div class="graph-row"><div class="graph-lane-label">${esc(s().graphRows[row.row]??row.row)}</div><div class="graph-plot">${items}</div></div>`;
    }).join("");
    return `<div class="graph-group"><div class="graph-group-title">${esc(s().graphGroups[group.group]??group.group)}</div>${rows}</div>`;
  }).join("");
  document.getElementById("graph-legend").innerHTML=`<ul>${s().graphGuide.map(value=>`<li>${esc(value)}</li>`).join("")}</ul>`;
  document.getElementById("graphical-timeline").innerHTML=`<div class="graph-content"><div class="graph-row"><div class="graph-axis-label">elapsed</div><div class="graph-axis">${tickHtml}</div></div>${groups}</div>`;
  document.querySelectorAll("[data-detail]").forEach(element=>bindGraphDetail(element,detailItems[Number(element.dataset.detail)]));
  renderLocalDetail(pinnedGraphDetail);
}

function focusTimelineItem(index){
  selectedCategory="All";renderTimelineFilters();renderDetailedTimeline();const target=document.getElementById(`timeline-event-${index}`);if(!target)return;target.classList.add("timeline-highlight");target.scrollIntoView({behavior:"smooth",block:"center"});setTimeout(()=>target.classList.remove("timeline-highlight"),1800);
}

function renderTiming(){
  const timing=DATA.timing;const l=s().timingLabels;
  document.getElementById("timing-stats").innerHTML=[card(l[0],fmtSeconds(timing.preparation_duration_seconds)),card(l[1],fmtSeconds(timing.recent_activation_duration_seconds)),card(l[2],fmtSeconds(timing.max_activation_duration_seconds)),card(l[3],fmtSeconds(timing.cleanup_duration_seconds))].join("");
  document.getElementById("activation-table").innerHTML=table(s().activationHeaders,timing.activations.map(value=>[fmtTime(value.boundary_timestamp),fmtSeconds(value.start_lag_seconds),fmtSeconds(value.duration_seconds),fmtSeconds(value.completion_lag_seconds),yesNo(value.completed)]));
  const detail=DATA.detailed_timing||{};
  const seconds=value=>value==null?"—":Number(value).toPrecision(6)+" s";
  document.getElementById("operation-timing-table").innerHTML=table(
    ["Operation family","Total","p50 / boundary","p95 / boundary","Max","Operations total","Count / boundary: min · p50 · max"],
    Object.entries(detail.operations||{}).map(([name,value])=>{const d=value.seconds,c=value.operations_per_boundary;return [name,seconds(d.total),seconds(d.p50),seconds(d.p95),seconds(d.max),c.total??"—",[c.min,c.p50,c.max].map(x=>x??"—").join(" · ")];}));
  for(const [id,key] of [["envelope-timing-table","envelope"],["pacing-timing-table","pacing"],["residual-timing-table","residuals"]]){
    document.getElementById(id).innerHTML=table(["Measurement","Samples","Total","p50","p95","Max"],Object.entries(detail[key]||{}).map(([name,d])=>[name,d.count,seconds(d.total),seconds(d.p50),seconds(d.p95),seconds(d.max)]));
  }
  document.getElementById("detailed-timing-evidence").innerHTML=[
    evidenceCard("Accounting",detail.accounting),evidenceCard("Preparation / B0",{preparation:detail.preparation,startup:detail.startup}),
    evidenceCard("Cleanup",detail.cleanup),evidenceCard("Actual kernel call counts",detail.kernel_calls),
    evidenceCard("Exact boundary aggregates",detail.boundaries)].join("");
}



function renderScaleSafety(){
  const value=DATA.scale_safety||{};
  const entries=[
    ["Compose startup / 部署启动",value.deployment_startup],
    ["Preparation breakdown / 准备分解",value.preparation_breakdown],
    ["Main subphases / Main 子阶段",value.main_subphases],
    ["Amplification / 放大关系",value.amplification],
    ["Batching / 批处理",value.batching],
    ["Workers / 并发",value.workers],
    ["Resource health / 资源健康",value.resource_health],
    ["Observation completeness / 证据完整性",value.observation],
    ["Teardown / 拆卸",value.teardown],
  ];
  document.getElementById("scale-safety").innerHTML=entries.map(([title,item])=>evidenceCard(title,item)).join("");
}

function classifyBinding(binding){
  const nodeId=String(binding.node_id||"");const ground=nodeId.match(/^\d+\/ground-station\/(gs-[a-z0-9-]+)$/);if(ground)return {group:"ground",shortName:ground[1]};
  const satellite=nodeId.match(/^\d+\/satellite\/dynamic-shell-orbit-(\d{3})-satellite-(\d{4})$/);if(satellite)return {group:"dynamic-shell",orbit:satellite[1],shortName:`satellite ${satellite[2]}`};
  return {group:"other",shortName:nodeId||binding.container_name||s().words.unknown};
}

function bindingNodeHtml(entry){
  const binding=entry.binding;const f=s().bindingFields;
  return `<details class="binding-node"><summary class="binding-node-summary"><strong class="binding-node-short">${esc(entry.shortName)}</strong><span class="binding-node-value">PID ${esc(binding.pid)}</span><span class="binding-node-value">netns ${esc(binding.netns_inode)}</span><span class="binding-node-value">${esc(binding.container_name)}</span></summary><dl class="binding-facts"><dt>${esc(f.nodeId)}</dt><dd>${esc(binding.node_id)}</dd><dt>${esc(f.service)}</dt><dd>${esc(binding.service)}</dd><dt>${esc(f.containerId)}</dt><dd>${esc(binding.container_id)}</dd><dt>${esc(f.containerName)}</dt><dd>${esc(binding.container_name)}</dd><dt>${esc(f.imageId)}</dt><dd>${esc(binding.image_id)}</dd><dt>${esc(f.startedAt)}</dt><dd>${esc(binding.started_at)}</dd></dl></details>`;
}

function bindingGroupHtml(label,count,content,{open=false}={}){
  return `<details class="binding-group" ${open?"open":""}><summary class="binding-group-summary"><strong>${esc(label)}</strong><span class="binding-count">${esc(s().bindingGroups.count(count))}</span></summary>${content}</details>`;
}

function renderFoundation(){
  const foundation=DATA.runtime_foundation||{};const l=s().foundationLabels;const bindings=foundation.bindings||[];
  document.getElementById("foundation-summary").innerHTML=[card(l[0],foundation.compose_project_name),card(l[1],foundation.foundation_token),card(l[2],foundation.expected_node_count),card(l[3],foundation.bound_node_count)].join("");
  if(!bindings.length){document.getElementById("bindings-table").innerHTML=`<p class="muted">${esc(s().noBindings)}</p>`;return;}
  const ground=[];const dynamicShell=new Map();const other=[];
  bindings.forEach(binding=>{const classified=classifyBinding(binding);const entry={binding,shortName:classified.shortName};if(classified.group==="ground")ground.push(entry);else if(classified.group==="dynamic-shell"){if(!dynamicShell.has(classified.orbit))dynamicShell.set(classified.orbit,[]);dynamicShell.get(classified.orbit).push(entry);}else other.push(entry);});
  const groups=[];
  if(ground.length)groups.push(bindingGroupHtml(s().bindingGroups.groundStations,ground.length,`<div class="binding-node-list">${ground.map(bindingNodeHtml).join("")}</div>`,{open:true}));
  if(dynamicShell.size){const orbitGroups=[...dynamicShell.entries()].sort(([left],[right])=>left.localeCompare(right)).map(([orbit,nodes])=>bindingGroupHtml(s().bindingGroups.orbit(orbit),nodes.length,`<div class="binding-node-list">${nodes.map(bindingNodeHtml).join("")}</div>`)).join("");const satelliteCount=[...dynamicShell.values()].reduce((total,nodes)=>total+nodes.length,0);const shell=bindingGroupHtml(s().bindingGroups.dynamicShell,satelliteCount,orbitGroups,{open:true});groups.push(bindingGroupHtml(s().bindingGroups.satellites,satelliteCount,shell,{open:true}));}
  if(other.length)groups.push(bindingGroupHtml(s().bindingGroups.other,other.length,`<div class="binding-node-list">${other.map(bindingNodeHtml).join("")}</div>`,{open:true}));
  document.getElementById("bindings-table").innerHTML=`<div class="binding-tree">${groups.join("")}</div>`;
}

const evidenceCard=(title,value)=>`<details class="card evidence"><summary><strong>${esc(title)}</strong></summary><pre>${esc(pretty(value))}</pre></details>`;
function renderEvidence(){
  const dataplane=DATA.default_dataplane;const values=[dataplane.foundation,dataplane.latest_capacity_audit,dataplane.latest_activation];
  document.getElementById("dataplane").innerHTML=s().dataplaneCards.map((title,index)=>evidenceCard(title,values[index])).join("");
  const fc=DATA.failures_cleanup;const failures=[fc.primary_error,fc.cleanup,fc.runner_failures,fc.capacity_failures,fc.runtime_cleanup_evidence];
  document.getElementById("failures-cleanup").innerHTML=s().failureCards.map((title,index)=>evidenceCard(title,failures[index])).join("");
  document.getElementById("evidence-boundaries").innerHTML=s().boundaryCards.map(([title,description])=>`<div><strong>${esc(title)}</strong><p>${esc(description)}</p></div>`).join("");
}

function renderDelivery(){
  const delivery=DATA.delivery||{};
  document.getElementById('delivery-summary').innerHTML=evidenceCard('Session / reclaimed runtime data',{...delivery.session,...delivery.reclamation});
  const events=delivery.batches||[];
  document.getElementById('delivery-events').innerHTML='<table><thead><tr><th>Event</th><th>Batch</th><th>Nominal time</th><th>Load + construction / prewrite (s)</th><th>Ready margin (s)</th></tr></thead><tbody>'+events.map(v=>`<tr><td>${esc(v.event)}</td><td>${esc(v.batch_index)}</td><td>${esc(v.boundary_timestamp||v.timeline?.start||'—')}</td><td>${esc(v.total_seconds??'—')}</td><td>${esc(v.readiness_margin_seconds??'—')}</td></tr>`).join('')+'</tbody></table>';
}
function renderAll(){
  setStaticText();renderDelivery();renderGuide();renderOverview();renderGraph();renderTimelineFilters();renderDetailedTimeline();renderTiming();renderScaleSafety();renderFoundation();renderEvidence();document.getElementById("raw-json").textContent=pretty(DATA.raw);
  document.querySelectorAll("[data-language]").forEach(button=>{button.classList.toggle("active",button.dataset.language===currentLanguage);button.setAttribute("aria-pressed",button.dataset.language===currentLanguage?"true":"false");});
}

document.querySelectorAll("[data-language]").forEach(button=>button.addEventListener("click",()=>{currentLanguage=button.dataset.language;renderAll();}));
renderAll();
"""


__all__ = ["render_runner_page"]
