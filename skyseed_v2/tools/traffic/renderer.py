"""Render the dependency-free Traffic / Path Replay page."""

from __future__ import annotations

from typing import Any

from tools._html import json_for_script, standalone_page


def render_traffic_replay_page(data: dict[str, Any]) -> str:
    """Render one standalone, read-only traffic replay page."""

    body = r"""
<section class="replay-shell">
  <div class="replay-toolbar">
    <div>
      <span class="status OBSERVED">POST-RUN EVIDENCE</span>
      <span class="status" id="evidence-health"></span>
      <p class="toolbar-copy">当前网络、真实 observed traffic，以及可下钻的 ICMP Ping 路径。</p>
    </div>
    <div class="controls primary-controls">
      <button type="button" id="simulation-toggle">▶ Play</button>
      <label>Speed <select id="simulation-speed"><option value="1">1×</option><option value="5">5×</option><option value="15" selected>15×</option><option value="30">30×</option><option value="60">60×</option></select></label>
      <label>Recent traffic <select id="recent-window"><option value="2">2s</option><option value="5" selected>5s</option><option value="10">10s</option></select></label>
      <code id="simulation-clock"></code>
    </div>
  </div>

  <div class="network-layout">
    <div class="globe-panel">
      <canvas id="globe" aria-label="3D Earth showing the current network and observed traffic"></canvas>
      <div id="globe-ping-control" class="globe-ping-control" hidden>
        <span id="globe-ping-context"></span>
        <button type="button" id="replay-ping">▶ Replay Ping</button>
      </div>
      <div class="globe-legend">
        <span><i class="legend-link"></i>ACTIVE link</span>
        <span><i class="legend-traffic"></i>recent observed traffic</span>
        <span><i class="legend-selected"></i>selected traffic/path</span>
        <span class="muted">拖动地球可旋转</span>
      </div>
    </div>
    <aside class="traffic-browser card">
      <div class="browser-heading">
        <div><span class="eyebrow-small">Observed Traffic</span><h2>真实流量</h2></div>
        <strong id="flow-count"></strong>
      </div>
      <input id="flow-filter" type="search" placeholder="Filter endpoint or protocol" aria-label="Filter observed traffic">
      <div id="flow-list" class="flow-list"></div>
      <p id="flow-empty" class="empty-state" hidden>没有真实 observed traffic。网络与时间轴仍可独立查看。</p>
    </aside>
  </div>

  <div class="timeline-panel card">
    <div class="timeline-heading"><strong>Simulation timeline</strong><span class="muted">点击或拖动任意位置定位 simulation time</span></div>
    <div class="activity-caption" title="Relative count of observed Ping transactions in each timeline bin; not bandwidth, bytes, RTT, utilization, or queue occupancy."><strong>Observed Ping activity</strong><span>柱高仅表示每个时间 bin 中 observed Ping transactions 的相对数量。</span></div>
    <div class="timeline-labels"><code id="timeline-start"></code><code id="timeline-end"></code></div>
    <div class="timeline-track">
      <div id="traffic-activity" class="traffic-activity" aria-hidden="true"></div>
      <input id="simulation-scrubber" type="range" min="0" max="100000" value="0" aria-label="Simulation time">
    </div>
  </div>
</section>

<section class="selected-layout">
  <div class="card selected-traffic">
    <span class="eyebrow-small">Selected Traffic</span>
    <h2 id="selected-flow-title">No observed traffic selected</h2>
    <p id="selected-flow-summary" class="muted"></p>
    <div id="selected-flow-facts" class="compact-facts"></div>
  </div>
  <div class="card network-inspector">
    <span class="eyebrow-small">Network State Inspector</span>
    <h2 id="network-title">Nominal network state</h2>
    <dl id="network-facts"></dl>
    <p id="network-authority" class="muted"></p>
  </div>
</section>

<section>
  <div class="section-heading"><div><h2>Observed Pings</h2><p class="muted">选择一次真实 Ping 后，页面暂停并使用 packet 记录的实际 generation。</p></div><strong id="ping-count"></strong></div>
  <div class="card ping-browser"><table>
    <thead><tr><th>Simulation time</th><th>ICMP</th><th>Status</th><th>Recorded timing</th><th>Generation</th><th>Service</th></tr></thead>
    <tbody id="ping-table"></tbody>
  </table></div>
</section>

<section id="selected-ping-section">
  <div class="section-heading">
    <div><span class="eyebrow-small">Selected Ping</span><h2 id="selected-ping-title">Select an observed Ping</h2></div>
  </div>
  <div id="ping-quality"></div>
  <div class="grid two">
    <div class="card path-card"><h3>Request path</h3><div id="request-path" class="path-strip"></div></div>
    <div class="card path-card"><h3>Reply path</h3><div id="reply-path" class="path-strip"></div></div>
  </div>
  <div id="current-packet-step" class="card current-packet-step"></div>
  <details class="card evidence-detail"><summary>Per-hop / raw selected evidence</summary>
    <div class="scroll"><table><thead><tr><th>#</th><th>Evidence</th><th>Node</th><th>Recorded time</th><th>Generation</th><th>Decision</th><th>Connection</th></tr></thead><tbody id="path-table"></tbody></table></div>
    <pre id="selected-json"></pre>
  </details>
</section>

<section>
  <details class="card" id="planned-workload" hidden><summary>Planned Workload / Schedule Comparison</summary>
    <p class="warning">Scheduled slot without tc-BPF observation 只表示计划 slot 没有匹配到 telemetry；不证明 timeout、packet loss、命令执行或 packet emission。</p>
    <div class="scroll"><table><thead><tr><th>Schedule ID</th><th>Planned endpoints</th><th>Interval</th><th>Scheduled</th><th>Observed</th><th>Without tc-BPF observation</th></tr></thead><tbody id="workload-table"></tbody></table></div>
  </details>
</section>

<section>
  <details class="card"><summary>Raw / Unclassified Observations</summary><div class="scroll"><table><thead><tr><th>Protocol</th><th>Source → destination</th><th>Observations</th><th>Reason</th></tr></thead><tbody id="unclassified-table"></tbody></table></div></details>
  <details class="card"><summary>Clock, interpolation, ownership, and projection evidence</summary><pre id="evidence-json"></pre></details>
</section>
"""
    page = standalone_page(
        title="Skyseed Traffic / Path Replay",
        eyebrow="Post-run · real observed traffic · read-only",
        body=body,
        script=_SCRIPT.replace("__TRAFFIC_DATA__", json_for_script(data)),
        language_strings={
            "Skyseed Traffic / Path Replay": "Skyseed 流量 / 路径回放",
            "Post-run · real observed traffic · read-only": "运行后 · 真实 observed traffic · 只读",
            "POST-RUN EVIDENCE": "运行后证据",
            "Current network, real observed traffic, and drill-down ICMP Ping paths.": "当前网络、真实 observed traffic，以及可下钻的 ICMP Ping 路径。",
            "Play": "播放",
            "Pause": "暂停",
            "Speed": "速度",
            "Recent traffic": "近期流量",
            "3D Earth showing the current network and observed traffic": "显示当前网络与 observed traffic 的 3D Earth",
            "Replay Ping": "回放 Ping",
            "ACTIVE link": "ACTIVE 链路",
            "recent observed traffic": "近期 observed traffic",
            "selected traffic/path": "所选流量 / 路径",
            "Drag the globe to rotate": "拖动地球可旋转",
            "Observed Traffic": "Observed Traffic / 已观测流量",
            "Observed traffic": "真实流量",
            "Filter endpoint or protocol": "筛选端点或协议",
            "No real observed traffic. The network and timeline remain independently available.": "没有真实 observed traffic。网络与时间轴仍可独立查看。",
            "Simulation timeline": "Simulation 时间轴",
            "Click or drag anywhere to select simulation time": "点击或拖动任意位置定位 simulation time",
            "Observed Ping activity": "Observed Ping 活动",
            "Bar height is only the relative number of observed Ping transactions in each time bin.": "柱高仅表示每个时间 bin 中 observed Ping transactions 的相对数量。",
            "Relative count of observed Ping transactions in each timeline bin; not bandwidth, bytes, RTT, utilization, or queue occupancy.": "每个时间轴 bin 中 observed Ping transaction 的相对数量；不是带宽、字节、RTT、利用率或队列占用。",
            "Simulation time": "Simulation time",
            "Selected Traffic": "所选流量",
            "No observed traffic selected": "尚未选择 observed traffic",
            "Network State Inspector": "网络状态检查",
            "Nominal network state": "Nominal 网络状态",
            "Observed Pings": "Observed Ping",
            "Selecting a real Ping pauses the page and uses the Generation recorded by the packet.": "选择一次真实 Ping 后，页面暂停并使用 packet 记录的实际 Generation。",
            "Simulation time": "Simulation time",
            "Status": "状态",
            "Recorded timing": "记录时间",
            "Service": "服务",
            "Selected Ping": "所选 Ping",
            "Select an observed Ping": "请选择一次 observed Ping",
            "Request path": "请求路径",
            "Reply path": "回复路径",
            "Per-hop / raw selected evidence": "逐 hop / 所选原始证据",
            "Evidence": "证据",
            "Node": "节点",
            "Recorded time": "记录时间",
            "Decision": "决策",
            "Connection": "Connection",
            "Planned Workload / Schedule Comparison": "计划 Workload / Schedule 对照",
            "A scheduled slot without tc-BPF observation only means no telemetry matched that planned slot; it does not prove timeout, packet loss, command execution, or packet emission.": "Scheduled slot 没有 tc-BPF observation，只表示计划 slot 未匹配到 telemetry；不证明 timeout、packet loss、命令执行或 packet emission。",
            "Schedule ID": "Schedule ID",
            "Planned endpoints": "计划端点",
            "Interval": "间隔",
            "Scheduled": "已计划",
            "Observed": "已观测",
            "Without tc-BPF observation": "无 tc-BPF observation",
            "Raw / Unclassified Observations": "原始 / 未分类 observation",
            "Protocol": "协议",
            "Source → destination": "源 → 目标",
            "Observations": "Observation",
            "Reason": "原因",
            "Clock, interpolation, ownership, and projection evidence": "时钟、插值、ownership 与 projection evidence",
        },
    )
    return page.replace("</style>", _STYLE + "</style>")


_STYLE = r"""
header { padding-bottom:4px; }
.replay-shell { min-height:calc(100vh - 94px); }
.replay-toolbar { display:flex; justify-content:space-between; gap:20px; align-items:center; margin-bottom:12px; }
.toolbar-copy { margin:7px 0 0; color:var(--muted); }
.primary-controls { margin:0; justify-content:flex-end; }
.primary-controls label { display:flex; gap:6px; align-items:center; color:var(--muted); }
.primary-controls code { min-width:174px; color:var(--cyan); font-weight:750; }
.network-layout { display:grid; grid-template-columns:minmax(0, 3.25fr) minmax(290px, .75fr); gap:14px; height:min(70vh, 760px); min-height:590px; }
.globe-panel { position:relative; min-width:0; overflow:hidden; border:1px solid var(--line); border-radius:16px; background:radial-gradient(circle at 50% 45%,#103554,#020711 70%); box-shadow:0 18px 50px #0007; }
#globe { width:100%; height:100%; border-radius:16px; cursor:grab; touch-action:none; background:transparent; }
#globe:active { cursor:grabbing; }
.globe-legend { position:absolute; left:14px; bottom:12px; display:flex; flex-wrap:wrap; gap:12px; padding:7px 10px; border:1px solid #294966aa; border-radius:9px; background:#06111dcc; font-size:11px; }
.globe-ping-control { position:absolute; top:12px; right:12px; display:flex; align-items:center; gap:9px; max-width:min(420px,calc(100% - 24px)); padding:7px 8px 7px 12px; border:1px solid #5d7891; border-radius:10px; background:#06111ee8; box-shadow:0 8px 24px #0008; }
.globe-ping-control[hidden] { display:none; }.globe-ping-control span { color:var(--muted); font:700 11px/1.3 ui-monospace,monospace; }.globe-ping-control button { padding:6px 9px; white-space:nowrap; }
.globe-legend i { display:inline-block; width:18px; height:3px; margin-right:5px; vertical-align:middle; }
.legend-link { background:#466c8b; }.legend-traffic { background:#5ee5e5; }.legend-selected { background:#ffd166; }
.traffic-browser { display:flex; flex-direction:column; min-height:0; padding:14px; }
.browser-heading,.section-heading,.timeline-heading { display:flex; justify-content:space-between; align-items:flex-start; gap:14px; }
.browser-heading h2 { margin:3px 0 10px; }.browser-heading strong { color:var(--cyan); }
.eyebrow-small { color:var(--cyan); font-size:10px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
#flow-filter { width:100%; margin-bottom:9px; }
.flow-list { overflow:auto; min-height:0; display:flex; flex-direction:column; gap:7px; padding-right:3px; }
.flow-item { width:100%; padding:10px; text-align:left; border-color:#294966; background:#0a192a; }
.flow-item:hover,.flow-item.selected { border-color:var(--cyan); background:#12314a; }
.flow-item.recent::before { content:""; display:inline-block; width:7px; height:7px; margin-right:7px; border-radius:50%; background:var(--pass); box-shadow:0 0 7px var(--pass); }
.flow-item strong { display:block; font-size:13px; overflow-wrap:anywhere; }.flow-item span { display:block; color:var(--muted); font-size:11px; margin-top:3px; }
.empty-state { margin:auto 0; color:var(--muted); }
.timeline-panel { margin-top:14px; padding:12px 16px 16px; }
.timeline-heading { align-items:center; }.timeline-labels { display:flex; justify-content:space-between; margin-top:6px; color:var(--muted); font-size:11px; }
.activity-caption { display:flex; gap:9px; align-items:baseline; margin-top:6px; color:var(--muted); font-size:11px; }.activity-caption strong { color:#87cad9; }.activity-caption span { opacity:.78; }
.timeline-track { position:relative; height:42px; margin-top:2px; border-radius:10px; background:#07111f; overflow:hidden; }
.traffic-activity { position:absolute; inset:8px 10px; display:flex; align-items:flex-end; gap:1px; pointer-events:none; opacity:.42; }
.activity-bin { flex:1 1 0; min-width:1px; background:linear-gradient(180deg,#5ee5e5,#28708f); border-radius:2px 2px 0 0; }
#simulation-scrubber { position:absolute; inset:0; width:100%; height:100%; margin:0; padding:0 5px; opacity:.82; background:transparent; border:0; cursor:ew-resize; accent-color:var(--warn); }
.selected-layout { display:grid; grid-template-columns:minmax(0,1.2fr) minmax(340px,.8fr); gap:14px; }
.selected-traffic h2,.network-inspector h2 { margin-top:4px; }.compact-facts { display:flex; flex-wrap:wrap; gap:8px; }
.fact-pill { padding:4px 8px; border:1px solid var(--line); border-radius:99px; color:var(--muted); font-size:12px; }
.network-inspector dl { display:grid; grid-template-columns:max-content 1fr; gap:5px 12px; margin:10px 0; }.network-inspector dt { color:var(--muted); }.network-inspector dd { margin:0; overflow-wrap:anywhere; }
.ping-browser { max-height:430px; overflow:auto; padding:0; }.ping-browser th { position:sticky; top:0; z-index:1; background:#10243a; }
.ping-row { cursor:pointer; }.ping-row:hover,.ping-row.selected { background:#153b54; }.ping-row.selected { outline:1px solid var(--cyan); outline-offset:-1px; }
.ping-row td:first-child { white-space:nowrap; }.status-word { font-weight:750; }.status-word.paired { color:var(--pass); }.status-word.incomplete { color:var(--warn); }
.path-card { min-height:130px; }.path-strip { display:flex; flex-wrap:wrap; align-items:center; gap:5px; }
.path-node { padding:4px 7px; border:1px solid var(--line); border-radius:7px; background:#0b1b2e; }.path-arrow { color:var(--cyan); }.path-empty { color:var(--muted); }
.quality-pass,.quality-warn { border-left:4px solid var(--pass); padding:9px 13px; background:#173d2b66; margin:10px 0; }.quality-warn { border-color:var(--warn); background:#493b1938; }
.current-packet-step { margin-top:14px; min-height:90px; }.current-packet-step strong { color:var(--warn); font-size:18px; }.current-packet-step code { display:block; margin-top:5px; color:var(--muted); }
.evidence-detail { margin-top:14px; }.evidence-detail pre { max-height:400px; overflow:auto; }.scroll { overflow:auto; } td code { font-size:11px; }
details.card { margin-top:14px; } details pre { max-height:480px; overflow:auto; }
@media (max-width:960px) { .replay-toolbar { align-items:flex-start; flex-direction:column; }.primary-controls { justify-content:flex-start; }.network-layout { grid-template-columns:1fr; height:auto; }.globe-panel { height:62vh; min-height:450px; }.traffic-browser { height:280px; }.selected-layout { grid-template-columns:1fr; } }
@media (max-width:620px) { .globe-panel { height:55vh; min-height:380px; }.primary-controls code { min-width:0; width:100%; }.ping-browser { font-size:12px; }.ping-browser th:nth-child(5),.ping-browser td:nth-child(5),.ping-browser th:nth-child(6),.ping-browser td:nth-child(6) { display:none; } }
"""


_SCRIPT = r"""
const DATA=__TRAFFIC_DATA__;
const $=id=>document.getElementById(id);
const esc=value=>String(value??"—").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const startMs=Date.parse(DATA.simulation_timeline.start_time);
const endMs=Date.parse(DATA.simulation_timeline.end_time);
const spanMs=Math.max(1,endMs-startMs);
const transactions=[...(DATA.ping_transactions||[])];
const transactionById=new Map(transactions.map(value=>[value.transaction_id,value]));
const flows=[...(DATA.observed_flows||[])];
const flowById=new Map(flows.map(value=>[value.flow_id,value]));
const networks=[...(DATA.active_networks||[])].sort((a,b)=>Date.parse(a.boundary_timestamp)-Date.parse(b.boundary_timestamp));
const networkByGeneration=new Map(networks.map(value=>[Number(value.generation),value]));
const samples=[...(DATA.position_samples||[])].sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp));
const nodeById=new Map((DATA.nodes||[]).map(value=>[value.node_id,value]));
const REQUEST_COLOR="#ffd166";
const REPLY_COLOR="#f58ad3";
let simulationMs=startMs;
let simulationPlaying=false;
let simulationSpeed=15;
let lastFrameMs=0;
let recentWindowSeconds=Number(DATA.visualization?.recent_traffic_default_seconds||5);
let selectedFlowId=flows[0]?.flow_id??null;
let selectedTransactionId=null;
let packetEvidenceActive=false;
let pingReplay=null;
let lastFlowRefreshMs=0;
let viewLon=-.35,viewLat=.2;
const MAX_VIEW_LAT=89*Math.PI/180;
let dragging=false,dragMoved=false,dragX=0,dragY=0;
let trafficHitTargets=[];

function normalizeGlobeView(){
  viewLat=Math.max(-MAX_VIEW_LAT,Math.min(MAX_VIEW_LAT,viewLat));
  viewLon=((viewLon+Math.PI)%(Math.PI*2)+Math.PI*2)%(Math.PI*2)-Math.PI;
}

const canvas=$("globe");
const ctx=canvas.getContext("2d");
const selectedFlow=()=>flowById.get(selectedFlowId)||null;
const selectedTransaction=()=>transactionById.get(selectedTransactionId)||null;
const timePercent=value=>Math.max(0,Math.min(1,(value-startMs)/spanMs));
const shortTime=value=>new Date(value).toISOString().slice(11,23);
const fmtDuration=value=>`${Number(value).toFixed(Number(value)<10?3:1)} ms`;
const protocolName=value=>({1:"ICMP",6:"TCP",17:"UDP"}[value]||`IPv4/${value}`);
const shortName=value=>String(value??"—").replace(/Orbit-0*([0-9]+) Sat-0*([0-9]+)/,"O$1-S$2");

function networkAt(timestampMs){
  let current=networks[0]||null;
  for(const network of networks){if(Date.parse(network.boundary_timestamp)<=timestampMs)current=network;else break;}
  return current;
}
function directions(transaction){return transaction?[transaction.request,transaction.reply].filter(Boolean):[];}
function directionGeneration(direction){return Number(direction?.inferred_source?.generation??direction?.observed_path?.[0]?.generation);}
function selectedDirection(){
  const tx=selectedTransaction();
  if(!tx)return null;
  if(pingReplay?.direction)return pingReplay.direction;
  return tx.request||tx.reply;
}
function displayNetwork(){
  if(packetEvidenceActive){
    const replayGeneration=pingReplay?.steps?.[pingReplay.index]?.generation;
    const generation=Number(replayGeneration??directionGeneration(selectedDirection()));
    return networkByGeneration.get(generation)||networkAt(simulationMs);
  }
  return networkAt(simulationMs);
}
function positionsAt(timestampMs){
  if(!samples.length)return new Map();
  let right=samples.findIndex(value=>Date.parse(value.timestamp)>=timestampMs);
  if(right<0)right=samples.length-1;
  const left=Math.max(0,right-(Date.parse(samples[right].timestamp)>timestampMs?1:0));
  if(left===right)return new Map(Object.entries(samples[left].positions).map(([id,value])=>[id,value.ecef_m]));
  const a=samples[left],b=samples[right];
  const fraction=Math.max(0,Math.min(1,(timestampMs-Date.parse(a.timestamp))/(Date.parse(b.timestamp)-Date.parse(a.timestamp))));
  return new Map(Object.keys(a.positions).map(id=>{
    const p=a.positions[id].ecef_m,q=b.positions[id].ecef_m;
    return [id,{x:p.x+(q.x-p.x)*fraction,y:p.y+(q.y-p.y)*fraction,z:p.z+(q.z-p.z)*fraction}];
  }));
}
function rotatePoint(point){
  const earth=6378137;
  const x=point.x/earth,y=point.y/earth,z=point.z/earth;
  const cy=Math.cos(viewLon),sy=Math.sin(viewLon);
  const x1=x*cy-y*sy,y1=x*sy+y*cy;
  const cx=Math.cos(viewLat),sx=Math.sin(viewLat);
  return {x:x1,y:y1*cx-z*sx,z:y1*sx+z*cx,r:Math.hypot(x,y,z)};
}
function geographicPoint(lat,lon,r=1.003){
  const p=lat*Math.PI/180,l=lon*Math.PI/180;
  return rotatePoint({x:6378137*r*Math.cos(p)*Math.cos(l),y:6378137*r*Math.cos(p)*Math.sin(l),z:6378137*r*Math.sin(p)});
}
function project(point,width,height){
  const scale=Math.min(width,height)*.38;
  return {x:width/2+point.x*scale,y:height/2-point.y*scale,z:point.z,r:point.r,visible:point.z>=0};
}
function resizeCanvas(){
  const ratio=window.devicePixelRatio||1;
  const rect=canvas.getBoundingClientRect();
  const width=Math.max(320,Math.floor(rect.width*ratio)),height=Math.max(320,Math.floor(rect.height*ratio));
  if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;}
  ctx.setTransform(ratio,0,0,ratio,0,0);
  return {width:rect.width,height:rect.height};
}
function drawSegment(a,b,style,width,alpha=1,dash=[]){
  if(!a||!b||(!a.visible&&!b.visible))return;
  let first={...a},second={...b};
  if(first.visible!==second.visible){
    const t=first.z/(first.z-second.z);
    const cut={x:first.x+(second.x-first.x)*t,y:first.y+(second.y-first.y)*t,z:0,visible:true};
    if(first.visible)second=cut;else first=cut;
  }
  ctx.save();ctx.globalAlpha=alpha;ctx.strokeStyle=style;ctx.lineWidth=width;ctx.lineCap="round";ctx.setLineDash(dash);
  ctx.beginPath();ctx.moveTo(first.x,first.y);ctx.lineTo(second.x,second.y);ctx.stroke();ctx.restore();
}
function drawEarth(width,height){
  const radius=Math.min(width,height)*.38;
  const gradient=ctx.createRadialGradient(width/2-radius*.35,height/2-radius*.38,radius*.1,width/2,height/2,radius);
  gradient.addColorStop(0,"#1d6687");gradient.addColorStop(.62,"#0b314e");gradient.addColorStop(1,"#03131f");
  ctx.fillStyle=gradient;ctx.beginPath();ctx.arc(width/2,height/2,radius,0,Math.PI*2);ctx.fill();
  ctx.strokeStyle="#5aa4bf55";ctx.lineWidth=1;ctx.stroke();
  ctx.save();ctx.beginPath();ctx.arc(width/2,height/2,radius,0,Math.PI*2);ctx.clip();
  ctx.strokeStyle="#74b8cd18";ctx.lineWidth=1;
  for(let lat=-60;lat<=60;lat+=30){
    let prior=null;for(let lon=-180;lon<=180;lon+=4){const next=project(geographicPoint(lat,lon,1),width,height);if(prior)drawSegment(prior,next,"#74b8cd18",1);prior=next;}
  }
  for(let lon=-150;lon<=180;lon+=30){
    let prior=null;for(let lat=-90;lat<=90;lat+=3){const next=project(geographicPoint(lat,lon,1),width,height);if(prior)drawSegment(prior,next,"#74b8cd18",1);prior=next;}
  }
  ctx.restore();
}
function selectedZoneId(){
  const tx=selectedTransaction();
  const flow=selectedFlow();
  for(const endpoint of [tx?.source_endpoint,tx?.destination_endpoint,flow?.endpoint_a,flow?.endpoint_b])if(endpoint?.endpoint_kind==="zone")return endpoint.zone_id;
  return null;
}
function drawZones(network,width,height){
  const selectedZone=selectedZoneId();
  const services=new Map((network?.zone_services||[]).map(value=>[value.zone_id,value]));
  for(const zone of DATA.zones||[]){
    const geometry=zone.geometry,selected=zone.zone_id===selectedZone;
    for(const segment of geometry.longitude_segments||[]){
      const points=[];
      const west=segment.west_longitude_deg,east=segment.east_longitude_deg;
      for(let lon=west;lon<=east;lon+=Math.max(1,(east-west)/20))points.push(geographicPoint(geometry.south_latitude_deg,lon));
      points.push(geographicPoint(geometry.south_latitude_deg,east));
      for(let lat=geometry.south_latitude_deg;lat<=geometry.north_latitude_deg;lat+=Math.max(1,(geometry.north_latitude_deg-geometry.south_latitude_deg)/12))points.push(geographicPoint(lat,east));
      points.push(geographicPoint(geometry.north_latitude_deg,east));
      for(let lon=east;lon>=west;lon-=Math.max(1,(east-west)/20))points.push(geographicPoint(geometry.north_latitude_deg,lon));
      for(let lat=geometry.north_latitude_deg;lat>=geometry.south_latitude_deg;lat-=Math.max(1,(geometry.north_latitude_deg-geometry.south_latitude_deg)/12))points.push(geographicPoint(lat,west));
      const screen=points.map(value=>project(value,width,height));
      for(let index=1;index<screen.length;index++)drawSegment(screen[index-1],screen[index],selected?"#ffd166":"#5ee5e555",selected?2.5:1,selected?1:.55);
    }
    if(selected){
      const point=project(geographicPoint(zone.representative_point.latitude_deg,zone.representative_point.longitude_deg),width,height);
      if(point.visible){drawLabel(point.x,point.y-8,zone.zone_id,"#ffd166",true);const service=services.get(zone.zone_id);if(service)drawLabel(point.x,point.y+10,`service: ${shortName(service.service_display_name)}`,"#dbeeff",false);}
    }
  }
}
function pathNodeIds(direction){return direction?[direction.inferred_source.node_id,...direction.observed_path.map(value=>value.node_id)]:[];}
function connectionPairs(direction){
  if(!direction)return[];
  const ids=pathNodeIds(direction),result=[];
  for(let i=1;i<ids.length;i++)result.push([ids[i-1],ids[i]]);
  return result;
}
function transactionsInRecentWindow(){
  const lower=simulationMs-recentWindowSeconds*1000;
  return transactions.filter(value=>{const time=Date.parse(value.first_simulation_timestamp);return time>=lower&&time<=simulationMs;});
}
function routeSignature(a,b){return [a,b].sort().join("|");}
function drawTrafficPath(direction,positions,width,height,color,lineWidth,alpha,flowId,dash=[]){
  for(const [a,b] of connectionPairs(direction)){
    const pa=positions.get(a),pb=positions.get(b);if(!pa||!pb)continue;
    const sa=project(rotatePoint(pa),width,height),sb=project(rotatePoint(pb),width,height);
    drawSegment(sa,sb,color,lineWidth,alpha,dash);
    if(sa.visible||sb.visible)trafficHitTargets.push({a:sa,b:sb,flowId});
  }
}
function drawDirectionLabel(direction,label,positions,width,height,color){
  const pair=connectionPairs(direction).find(([a,b])=>positions.has(a)&&positions.has(b));if(!pair)return;
  const a=project(rotatePoint(positions.get(pair[0])),width,height),b=project(rotatePoint(positions.get(pair[1])),width,height);if(!a.visible&&!b.visible)return;
  if((a.z+b.z)/2<0)return;
  drawLabel((a.x+b.x)/2,(a.y+b.y)/2-7,label,color,true);
}
function drawReplayPulse(positions,width,height){
  if(!pingReplay?.steps?.length)return;
  const color=pingReplay.label==="Request"?REQUEST_COLOR:REPLY_COLOR;
  const dash=pingReplay.label==="Request"?[]:[7,5];
  const points=pingReplay.steps.map(value=>positions.has(value.node_id)?project(rotatePoint(positions.get(value.node_id)),width,height):null);
  for(let index=1;index<=pingReplay.index;index++)drawSegment(points[index-1],points[index],color,6,1,dash);
  const from=points[pingReplay.index],to=points[Math.min(pingReplay.index+1,points.length-1)];if(!from||!to)return;
  const progress=Math.max(0,Math.min(1,(performance.now()-pingReplay.lastAdvance)/420));
  const pulse={x:from.x+(to.x-from.x)*progress,y:from.y+(to.y-from.y)*progress,z:from.z+(to.z-from.z)*progress,visible:from.z+(to.z-from.z)*progress>=0};
  drawSegment(from,pulse,color,7,1,dash);
  if(pulse.visible){ctx.save();ctx.fillStyle="#ffffff";ctx.shadowColor=color;ctx.shadowBlur=14;ctx.beginPath();ctx.arc(pulse.x,pulse.y,5.5,0,Math.PI*2);ctx.fill();ctx.restore();}
  if(from.visible){ctx.save();ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();ctx.arc(from.x,from.y,10,0,Math.PI*2);ctx.stroke();ctx.restore();}
}
function drawNode(point,metadata,selected,service,width,height){
  if(!point.visible)return;
  const ground=metadata?.kind==="ground-station";
  ctx.save();ctx.globalAlpha=selected ? .98 : (ground ? .76 : .18);ctx.fillStyle=selected?"#ffd166":ground?"#8fe7bb":"#a9d8ff";
  const radius=selected?5.5:ground?4:2.3;ctx.beginPath();ctx.arc(point.x,point.y,radius,0,Math.PI*2);ctx.fill();
  if(service){ctx.strokeStyle="#ffd166";ctx.lineWidth=2;ctx.beginPath();ctx.arc(point.x,point.y,10,0,Math.PI*2);ctx.stroke();}
  ctx.restore();
}
function drawLabel(x,y,text,color="#dbeeff",strong=false){
  ctx.save();ctx.font=`${strong?700:600} 11px system-ui`;const width=ctx.measureText(text).width;
  ctx.fillStyle="#05111ddd";ctx.fillRect(x-width/2-4,y-11,width+8,15);ctx.fillStyle=color;ctx.textAlign="center";ctx.fillText(text,x,y);ctx.restore();
}
function serviceNodeIds(network){
  const zoneId=selectedZoneId();
  return new Set((network?.zone_services||[]).filter(value=>value.zone_id===zoneId).map(value=>value.service_satellite));
}
function selectedPathNodes(){
  if(!packetEvidenceActive)return new Set();
  return new Set(directions(selectedTransaction()).flatMap(pathNodeIds));
}
function drawGlobe(){
  const {width,height}=resizeCanvas();ctx.clearRect(0,0,width,height);drawEarth(width,height);
  const network=displayNetwork(),positions=positionsAt(simulationMs),projected=new Map();
  for(const [id,value] of positions)projected.set(id,project(rotatePoint(value),width,height));
  drawZones(network,width,height);
  for(const connection of network?.connections||[])drawSegment(projected.get(connection.endpoint_a),projected.get(connection.endpoint_b),connection.kind==="ground-station-access"?"#5787a6":"#355b78",1,.55);
  trafficHitTargets=[];
  if(packetEvidenceActive&&selectedTransaction()){
    const tx=selectedTransaction();
    for(const other of transactionsInRecentWindow())if(other.transaction_id!==tx.transaction_id)for(const direction of directions(other))drawTrafficPath(direction,positions,width,height,"#5ee5e5",1.5,.16,other.observed_flow_id);
    if(tx.request){drawTrafficPath(tx.request,positions,width,height,REQUEST_COLOR,3,.64,tx.observed_flow_id);drawDirectionLabel(tx.request,"Request",positions,width,height,REQUEST_COLOR);}
    if(tx.reply){drawTrafficPath(tx.reply,positions,width,height,REPLY_COLOR,3,.64,tx.observed_flow_id,[7,5]);drawDirectionLabel(tx.reply,"Reply",positions,width,height,REPLY_COLOR);}
    const active=selectedDirection();if(active)drawTrafficPath(active,positions,width,height,pingReplay?.label==="Reply"?REPLY_COLOR:REQUEST_COLOR,5,pingReplay?0.58:0.9,tx.observed_flow_id,pingReplay?.label==="Reply"?[7,5]:[]);
    drawReplayPulse(positions,width,height);
  }else{
    for(const tx of transactionsInRecentWindow()){
      const selected=tx.observed_flow_id===selectedFlowId;
      for(const direction of directions(tx))drawTrafficPath(direction,positions,width,height,selected?"#ffd166":"#5ee5e5",selected?4:2,selected?.95:.28,tx.observed_flow_id);
    }
  }
  const selectedNodes=selectedPathNodes(),serviceNodes=serviceNodeIds(network);
  for(const [id,point] of projected)drawNode(point,nodeById.get(id),selectedNodes.has(id),serviceNodes.has(id),width,height);
  for(const [id,point] of projected){
    const metadata=nodeById.get(id);if(!point.visible)continue;
    if(selectedNodes.has(id))drawLabel(point.x,point.y-9,shortName(metadata?.display_name||id),"#ffd166",true);
    else if(metadata?.kind==="ground-station")drawLabel(point.x,point.y-7,metadata.display_name,"#a9e9c7",false);
  }
  canvas.dataset.cameraLongitude=String(viewLon);
  canvas.dataset.cameraLatitude=String(viewLat);
}
function lineDistance(point,a,b){
  const dx=b.x-a.x,dy=b.y-a.y,length=dx*dx+dy*dy;if(!length)return Math.hypot(point.x-a.x,point.y-a.y);
  const t=Math.max(0,Math.min(1,((point.x-a.x)*dx+(point.y-a.y)*dy)/length));return Math.hypot(point.x-(a.x+t*dx),point.y-(a.y+t*dy));
}
function selectFlow(flowId){selectedFlowId=flowId;selectedTransactionId=null;packetEvidenceActive=false;pingReplay=null;renderAll();}
function selectPing(transactionId){
  const tx=transactionById.get(transactionId);if(!tx)return;
  selectedTransactionId=transactionId;selectedFlowId=tx.observed_flow_id;simulationMs=Date.parse(tx.first_simulation_timestamp);simulationPlaying=false;packetEvidenceActive=true;pingReplay=null;renderAll();
  $("globe").scrollIntoView({behavior:"smooth",block:"center"});
}
function renderOverview(){
  const flow=selectedFlow();
  $("selected-flow-title").textContent=flow?.display_label||"No observed traffic selected";
  $("selected-flow-summary").textContent=flow?`${flow.first_simulation_timestamp} → ${flow.last_simulation_timestamp}`:"Telemetry contains no grouped ICMP Ping flow.";
  $("selected-flow-facts").innerHTML=flow?[`<span class="fact-pill">${flow.observed_ping_count} observed Pings</span>`,`<span class="fact-pill">${flow.paired_ping_count} paired</span>`,`<span class="fact-pill">${flow.incomplete_ping_count} incomplete</span>`].join(""):"";
  const network=displayNetwork();
  $("network-title").textContent=packetEvidenceActive?`Recorded generation G${network?.generation??"—"}`:`Nominal generation G${network?.generation??"—"}`;
  const zoneId=selectedZoneId(),service=(network?.zone_services||[]).find(value=>value.zone_id===zoneId);
  const events=(DATA.simulation_timeline.zone_events||[]).filter(value=>value.zone_id===zoneId&&Date.parse(value.nominal_time)<=Date.parse(network?.boundary_timestamp||0));
  const latest=events.at(-1);
  $("network-facts").innerHTML=[
    ["Boundary",network?.boundary_timestamp],["ACTIVE links",network?.connections?.length??0],["Selected Zone",zoneId],["Current service",service?.service_display_name],["Previous service",latest?.previous_service_display_name],["State change",latest?.nominal_time]
  ].filter(value=>value[1]!=null).map(([key,value])=>`<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join("");
  $("network-authority").textContent=packetEvidenceActive?"Selected Ping: background, Zone service, and ACTIVE graph use the packet's actual recorded generation.":"Free scrub: network state uses the latest nominal boundary at or before the simulation cursor.";
}
function renderFlowBrowser(){
  const query=$("flow-filter").value.trim().toLowerCase(),recent=new Set(transactionsInRecentWindow().map(value=>value.observed_flow_id));
  const visible=flows.filter(value=>value.display_label.toLowerCase().includes(query));
  $("flow-count").textContent=`${visible.length} / ${flows.length}`;
  $("flow-list").innerHTML=visible.map(flow=>`<button type="button" class="flow-item ${flow.flow_id===selectedFlowId?"selected":""} ${recent.has(flow.flow_id)?"recent":""}" data-flow="${esc(flow.flow_id)}"><strong>${esc(flow.display_label)}</strong><span>${flow.observed_ping_count} observed · ${flow.incomplete_ping_count} incomplete</span></button>`).join("");
  $("flow-empty").textContent=flows.length?"No observed traffic matches this filter.":"没有真实 observed traffic。网络与时间轴仍可独立查看。";
  $("flow-empty").hidden=visible.length!==0;
  document.querySelectorAll("[data-flow]").forEach(button=>button.onclick=()=>selectFlow(button.dataset.flow));
}
function transactionService(transaction){
  for(const endpoint of [transaction.source_endpoint,transaction.destination_endpoint])if(endpoint?.endpoint_kind==="zone")return endpoint.service_satellite_display_name;
  return null;
}
function renderPingTable(){
  const flow=selectedFlow(),values=flow?flow.transaction_ids.map(id=>transactionById.get(id)).filter(Boolean):[];
  $("ping-count").textContent=`${values.length} observed`;
  $("ping-table").innerHTML=values.map(tx=>`<tr class="ping-row ${tx.transaction_id===selectedTransactionId?"selected":""}" data-ping="${esc(tx.transaction_id)}"><td><code>${esc(shortTime(Date.parse(tx.first_simulation_timestamp)))}</code></td><td>seq ${tx.sequence}</td><td><span class="status-word ${tx.pairing_status==="paired"?"paired":"incomplete"}">${esc(tx.pairing_status)}</span></td><td>${esc(fmtDuration(tx.recorded_duration_ms))}</td><td>${esc(tx.generations.map(value=>`G${value}`).join(", "))}</td><td>${esc(transactionService(tx))}</td></tr>`).join("")||'<tr><td colspan="6" class="muted">No observed Pings for this flow.</td></tr>';
  document.querySelectorAll("[data-ping]").forEach(row=>row.onclick=()=>selectPing(row.dataset.ping));
}
function pathHtml(direction){
  if(!direction)return'<span class="path-empty">No recorded direction evidence</span>';
  const values=[direction.inferred_source,...direction.observed_path];
  return values.map((value,index)=>`${index?'<span class="path-arrow">→</span>':""}<span class="path-node">${esc(shortName(value.display_name))}</span>`).join("");
}
function evidenceRows(direction,label){
  if(!direction)return"";
  const values=[direction.inferred_source,...direction.observed_path];
  return values.map((value,index)=>`<tr><td>${index}</td><td>${index===0?"inferred source":"observed ingress"} · ${esc(label)}</td><td>${esc(value.display_name)}<br><code>${esc(value.node_id)}</code></td><td>${esc(value.simulation_timestamp)}<br><code>+${Number(value.recorded_elapsed_ms||0).toFixed(3)} ms</code></td><td>G${esc(value.generation)}<br><code>${esc(value.active_boundary_timestamp)}</code></td><td>${esc(value.decision||"source")}</td><td><code>${esc(value.outgoing_connection_id||value.incoming_connection_id)}</code></td></tr>`).join("");
}
function renderSelectedPing(){
  const tx=selectedTransaction(),control=$("globe-ping-control");control.hidden=!tx;$("replay-ping").disabled=!tx;
  $("globe-ping-context").textContent=tx?`Ping seq ${tx.sequence} · ${tx.generations.map(value=>`G${value}`).join("/")}`:"";
  $("selected-ping-title").textContent=tx?`ICMP Ping · seq ${tx.sequence} · ${tx.source_endpoint.display_name} → ${tx.destination_endpoint.display_name}`:"Select an observed Ping";
  $("request-path").innerHTML=pathHtml(tx?.request);$("reply-path").innerHTML=pathHtml(tx?.reply);
  $("ping-quality").innerHTML=tx?`<div class="${tx.evidence_complete?"quality-pass":"quality-warn"}"><strong>${esc(tx.pairing_status)}</strong> · ${esc(fmtDuration(tx.recorded_duration_ms))} recorded timing · ${esc(tx.generations.map(value=>`G${value}`).join(", "))}${tx.warnings?.length?`<br>${esc(tx.warnings.join("; "))}`:""}</div>`:"";
  $("path-table").innerHTML=tx?evidenceRows(tx.request,"request")+evidenceRows(tx.reply,"reply"):"";
  $("selected-json").textContent=tx?JSON.stringify(tx,null,2):"No selected Ping";
  renderPacketStep();
}
function renderPacketStep(){
  const tx=selectedTransaction();if(!tx){$("current-packet-step").innerHTML='<span class="muted">Select a Ping to inspect or replay its real evidence.</span>';return;}
  const direction=selectedDirection();
  if(!pingReplay){$("current-packet-step").innerHTML=`<strong>Paused at recorded Ping</strong><p>${esc(direction?.source_display_name)} → ${esc(direction?.destination_display_name)}</p><code>Actual G${esc(directionGeneration(direction))}; Replay Ping uses visualization-only time expansion.</code>`;return;}
  const step=pingReplay.steps[pingReplay.index];
  $("current-packet-step").innerHTML=`<span class="eyebrow-small">${esc(pingReplay.label)}</span><strong>${esc(step?.display_name||"Recorded path complete")}</strong><p>${esc(step?.decision||step?.evidence_kind||"")}</p><code>${esc(step?.simulation_timestamp||tx.first_simulation_timestamp)} · recorded +${Number(step?.recorded_elapsed_ms||0).toFixed(3)} ms · visual step ${pingReplay.index+1}/${pingReplay.steps.length}</code>`;
}
function buildReplayPhases(tx){
  return directions(tx).map(direction=>({direction,label:direction.icmp_type===8?"Request":"Reply",steps:[direction.inferred_source,...direction.observed_path]}));
}
function startPingReplay(){
  const tx=selectedTransaction();if(!tx)return;
  simulationPlaying=false;packetEvidenceActive=true;simulationMs=Date.parse(tx.first_simulation_timestamp);
  const phases=buildReplayPhases(tx);if(!phases.length)return;
  pingReplay={phases,phase:0,index:0,lastAdvance:performance.now(),direction:phases[0].direction,label:phases[0].label,steps:phases[0].steps};renderAll();
}
function advancePingReplay(now){
  if(!pingReplay||now-pingReplay.lastAdvance<420)return;
  pingReplay.lastAdvance=now;pingReplay.index+=1;
  if(pingReplay.index>=pingReplay.steps.length){
    pingReplay.phase+=1;
    if(pingReplay.phase>=pingReplay.phases.length){pingReplay=null;renderAll();return;}
    const next=pingReplay.phases[pingReplay.phase];pingReplay.index=0;pingReplay.direction=next.direction;pingReplay.label=next.label;pingReplay.steps=next.steps;
  }
  renderOverview();renderPacketStep();drawGlobe();
}
function renderTimeline(){
  $("simulation-clock").textContent=new Date(simulationMs).toISOString();
  $("simulation-scrubber").value=Math.round(timePercent(simulationMs)*100000);
  $("timeline-start").textContent=DATA.simulation_timeline.start_time;$("timeline-end").textContent=DATA.simulation_timeline.end_time;
  $("simulation-toggle").textContent=simulationPlaying?"Ⅱ Pause":"▶ Play";
}
function renderActivity(){
  const bins=120,counts=Array(bins).fill(0);for(const tx of transactions){const index=Math.min(bins-1,Math.floor(timePercent(Date.parse(tx.first_simulation_timestamp))*bins));counts[index]+=1;}
  const maximum=Math.max(1,...counts);$("traffic-activity").innerHTML=counts.map(value=>`<i class="activity-bin" style="height:${Math.max(1,value/maximum*100)}%"></i>`).join("");
}
function renderWorkload(){
  const section=$("planned-workload"),lanes=DATA.traffic_lanes||[];section.hidden=!DATA.workload;
  $("workload-table").innerHTML=lanes.map(value=>`<tr><td>${esc(value.series_id)}</td><td>${esc(value.ground_station.display_name)} ↔ ${esc(value.zone.zone_id)}</td><td>${esc(value.schedule.start_time)} → ${esc(value.schedule.end_time)}<br>${esc(value.schedule.interval_seconds)}s cadence</td><td>${value.scheduled_slots}</td><td>${value.observed_transactions}</td><td>${value.observed_gap_slots}</td></tr>`).join("");
}
function renderRaw(){
  $("unclassified-table").innerHTML=(DATA.unclassified_observations||[]).map(value=>`<tr><td>${esc(protocolName(value.protocol))}</td><td>${esc(value.source_ipv4)} → ${esc(value.destination_ipv4)}</td><td>${value.observations?.length||0}</td><td>${esc(value.classification_reason)}</td></tr>`).join("")||'<tr><td colspan="4" class="muted">No raw / unclassified observations.</td></tr>';
  $("evidence-json").textContent=JSON.stringify({authority:DATA.authority,overview:DATA.overview,clock:DATA.clock,position:DATA.position,visualization:DATA.visualization,inventory:DATA.inventory},null,2);
}
function renderEvidenceHealth(){
  const healthy=DATA.overview.telemetry_complete&&Number(DATA.overview.reported_losses)===0;
  const badge=$("evidence-health");badge.className=`status ${healthy?"PASS":"OBSERVED"}`;badge.textContent=healthy?"TELEMETRY COMPLETE · LOSS 0":`INCOMPLETE · REPORTED LOSS ${DATA.overview.reported_losses}`;
}
function renderAll(){renderTimeline();renderFlowBrowser();renderOverview();renderPingTable();renderSelectedPing();drawGlobe();}
function leavePacketEvidence(){packetEvidenceActive=false;pingReplay=null;selectedTransactionId=null;}

$("simulation-toggle").onclick=()=>{leavePacketEvidence();simulationPlaying=!simulationPlaying;lastFrameMs=performance.now();renderAll();};
$("simulation-speed").onchange=event=>{simulationSpeed=Number(event.target.value);};
$("recent-window").onchange=event=>{recentWindowSeconds=Number(event.target.value);renderAll();};
$("simulation-scrubber").oninput=event=>{simulationPlaying=false;leavePacketEvidence();simulationMs=startMs+Number(event.target.value)/100000*spanMs;renderAll();};
$("flow-filter").oninput=renderFlowBrowser;
$("replay-ping").onclick=startPingReplay;
canvas.addEventListener("pointerdown",event=>{dragging=true;dragMoved=false;dragX=event.clientX;dragY=event.clientY;canvas.setPointerCapture(event.pointerId);});
canvas.addEventListener("pointermove",event=>{if(!dragging)return;const dx=event.clientX-dragX,dy=event.clientY-dragY;if(Math.abs(dx)+Math.abs(dy)>2)dragMoved=true;viewLon+=dx*.008;viewLat-=dy*.008;normalizeGlobeView();dragX=event.clientX;dragY=event.clientY;drawGlobe();});
canvas.addEventListener("pointerup",event=>{dragging=false;canvas.releasePointerCapture(event.pointerId);if(dragMoved)return;const rect=canvas.getBoundingClientRect(),point={x:event.clientX-rect.left,y:event.clientY-rect.top};let best=null;for(const hit of trafficHitTargets){const distance=lineDistance(point,hit.a,hit.b);if(distance<12&&(!best||distance<best.distance))best={...hit,distance};}if(best)selectFlow(best.flowId);});
window.addEventListener("resize",drawGlobe);
function frame(now){
  if(simulationPlaying){if(!lastFrameMs)lastFrameMs=now;simulationMs+=Math.max(0,now-lastFrameMs)*simulationSpeed;if(simulationMs>=endMs){simulationMs=endMs;simulationPlaying=false;}lastFrameMs=now;renderTimeline();renderOverview();drawGlobe();if(now-lastFlowRefreshMs>250){renderFlowBrowser();lastFlowRefreshMs=now;}}else lastFrameMs=now;
  advancePingReplay(now);if(pingReplay)drawGlobe();requestAnimationFrame(frame);
}
renderWorkload();renderRaw();renderEvidenceHealth();renderActivity();renderAll();requestAnimationFrame(frame);
"""
