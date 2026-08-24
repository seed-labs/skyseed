"""Render a non-authoritative Slice / Difference projection offline."""

from __future__ import annotations

from html import escape
from typing import Any

from .._html import json_for_script, standalone_page


def render_slice_difference_page(data: dict[str, Any]) -> str:
    """Render five focused presets over shared spatial and timeline context."""

    inventory = data["inventory"]
    stats = "".join(
        f'<div class="card stat"><span class="muted">{escape(label)}</span>'
        f"<strong>{inventory[key]}</strong></div>"
        for key, label in (
            ("satellites", "Satellite"),
            ("ground_stations", "GroundStation"),
            ("orbits", "Orbit"),
            ("zones", "Zone"),
            ("slices", "Slice"),
            ("differences", "Difference"),
            ("paths", "Resolved path"),
        )
    )
    body = f"""
<style>
.timeline {{ display:grid; grid-template-columns:auto auto auto 1fr auto; gap:9px; align-items:center; }}
.timeline input {{ width:100%; }}
.timeline-rail {{ display:flex; align-items:center; gap:4px; overflow-x:auto; padding:8px 0 3px; }}
.timeline-marker {{ min-width:max-content; padding:5px 8px; border-radius:99px; font-size:12px; }}
.timeline-marker.difference {{ border-style:dashed; color:#ffbd73; }}
.timeline-marker[aria-current="true"] {{ border-color:var(--cyan); color:var(--cyan); }}
.view-tabs {{ display:flex; flex-wrap:wrap; gap:8px; margin:12px 0; }}
.view-tab[aria-selected="true"] {{ border-color:var(--cyan); color:var(--cyan); }}
.view-panel[hidden] {{ display:none; }}
.view-grid {{ display:grid; grid-template-columns:minmax(0,2fr) minmax(330px,1fr); gap:14px; }}
.globe {{ min-height:590px; touch-action:none; }}
.toolbar {{ display:flex; flex-wrap:wrap; gap:9px; align-items:center; margin:10px 0; }}
.toolbar label {{ display:flex; gap:6px; align-items:center; }}
.toolbar select {{ max-width:360px; }}
.legend {{ display:flex; flex-wrap:wrap; gap:12px; margin:10px 0; }}
.swatch {{ display:inline-block; width:13px; height:4px; margin-right:5px; vertical-align:middle; }}
.structured {{ max-height:590px; overflow:auto; }}
.structured table {{ font-size:13px; margin-bottom:16px; }}
.structured h3 {{ margin-top:18px; }}
.audit pre {{ max-height:320px; overflow:auto; font-size:12px; }}
.difference-items {{ display:grid; gap:7px; margin:10px 0; max-height:260px; overflow:auto; }}
.difference-item {{ text-align:left; width:100%; }}
.difference-item[aria-selected="true"] {{ border-color:var(--cyan); }}
.transition-note {{ border-left:3px solid #ffbd73; padding:7px 10px; }}
.empty {{ color:var(--muted); padding:12px 0; }}
@media (max-width:980px) {{ .view-grid {{ grid-template-columns:1fr; }} }}
</style>
<section class="card">
  <span class="status PASS">NON-AUTHORITATIVE VIEW</span>
  <h2>Accepted A–F results · focused read-only presets</h2>
  <p>All five presets combine shared static spatial context with one focused
  task overlay. They share the formal Slice/Difference timeline, camera and
  applicable selections. The page does not run a Model, propagate Position,
  form Connections, route, compare Slices or update the static container
  network.</p>
</section>
<section><h2>Bundle inventory</h2><div class="grid stats">{stats}</div></section>
<section class="card" id="shared-timeline">
  <h2>Shared timeline</h2>
  <div class="timeline">
    <button id="previous-slice" type="button">Previous</button>
    <button id="play-pause" type="button">Play</button>
    <button id="next-slice" type="button">Next</button>
    <input id="slice-range" type="range" min="0"
      max="{2 * inventory['slices'] - 2}" value="0" step="1">
    <label><input id="sync-camera" type="checkbox" checked>Sync camera</label>
  </div>
  <div id="timeline-rail" class="timeline-rail"
    aria-label="Interleaved Slice and Difference timeline"></div>
  <p><strong id="slice-label"></strong> · <span id="timestamp-label"></span></p>
  <p id="difference-summary" class="muted"></p>
</section>
<nav class="view-tabs card" aria-label="Slice and Difference inspection views">
  <button class="view-tab" data-view="topology" aria-selected="true">Topology / Orbit</button>
  <button class="view-tab" data-view="isl" aria-selected="false">ISL Connections</button>
  <button class="view-tab" data-view="service" aria-selected="false">Ground / Zone Service</button>
  <button class="view-tab" data-view="routing" aria-selected="false">Routing</button>
  <button class="view-tab" data-view="difference" aria-selected="false">Difference</button>
</nav>

<section class="view-panel" data-panel="topology">
  <div class="view-grid">
    <div class="card">
      <h2>Topology / Orbit view · <span class="view-context"></span></h2>
      <div class="toolbar">
        <label>Orbit <select id="orbit-select"></select></label>
        <label><input id="topology-ground-layer" type="checkbox" checked>GroundStations</label>
        <label><input id="topology-zone-layer" type="checkbox" checked>Zones</label>
        <button class="reset-view" data-canvas="topology-globe" type="button">Reset</button>
      </div>
      <div class="legend">
        <span>Orbit colors distinguish public Orbit ownership</span>
        <span><i class="swatch" style="background:#ffb454"></i>GroundStation</span>
        <span><i class="swatch" style="background:#d7f171"></i>Zone boundary</span>
      </div>
      <canvas class="globe" id="topology-globe" width="1200" height="650"></canvas>
      <p class="muted">Orbit guides are non-authoritative presentation
      helpers fitted through Earth center from each formal Slice's Satellite
      ECEF Positions. A Δ frame shows only its two formal endpoints; it does
      not interpolate Position.</p>
    </div>
    <div class="card structured" id="orbit-detail"></div>
  </div>
</section>

<section class="view-panel" data-panel="isl" hidden>
  <div class="view-grid">
    <div class="card">
      <h2>ISL Connection view · <span class="view-context"></span></h2>
      <div class="toolbar">
        <label>Orbit filter <select id="isl-orbit-filter"></select></label>
        <label>Local node <select id="isl-node-filter"></select></label>
        <label><input id="isl-orbit-layer" type="checkbox" checked>Stable Orbit context</label>
        <label><input id="isl-satellite-layer" type="checkbox" checked>Stable constellation context</label>
        <button class="reset-view" data-canvas="isl-globe" type="button">Reset</button>
      </div>
      <div class="legend">
        <span><i class="swatch" style="background:#4fd1c5"></i>same-orbit ISL</span>
        <span><i class="swatch" style="background:#7aa7ff"></i>cross-orbit ISL</span>
        <span><i class="swatch" style="background:#9b8cff"></i>previous / predecessor Orbit</span>
        <span><i class="swatch" style="background:#5ab4ff"></i>next / successor Orbit</span>
        <span><i class="swatch" style="background:#ff6464"></i>removed</span>
        <span><i class="swatch" style="background:#5dff8b"></i>added</span>
        <span><i class="swatch" style="background:#ff9f43"></i>parameters</span>
      </div>
      <canvas class="globe" id="isl-globe" width="1200" height="650"></canvas>
      <p class="muted">No GroundStation access, Zone service or Route is
      rendered. At a Δ marker, only the relevant formal ISL Difference
      overlays are added to the previous Slice base.</p>
    </div>
    <div class="card structured" id="isl-detail"></div>
  </div>
</section>

<section class="view-panel" data-panel="service" hidden>
  <div class="view-grid">
    <div class="card">
      <h2>Ground Access / Zone Service view · <span class="view-context"></span></h2>
      <div class="toolbar">
        <label><input id="service-orbit-layer" type="checkbox" checked>Stable Orbit context</label>
        <label><input id="service-satellite-layer" type="checkbox" checked>Stable constellation context</label>
        <button class="reset-view" data-canvas="service-globe" type="button">Reset</button>
      </div>
      <div class="legend">
        <span><i class="swatch" style="background:#ffd166"></i>GroundStation access</span>
        <span><i class="swatch" style="background:#e879f9"></i>Zone service</span>
        <span><i class="swatch" style="background:#ff6464"></i>unserved Zone</span>
        <span><i class="swatch" style="background:#ff6464"></i>previous/removed</span>
        <span><i class="swatch" style="background:#5dff8b"></i>current/added</span>
      </div>
      <canvas class="globe" id="service-globe" width="1200" height="650"></canvas>
      <p class="muted">ISLs and Routes are absent. At a Δ marker, only formal
      GroundStation-access and Zone-service Difference overlays are shown.</p>
    </div>
    <div class="card structured" id="service-detail"></div>
  </div>
</section>

<section class="view-panel" data-panel="routing" hidden>
  <div class="view-grid">
    <div class="card">
      <h2>Routing view · <span class="view-context"></span></h2>
      <div class="toolbar">
        <label>Source <select id="route-source"></select></label>
        <label>G/Z target <select id="route-target"></select></label>
        <label><input id="routing-orbit-layer" type="checkbox" checked>Stable Orbit context</label>
        <label><input id="routing-satellite-layer" type="checkbox" checked>Stable constellation context</label>
        <label><input id="routing-ground-layer" type="checkbox" checked>GroundStations</label>
        <button class="reset-view" data-canvas="routing-globe" type="button">Reset</button>
      </div>
      <div class="legend">
        <span><i class="swatch" style="background:#ffffff"></i>formal resolved path</span>
        <span><i class="swatch" style="background:#5dff8b"></i>source</span>
        <span><i class="swatch" style="background:#ffb454"></i>G/Z target</span>
        <span><i class="swatch" style="background:#ff6464"></i>previous path at Δ</span>
        <span><i class="swatch" style="background:#5dff8b"></i>current path at Δ</span>
      </div>
      <canvas class="globe" id="routing-globe" width="1200" height="650"></canvas>
      <p class="muted">Unrelated Connections are hidden. Every displayed path
      is the recorded result of Slicer.resolve_path().</p>
    </div>
    <div class="card structured" id="route-detail"></div>
  </div>
</section>

<section class="view-panel" data-panel="difference" hidden>
  <div class="view-grid">
    <div class="card">
      <h2>Transition Inspector · <span class="view-context"></span></h2>
      <div class="toolbar">
        <label>Category
          <select id="difference-category">
            <option value="connection-structure">Connection add/remove</option>
            <option value="parameters">Parameter patches</option>
            <option value="zone">Zone patches</option>
            <option value="route">Route patches</option>
          </select>
        </label>
        <label><input id="difference-orbit-layer" type="checkbox" checked>Relevant Orbit context</label>
        <label><input id="difference-satellite-layer" type="checkbox">Show full constellation context</label>
        <button class="reset-view" data-canvas="difference-globe" type="button">Reset</button>
      </div>
      <canvas class="globe" id="difference-globe" width="1200" height="650"></canvas>
      <p class="muted">Detailed audit of the same previous-base Transition
      shown by every task view. Exactly one Difference category and selected
      structured item are emphasized at a time.</p>
    </div>
    <div class="card structured">
      <div id="difference-list"></div>
      <div id="difference-detail"></div>
    </div>
  </div>
</section>

<details class="card audit">
  <summary>Raw JSON / Audit View for the current timeline item</summary>
  <pre id="audit-json"></pre>
</details>
"""
    script = _SCRIPT.replace("__SLICE_DATA__", json_for_script(data))
    return standalone_page(
        title="Slice / Difference Inspection Tool",
        eyebrow="Accepted results · offline read-only views",
        body=body,
        script=script,
        language_strings={
            "Slice / Difference Inspection Tool": "切片 / 差异检查工具",
            "Accepted results · offline read-only views": "已接受结果 · 离线只读视图",
            "Accepted A–F results · focused read-only presets": "已接受的 A–F 结果 · 聚焦只读预设",
            "All five presets combine shared static spatial context with one focused task overlay. They share the formal Slice/Difference timeline, camera and applicable selections. The page does not run a Model, propagate Position, form Connections, route, compare Slices or update the static container network.": "五个预设将共享的静态空间上下文与一个聚焦任务叠层组合在一起，并共享正式的 Slice / Difference 时间轴、相机和适用选择。页面不会运行 Model、传播 Position、形成 Connection、计算路由、比较 Slice 或更新静态容器网络。",
            "Bundle inventory": "产物内容概览",
            "Shared timeline": "共享时间轴",
            "Play": "播放",
            "Sync camera": "同步相机",
            "Interleaved Slice and Difference timeline": "交错的 Slice 与 Difference 时间轴",
            "Slice and Difference inspection views": "Slice 与 Difference 检查视图",
            "Topology / Orbit": "拓扑 / 轨道",
            "ISL Connections": "ISL 连接",
            "Ground / Zone Service": "地面接入 / Zone 服务",
            "Routing": "路由",
            "Difference": "差异",
            "Orbit": "轨道",
            "GroundStations": "地面站",
            "Zones": "Zone",
            "Reset": "重置视角",
            "Orbit colors distinguish public Orbit ownership": "轨道颜色区分 public Orbit ownership",
            "Zone boundary": "Zone 边界",
            "Orbit filter": "轨道筛选",
            "Local node": "本地节点",
            "Stable Orbit context": "稳定轨道上下文",
            "Stable constellation context": "稳定星座上下文",
            "same-orbit ISL": "同轨 ISL",
            "cross-orbit ISL": "跨轨 ISL",
            "previous / predecessor Orbit": "上一 / 前驱轨道",
            "next / successor Orbit": "下一 / 后继轨道",
            "removed": "已移除",
            "added": "已添加",
            "parameters": "参数",
            "GroundStation access": "地面站接入",
            "Zone service": "Zone 服务",
            "unserved Zone": "未服务 Zone",
            "previous/removed": "此前 / 已移除",
            "current/added": "当前 / 已添加",
            "Source": "源节点",
            "G/Z target": "G/Z 目标",
            "formal resolved path": "正式解析路径",
            "source": "源节点",
            "previous path at Δ": "Δ 前路径",
            "current path at Δ": "Δ 后路径",
            "Transition Inspector": "Transition 检查",
            "Category": "类别",
            "Connection add/remove": "Connection 添加 / 移除",
            "Parameter patches": "参数补丁",
            "Zone patches": "Zone 补丁",
            "Route patches": "路由补丁",
            "Relevant Orbit context": "相关轨道上下文",
            "Show full constellation context": "显示完整星座上下文",
            "Raw JSON / Audit View for the current timeline item": "当前时间轴项目的原始 JSON / 审核视图",
        },
    )


_SCRIPT = r"""
var DATA=__SLICE_DATA__;
var nodesById=Object.fromEntries(DATA.topology.nodes.map(value=>[value.node_id,value]));
var zonesById=Object.fromEntries(DATA.topology.zones.map(value=>[value.zone_id,value]));
var orbitsByRef=Object.fromEntries(DATA.topology.orbits.map(value=>[value.orbit_ref,value]));
var orbitRefBySatellite=Object.fromEntries(
  DATA.topology.orbits.flatMap(orbit=>orbit.satellite_ids.map(node=>[node,orbit.orbit_ref]))
);
var WGS84_DISPLAY_RADIUS_M=6378137;
const orbitColors=["#4fd1c5","#7aa7ff","#e879f9","#ffd166","#5dff8b","#ff8f70"];
const canvasIds=["topology-globe","isl-globe","service-globe","routing-globe","difference-globe"];
const viewState=Object.fromEntries(
  canvasIds.map(id=>[id,{yaw:-.65,pitch:.28,drag:null}])
);
var sharedCamera={yaw:-.65,pitch:.28,drag:null};
let timelineIndex=0;
let activeView="topology";
let timer=null;
let selectedDifferenceIndex=0;
let selectedNodeId="*";
let selectedOrbitRef="*";
let selectedRouteSource="";
let selectedRouteTarget="";
const range=document.getElementById("slice-range");

function isTransition(){return timelineIndex%2===1;}
function displayBaseSliceIndex(){return Math.floor(timelineIndex/2);}
function displayCurrentSliceIndex(){return Math.ceil(timelineIndex/2);}
function displayBaseSlice(){return DATA.slices[displayBaseSliceIndex()];}
function displayPreviousSlice(){return isTransition()?displayBaseSlice():null;}
function displayCurrentSlice(){return DATA.slices[displayCurrentSliceIndex()];}
function displayDifference(){
  return isTransition()?displayCurrentSlice().difference_from_previous:null;
}
function timelineLabel(){
  return isTransition()
    ?`Δ${displayPreviousSlice().slice_id}→${displayCurrentSlice().slice_id}`
    :`Slice ${displayBaseSlice().slice_id}`;
}
const positionMap=(slice=displayBaseSlice())=>Object.fromEntries(
  slice.positions.map(value=>[value.node_id,value])
);
const escapeHtml=value=>String(value??"").replace(/[&<>"']/g,char=>({
  "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
}[char]));
const table=(headers,rows)=>rows.length
  ?`<table><thead><tr>${headers.map(value=>`<th>${escapeHtml(value)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(row=>`<tr>${row.map(value=>`<td>${escapeHtml(value)}</td>`).join("")}</tr>`).join("")}</tbody></table>`
  :'<p class="empty">None for this Slice/filter.</p>';
const section=(title,content)=>`<h3>${escapeHtml(title)}</h3>${content}`;
const targetLabel=target=>target.kind==="G"
  ?`G · ${target.ground_station_id}`:`Z · ${target.zone_id}`;
const targetIdentity=target=>target.kind==="G"
  ?target.ground_station_id:target.zone_id;
const parameters=value=>`${value.bandwidth_bps} bps · ${value.delay_us} µs · ${value.packet_loss_ppm} ppm`;

function cameraFor(canvas){
  return document.getElementById("sync-camera").checked
    ?sharedCamera:viewState[canvas.id];
}
function rotate(point,state){
  const [x,y,z]=point,cy=Math.cos(state.yaw),sy=Math.sin(state.yaw);
  const cp=Math.cos(state.pitch),sp=Math.sin(state.pitch);
  const x1=x*cy+z*sy,z1=-x*sy+z*cy;
  return [x1,y*cp-z1*sp,y*sp+z1*cp];
}
function sphere(latitude,longitude,radius=1){
  const a=latitude*Math.PI/180,b=longitude*Math.PI/180;
  return [radius*Math.cos(a)*Math.cos(b),radius*Math.sin(a),
    radius*Math.cos(a)*Math.sin(b)];
}
function project(canvas,point){
  const state=cameraFor(canvas),[x,y,z]=rotate(point,state),scale=245;
  return [canvas.width/2+x*scale,canvas.height/2-y*scale,z];
}
function ecefToDisplay(vector,scale=1/WGS84_DISPLAY_RADIUS_M){
  return [Number(vector.x)*scale,Number(vector.z)*scale,Number(vector.y)*scale];
}
function nodePoint(nodeId,positions=positionMap()){
  const position=positions[nodeId];
  if(!position)return null;
  return ecefToDisplay(position.ecef_m);
}
function drawPolyline(canvas,values,color,width=1,dashed=false,opacity=1){
  const ctx=canvas.getContext("2d");
  ctx.save();ctx.globalAlpha=opacity;ctx.strokeStyle=color;ctx.lineWidth=width;
  ctx.setLineDash(dashed?[9,7]:[]);ctx.beginPath();
  let open=false;
  values.forEach(value=>{
    const point=project(canvas,value);
    if(point[2]<0){open=false;return;}
    if(!open){ctx.moveTo(point[0],point[1]);open=true;}
    else ctx.lineTo(point[0],point[1]);
  });
  ctx.stroke();ctx.restore();
}
function drawSegment(canvas,first,second,color,width=2,dashed=false,opacity=1){
  if(!first||!second)return;
  const ctx=canvas.getContext("2d"),a=project(canvas,first),b=project(canvas,second);
  if(a[2]<-.2&&b[2]<-.2)return;
  ctx.save();ctx.globalAlpha=opacity;ctx.strokeStyle=color;ctx.lineWidth=width;
  ctx.setLineDash(dashed?[9,7]:[]);
  ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke();ctx.restore();
}
function drawPoint(canvas,point,color,radius=6,label=null,opacity=1){
  if(!point)return;
  const ctx=canvas.getContext("2d"),value=project(canvas,point);
  if(value[2]<0)return;
  ctx.save();ctx.globalAlpha=opacity;ctx.fillStyle=color;
  ctx.beginPath();ctx.arc(value[0],value[1],radius,0,Math.PI*2);
  ctx.fill();ctx.strokeStyle="#07111f";ctx.lineWidth=2;ctx.stroke();
  if(label){ctx.fillStyle="#edf6ff";ctx.font="12px system-ui";ctx.fillText(label,value[0]+8,value[1]-8);}
  ctx.restore();
}
function drawHollowPoint(canvas,point,color,radius=7,label=null,opacity=.72){
  if(!point)return;
  const ctx=canvas.getContext("2d"),value=project(canvas,point);
  if(value[2]<0)return;
  ctx.save();ctx.globalAlpha=opacity;ctx.strokeStyle=color;ctx.lineWidth=2.5;
  ctx.beginPath();ctx.arc(value[0],value[1],radius,0,Math.PI*2);ctx.stroke();
  if(label){ctx.fillStyle=color;ctx.font="12px system-ui";ctx.fillText(label,value[0]+9,value[1]-9);}
  ctx.restore();
}
function drawBase(canvas){
  const ctx=canvas.getContext("2d");
  ctx.clearRect(0,0,canvas.width,canvas.height);
  const gradient=ctx.createRadialGradient(canvas.width*.43,canvas.height*.39,12,
    canvas.width/2,canvas.height/2,245);
  gradient.addColorStop(0,"#27739d");gradient.addColorStop(1,"#08233a");
  ctx.fillStyle=gradient;ctx.beginPath();ctx.arc(canvas.width/2,canvas.height/2,245,0,Math.PI*2);ctx.fill();
  for(let lat=-60;lat<=60;lat+=30)
    drawPolyline(canvas,Array.from({length:73},(_,i)=>sphere(lat,-180+i*5)),"#8dc6dd44");
  for(let lon=-150;lon<180;lon+=30)
    drawPolyline(canvas,Array.from({length:37},(_,i)=>sphere(-90+i*5,lon)),"#8dc6dd44");
}
function zoneBoundary(zone){
  const g=zone.geometry,values=[];let east=g.east_longitude_deg;
  if(east<=g.west_longitude_deg)east+=360;
  for(let i=0;i<=20;i++)values.push([g.south_latitude_deg,g.west_longitude_deg+(east-g.west_longitude_deg)*i/20]);
  for(let i=1;i<=12;i++)values.push([g.south_latitude_deg+(g.north_latitude_deg-g.south_latitude_deg)*i/12,east]);
  for(let i=1;i<=20;i++)values.push([g.north_latitude_deg,east-(east-g.west_longitude_deg)*i/20]);
  for(let i=1;i<=12;i++)values.push([g.north_latitude_deg-(g.north_latitude_deg-g.south_latitude_deg)*i/12,g.west_longitude_deg]);
  return values.map(([lat,lon])=>sphere(lat,lon,1.006));
}
function orbitGuide(guide){
  const basisU=guide.basis_u_ecef_unit,basisV=guide.basis_v_ecef_unit;
  const radius=guide.mean_radius_m/WGS84_DISPLAY_RADIUS_M;
  return Array.from({length:145},(_,index)=>{
    const phase=index*Math.PI*2/144;
    const ecef={
      x:radius*(basisU.x*Math.cos(phase)+basisV.x*Math.sin(phase)),
      y:radius*(basisU.y*Math.cos(phase)+basisV.y*Math.sin(phase)),
      z:radius*(basisU.z*Math.cos(phase)+basisV.z*Math.sin(phase))
    };
    return ecefToDisplay(ecef,1);
  });
}
function orbitGuideMap(slice){
  return Object.fromEntries(slice.orbit_guides.map(value=>[value.orbit_ref,value]));
}
function focusNodes(canvasId,nodeIds){
  const positions=comparisonPositionMap();
  const values=nodeIds.map(id=>positions[id]?.geographic).filter(Boolean);
  focusLocations(canvasId,values,nodeIds.join("|"));
}
function focusLocations(canvasId,values,focusLabel){
  if(!values.length)return;
  const latitude=values.reduce((total,value)=>total+value.latitude_deg,0)/values.length;
  const longitude=values.reduce((total,value)=>total+value.longitude_deg,0)/values.length;
  const state=cameraFor(document.getElementById(canvasId));
  state.yaw=longitude*Math.PI/180-Math.PI/2;
  state.pitch=latitude*Math.PI/180;
  document.getElementById(canvasId).dataset.focus=focusLabel;
}
function focusPath(path){
  const positions=comparisonPositionMap();
  const values=path.nodes.map(id=>positions[id]?.geographic).filter(Boolean);
  if(path.target.kind==="Z"){
    values.push(zonesById[path.target.zone_id].representative_point);
  }
  focusLocations("routing-globe",values,path.path_id);
}
function identityRows(nodeId){
  const node=nodesById[nodeId];
  if(!node)return [];
  return [
    ["NodeId",node.node_id],["kind",node.kind],["AS",node.asn],
    ["Shell",node.shell_id??"—"],["Orbit",node.orbit_id??"—"],
    ["Orbit ref",node.orbit_ref??"—"]
  ];
}
function positionRows(nodeId,positions=positionMap()){
  const position=positions[nodeId];
  if(!position)return [];
  return [
    ["ECEF X",`${position.ecef_m.x} m`],["ECEF Y",`${position.ecef_m.y} m`],
    ["ECEF Z",`${position.ecef_m.z} m`],
    ["latitude",`${position.geographic.latitude_deg}°`],
    ["longitude",`${position.geographic.longitude_deg}°`],
    ["ellipsoidal height",`${position.geographic.ellipsoidal_height_m} m`]
  ];
}
function connectionFor(first,second,slice=displayBaseSlice()){
  return slice.connections.find(value=>{
    const id=value.connection_id;
    return (id.endpoint_a===first&&id.endpoint_b===second)||
      (id.endpoint_a===second&&id.endpoint_b===first);
  })??null;
}
function orbitColor(orbitRef){
  const index=DATA.topology.orbits.findIndex(value=>value.orbit_ref===orbitRef);
  return orbitColors[(index<0?0:index)%orbitColors.length];
}
function drawOrbitContext(canvas,slice,{strong=false,orbitRef="*",color=null,dashed=false,opacity=null}={}){
  const orbits=orbitRef==="*"
    ?DATA.topology.orbits:DATA.topology.orbits.filter(value=>value.orbit_ref===orbitRef);
  const guides=orbitGuideMap(slice);
  let drawn=0;
  orbits.forEach(orbit=>{
    const guide=guides[orbit.orbit_ref];
    if(guide){
      drawPolyline(
        canvas,orbitGuide(guide),color??orbitColor(orbit.orbit_ref),
        strong?2.4:1.2,dashed,opacity??(strong?.9:.28)
      );
      drawn+=1;
    }
  });
  return drawn;
}
function drawSatelliteContext(canvas,positions,{strong=false,orbitRef="*"}={}){
  DATA.topology.nodes.filter(value=>
    value.kind==="satellite"&&(orbitRef==="*"||value.orbit_ref===orbitRef)
  ).forEach(node=>drawPoint(
    canvas,nodePoint(node.node_id,positions),orbitColor(node.orbit_ref),
    strong?5:3,null,strong ? .95 : .35
  ));
}
function drawGroundContext(canvas,positions,{strong=false}={}){
  DATA.topology.nodes.filter(value=>value.kind==="ground-station").forEach(node=>
    drawPoint(canvas,nodePoint(node.node_id,positions),"#ffb454",strong?8:5,
      strong?node.node_id.split("/").at(-1):null,strong?1:.48)
  );
}
function drawZoneContext(canvas,{strong=false,zoneId=null}={}){
  DATA.topology.zones.filter(zone=>zoneId===null||zone.zone_id===zoneId).forEach(zone=>
    drawPolyline(canvas,zoneBoundary(zone),"#d7f171",strong?3:1.4,false,strong?1:.42)
  );
}
function comparisonSlice(){
  if(isTransition()&&displayBaseSlice().positions.length===0){
    return displayCurrentSlice();
  }
  return displayBaseSlice();
}
function comparisonPositionMap(){return positionMap(comparisonSlice());}
function stableComparisonLayerNames(){
  if(!isTransition())return [];
  return [
    "transition-previous-network-base",
    "stable-comparison-geometry",
    ...(displayBaseSlice().positions.length===0
      ?["comparison-geometry-current-fallback"]:["comparison-geometry-previous"])
  ];
}
function drawStableOrbitContext(canvas,{strong=false,orbitRef="*",color=null}={}){
  return drawOrbitContext(
    canvas,comparisonSlice(),{strong,orbitRef,color,opacity:strong?.9:.2}
  );
}
function drawStableSatelliteContext(canvas,{strong=false,orbitRef="*"}={}){
  drawSatelliteContext(canvas,comparisonPositionMap(),{strong,orbitRef});
}
function drawPositionComparisonOrbitContext(canvas,{strong=false,orbitRef="*"}={}){
  const base=displayBaseSlice();
  const previousCount=drawOrbitContext(canvas,base,{strong,orbitRef});
  let currentCount=0;
  if(isTransition()){
    currentCount=drawOrbitContext(
      canvas,displayCurrentSlice(),
      {strong:true,orbitRef,color:"#5dff8b",dashed:true,opacity:.6}
    );
  }
  return {previousCount,currentCount};
}
function drawPositionComparisonSatelliteContext(canvas,{strong=false,orbitRef="*"}={}){
  const previousPositions=positionMap(displayBaseSlice());
  drawSatelliteContext(canvas,previousPositions,{strong,orbitRef});
  if(!isTransition())return;
  const currentPositions=positionMap(displayCurrentSlice());
  DATA.topology.nodes.filter(value=>
    value.kind==="satellite"&&(orbitRef==="*"||value.orbit_ref===orbitRef)
  ).forEach(node=>{
    const previous=nodePoint(node.node_id,previousPositions);
    const current=nodePoint(node.node_id,currentPositions);
    drawSegment(canvas,previous,current,"#ffbd73",1.5,true,.55);
    drawHollowPoint(
      canvas,current,orbitColor(node.orbit_ref),strong?6:4,null,strong?.82:.52
    );
  });
}
function positionComparisonLayerNames(){
  return isTransition()
    ?[
      "transition-previous-base",
      "transition-current-reference",
      "position-motion-vectors",
      "position-comparison-endpoints",
      "no-position-interpolation"
    ]
    :[];
}
function drawSelectedNodePositionComparison(canvas,nodeId){
  if(!isTransition()||!nodeId||nodeId==="*")return false;
  const previousPositions=positionMap(displayPreviousSlice());
  const currentPositions=positionMap(displayCurrentSlice());
  const previous=nodePoint(nodeId,previousPositions);
  const current=nodePoint(nodeId,currentPositions);
  if(!previous||!current)return false;
  drawSegment(canvas,previous,current,"#ffbd73",2,true,.75);
  drawPoint(canvas,previous,"#ff8a8a",7,"previous");
  drawHollowPoint(canvas,current,"#5dff8b",8,"current",.9);
  return true;
}
function orbitRefsForNodes(nodeIds){
  return [...new Set(
    nodeIds.map(nodeId=>orbitRefBySatellite[nodeId]).filter(Boolean)
  )];
}

function drawTopology(){
  const canvas=document.getElementById("topology-globe");
  drawBase(canvas);
  const selected=selectedOrbitRef;
  const orbits=selected==="*" ? DATA.topology.orbits : DATA.topology.orbits.filter(value=>value.orbit_ref===selected);
  const guideCounts=drawPositionComparisonOrbitContext(
    canvas,{strong:true,orbitRef:selected}
  );
  drawPositionComparisonSatelliteContext(
    canvas,{strong:true,orbitRef:selected}
  );
  const layers=["satellites",...positionComparisonLayerNames()];
  if(guideCounts.previousCount)layers.push("ecef-orbit-guides-previous");
  if(guideCounts.currentCount)layers.push("ecef-orbit-guides-current");
  if(document.getElementById("topology-ground-layer").checked){
    drawGroundContext(canvas,positionMap(displayBaseSlice()),{strong:true});
    layers.push("ground-stations");
  }
  if(document.getElementById("topology-zone-layer").checked){
    drawZoneContext(canvas,{strong:true});
    layers.push("zones");
  }
  canvas.dataset.layers=layers.join(",");
  const detail=selected==="*" ? DATA.topology.orbits : orbits;
  document.getElementById("orbit-detail").innerHTML=
    (isTransition()?'<p class="transition-note">Previous formal Slice is the base. Hollow points and dashed green guides are the current formal Slice reference; short vectors connect only the two formal endpoints. No intermediate Position exists.</p>':"")
    +section("Orbit identity and static parameters",table(
      ["Orbit","AS / Shell","RAAN","inclination","altitude","epoch","Satellites"],
      detail.map(orbit=>[
        orbit.orbit_id,`AS${orbit.asn} / ${orbit.shell_id}`,
        `${orbit.right_ascension_deg}°`,`${orbit.inclination_deg}°`,
        `${orbit.altitude_km} km`,orbit.epoch_utc,orbit.satellite_ids.length
      ])
    ))+section("View boundary",displayBaseSlice().orbit_guides.length
      ?"Guides are fitted from formal ECEF Positions; static RAAN is identity detail only."
      :"No dynamic Orbit guide exists because this formal Slice has no Position.");
}

function connectionMatchesIslFilter(value,orbit,node){
  if(value.connection_id.kind!=="inter-satellite")return false;
  const id=value.connection_id;
  if(orbit!=="*"&&orbitRefBySatellite[id.endpoint_a]!==orbit&&orbitRefBySatellite[id.endpoint_b]!==orbit)return false;
  return node==="*"||id.endpoint_a===node||id.endpoint_b===node;
}
function crossPlaneSide(value,node){
  if(value.display_kind!=="cross-orbit-isl"||!value.cross_plane_sides)return "—";
  const id=value.connection_id;
  if(node===id.endpoint_a)return value.cross_plane_sides.endpoint_a;
  if(node===id.endpoint_b)return value.cross_plane_sides.endpoint_b;
  return `A: ${value.cross_plane_sides.endpoint_a} / B: ${value.cross_plane_sides.endpoint_b}`;
}
function islDisplayColor(value,node){
  if(value.display_kind==="same-orbit-isl")return "#4fd1c5";
  const side=crossPlaneSide(value,node);
  if(side==="predecessor-orbit")return "#9b8cff";
  if(side==="successor-orbit")return "#5ab4ff";
  return "#7aa7ff";
}
function drawIsl(){
  const canvas=document.getElementById("isl-globe");
  const baseSlice=displayBaseSlice(),positions=comparisonPositionMap();
  drawBase(canvas);
  const orbit=selectedOrbitRef;
  const node=selectedNodeId;
  const layers=[...stableComparisonLayerNames()];
  if(document.getElementById("isl-orbit-layer").checked){
    if(drawStableOrbitContext(canvas,{orbitRef:orbit})){
      layers.push("stable-ecef-orbit-guides");
    }
  }
  if(document.getElementById("isl-satellite-layer").checked){
    drawStableSatelliteContext(canvas,{orbitRef:orbit});
    layers.push("stable-satellite-context");
  }
  const connections=baseSlice.connections.filter(
    value=>connectionMatchesIslFilter(value,orbit,node)
  );
  connections.forEach(value=>{
    const id=value.connection_id;
    drawSegment(canvas,nodePoint(id.endpoint_a,positions),nodePoint(id.endpoint_b,positions),
      islDisplayColor(value,node),
      isTransition()?1.5:(node==="*"?2:5),false,isTransition()?.3:1);
  });
  if(isTransition()){
    const difference=displayDifference();
    difference.removed_connection_ids.filter(
      value=>connectionMatchesIslFilter(value,orbit,node)
    ).forEach(value=>{
      const id=value.connection_id;
      drawSegment(
        canvas,nodePoint(id.endpoint_a,positions),nodePoint(id.endpoint_b,positions),
        "#ff6464",6,true,.95
      );
    });
    difference.added_connections.filter(
      value=>connectionMatchesIslFilter(value,orbit,node)
    ).forEach(value=>{
      const id=value.connection_id;
      drawSegment(
        canvas,nodePoint(id.endpoint_a,positions),nodePoint(id.endpoint_b,positions),
        "#5dff8b",6,true,.9
      );
    });
    difference.connection_parameter_patches.filter(
      value=>connectionMatchesIslFilter(value,orbit,node)
    ).forEach(value=>{
      const id=value.connection_id;
      drawSegment(
        canvas,nodePoint(id.endpoint_a,positions),nodePoint(id.endpoint_b,positions),
        "#ff9f43",6,false,.95
      );
    });
    layers.push("isl-removed","isl-added","isl-parameter-patch");
    if(drawSelectedNodePositionComparison(canvas,node)){
      layers.push("selected-node-position-comparison");
    }
  }
  const visible=new Set(connections.flatMap(value=>[
    value.connection_id.endpoint_a,value.connection_id.endpoint_b
  ]));
  visible.forEach(id=>drawPoint(
    canvas,nodePoint(id,positions),orbitColor(orbitRefBySatellite[id]),id===node?9:5,
    id===node?id.split("/").at(-1):null
  ));
  layers.push("same-orbit-isl","cross-orbit-isl","filtered-satellites");
  canvas.dataset.layers=layers.join(",");
  const rows=connections.map(value=>{
    const id=value.connection_id,fromA=node==="*"||id.endpoint_a===node;
    return [
      value.display_kind,crossPlaneSide(value,node),id.endpoint_a,id.endpoint_b,
      parameters(fromA?value.endpoint_a_to_b:value.endpoint_b_to_a),
      parameters(fromA?value.endpoint_b_to_a:value.endpoint_a_to_b)
    ];
  });
  let detail=section("Filtered ISLs",table(
    ["class","adjacent Orbit side","endpoint A","endpoint B","A/local outbound","B/local inbound"],rows
  ));
  if(node!=="*"){
    detail=section("Selected node",table(["field","value"],identityRows(node)))
      +section(isTransition()?"Comparison Position":"Current Position",
        table(["field","value"],positionRows(node,positions)))+detail;
  }
  if(isTransition()){
    detail='<p class="transition-note">Previous formal ISLs are the weak network base. All logical relations use one Tool-local comparison geometry ('+escapeHtml(comparisonSlice().slice_id===displayCurrentSlice().slice_id&&displayBaseSlice().positions.length===0?"current Slice fallback for Δ0→1":"previous Slice formal Positions")+'). Removed, added and parameter-patched ISLs are Difference overlays; no full-constellation Position movement is shown.</p>'+detail;
  }
  document.getElementById("isl-detail").innerHTML=detail;
}

function drawService(){
  const canvas=document.getElementById("service-globe");
  const baseSlice=displayBaseSlice(),positions=comparisonPositionMap();
  drawBase(canvas);
  const layers=[...stableComparisonLayerNames()];
  if(document.getElementById("service-orbit-layer").checked){
    if(drawStableOrbitContext(canvas))layers.push("stable-ecef-orbit-guides");
  }
  if(document.getElementById("service-satellite-layer").checked){
    drawStableSatelliteContext(canvas);layers.push("stable-satellite-context");
  }
  drawGroundContext(canvas,positions,{strong:true});
  drawZoneContext(canvas,{strong:true});
  layers.push("ground-stations","zones");
  const access=baseSlice.connections.filter(value=>value.connection_id.kind==="ground-station-access");
  access.forEach(value=>{
    const id=value.connection_id;
    drawSegment(
      canvas,nodePoint(id.endpoint_a,positions),nodePoint(id.endpoint_b,positions),
      "#ffd166",isTransition()?2:4,false,isTransition()?.3:1
    );
    drawPoint(canvas,nodePoint(id.endpoint_a,positions),"#ffb454",8);
    drawPoint(canvas,nodePoint(id.endpoint_b,positions),"#72f1e1",6);
  });
  const assignmentRecords=new Map(
    baseSlice.zone_service_assignments.map(value=>[value.zone_id,value])
  );
  const assigned=new Map(
    [...assignmentRecords].map(([zoneId,value])=>[
      zoneId,value.service_satellite_id
    ])
  );
  DATA.topology.zones.forEach(zone=>{
    const center=zone.representative_point;
    const zonePoint=sphere(center.latitude_deg,center.longitude_deg,1.008);
    const satellite=assigned.get(zone.zone_id);
    if(satellite){
      drawSegment(
        canvas,nodePoint(satellite,positions),zonePoint,"#e879f9",
        isTransition()?2:4,true,isTransition()?.3:1
      );
      drawPoint(canvas,zonePoint,"#e879f9",8,zone.zone_id);
      drawPoint(canvas,nodePoint(satellite,positions),"#72f1e1",6);
    }else drawPoint(canvas,zonePoint,"#ff6464",8,zone.zone_id);
  });
  if(isTransition()){
    const difference=displayDifference();
    difference.removed_connection_ids.filter(
      value=>value.connection_id.kind==="ground-station-access"
    ).forEach(value=>{
      const id=value.connection_id;
      drawSegment(
        canvas,nodePoint(id.endpoint_a,positions),nodePoint(id.endpoint_b,positions),
        "#ff6464",6,true,.95
      );
    });
    difference.added_connections.filter(
      value=>value.connection_id.kind==="ground-station-access"
    ).forEach(value=>{
      const id=value.connection_id;
      drawSegment(
        canvas,nodePoint(id.endpoint_a,positions),nodePoint(id.endpoint_b,positions),
        "#5dff8b",6,false,.9
      );
    });
    difference.connection_parameter_patches.filter(
      value=>value.connection_id.kind==="ground-station-access"
    ).forEach(value=>{
      const id=value.connection_id;
      drawSegment(
        canvas,nodePoint(id.endpoint_a,positions),nodePoint(id.endpoint_b,positions),
        "#ff9f43",6,false,.95
      );
    });
    difference.zone_service_assignment_patches.forEach(patch=>{
      const zone=zonesById[patch.zone_id],center=zone.representative_point;
      const zonePoint=sphere(center.latitude_deg,center.longitude_deg,1.008);
      const previousSatellite=assigned.get(patch.zone_id);
      const currentSatellite=displayCurrentSlice().zone_service_assignments.find(
        value=>value.zone_id===patch.zone_id
      )?.service_satellite_id;
      orbitRefsForNodes([previousSatellite,currentSatellite].filter(Boolean))
        .forEach(orbitRef=>drawStableOrbitContext(
          canvas,{strong:true,orbitRef}
        ));
      if(previousSatellite){
        drawSegment(
          canvas,nodePoint(previousSatellite,positions),zonePoint,
          "#ff6464",7,true,.95
        );
        drawPoint(canvas,nodePoint(previousSatellite,positions),"#ff6464",9,"previous");
      }
      if(currentSatellite){
        drawSegment(
          canvas,nodePoint(currentSatellite,positions),zonePoint,
          "#5dff8b",7,false,.95
        );
        drawPoint(
          canvas,nodePoint(currentSatellite,positions),"#5dff8b",10,"current",.95
        );
      }
    });
    layers.push(
      "access-removed","access-added","access-parameter-patch",
      "zone-service-transition","focused-service-orbits"
    );
  }
  layers.push("ground-station-access","zone-service");
  canvas.dataset.layers=layers.join(",");
  const accessRows=access.map(value=>[
    value.connection_id.endpoint_a,value.connection_id.endpoint_b,
    parameters(value.endpoint_a_to_b),parameters(value.endpoint_b_to_a)
  ]);
  const zoneRows=DATA.topology.zones.map(zone=>{
    const assignment=assignmentRecords.get(zone.zone_id);
    const explanation=assignment?.display_explanation;
    return [
      zone.zone_id,assignment?"served":"unserved",
      assignment?.service_satellite_id??"—",
      explanation
        ?`${Number(explanation.representative_point_elevation_deg).toFixed(3)}°`
        :"—",
      explanation
        ?(explanation.subpoint_inside_zone?"yes":"no")
        :"—"
    ];
  });
  document.getElementById("service-detail").innerHTML=
    (isTransition()?'<p class="transition-note">Previous access and service relations are the network base. Old/new relations share one stable comparison geometry; removals/old assignments are red and additions/new assignments are green. Unrelated Satellite Position movement is not rendered.</p>':"")
    +section("GroundStation access",table(
      ["endpoint A","endpoint B","A→B","B→A"],accessRows
    ))+section("Zone service",table(
      ["Zone","status","service Satellite","representative-point elevation","subpoint inside Zone"],zoneRows
    ))+'<p class="muted">The assignment is the formal ConnectionSlice result. Representative-point elevation and subpoint containment are non-authoritative explanations derived from that Slice’s formal Position. Eligibility uses representative-point visibility/minimum elevation; it is not subpoint containment.</p>';
}

function displayPaths(){
  if(!isTransition())return displayBaseSlice().paths;
  const paths=new Map(displayPreviousSlice().paths.map(path=>[path.path_id,path]));
  displayCurrentSlice().paths.forEach(path=>{
    if(!paths.has(path.path_id))paths.set(path.path_id,path);
  });
  return [...paths.values()];
}
function populateRouteTargets(){
  const source=document.getElementById("route-source").value;
  const target=document.getElementById("route-target");
  const available=displayPaths().filter(path=>path.source===source);
  target.replaceChildren();
  available.forEach(path=>{
    const option=document.createElement("option");
    option.value=path.path_id;option.textContent=targetLabel(path.target);
    target.append(option);
  });
  if(available.some(path=>path.path_id===selectedRouteTarget)){
    target.value=selectedRouteTarget;
  }
  selectedRouteTarget=target.value;
}
function selectedPath(){
  const reference=document.getElementById("route-target").value;
  return displayPaths().find(path=>path.path_id===reference)??null;
}
function selectedPathPair(){
  const reference=document.getElementById("route-target").value;
  return {
    previous:(isTransition()?displayPreviousSlice():displayBaseSlice()).paths.find(
      path=>path.path_id===reference
    )??null,
    current:displayCurrentSlice().paths.find(path=>path.path_id===reference)??null
  };
}
function drawRouting(){
  const canvas=document.getElementById("routing-globe");
  const baseSlice=displayBaseSlice(),positions=comparisonPositionMap();
  drawBase(canvas);
  const layers=[...stableComparisonLayerNames()];
  if(document.getElementById("routing-orbit-layer").checked){
    if(drawStableOrbitContext(canvas))layers.push("stable-ecef-orbit-guides");
  }
  if(document.getElementById("routing-satellite-layer").checked){
    drawStableSatelliteContext(canvas);layers.push("stable-satellite-context");
  }
  if(document.getElementById("routing-ground-layer").checked){
    drawGroundContext(canvas,positions);layers.push("ground-stations");
  }
  const pair=selectedPathPair();
  const path=isTransition()?(pair.current??pair.previous):pair.current;
  if(!path){
    layers.push("selected-path");
    canvas.dataset.layers=layers.join(",");
    document.getElementById("route-detail").innerHTML='<p class="empty">No formal path in Slice 0 or for this source.</p>';
    return;
  }
  const edgeRows=[];
  if(isTransition()){
    drawFormalPath(canvas,pair.previous,positions,"#ff6464",6,true);
    drawFormalPath(canvas,pair.current,positions,"#5dff8b",6,false);
    (pair.previous?.nodes??[]).forEach(id=>
      drawPoint(canvas,nodePoint(id,positions),"#ff8a8a",6)
    );
    (pair.current?.nodes??[]).forEach(id=>
      drawPoint(canvas,nodePoint(id,positions),"#5dff8b",8)
    );
    if(document.getElementById("routing-orbit-layer").checked){
      orbitRefsForNodes([
        ...(pair.previous?.nodes??[]),...(pair.current?.nodes??[])
      ]).forEach(orbitRef=>drawStableOrbitContext(
        canvas,{strong:true,orbitRef}
      ));
    }
    layers.push("previous-formal-path","current-formal-path");
  }else{
    for(let index=0;index<path.nodes.length-1;index++){
      const first=path.nodes[index],second=path.nodes[index+1];
      drawSegment(canvas,nodePoint(first,positions),nodePoint(second,positions),"#ffffff",6);
      const connection=connectionFor(first,second,baseSlice);
      edgeRows.push([
        index+1,first,second,connection?.display_kind??"terminal",
        connection?parameters(
          connection.connection_id.endpoint_a===first
            ?connection.endpoint_a_to_b:connection.endpoint_b_to_a
        ):"—"
      ]);
    }
    path.nodes.forEach((id,index)=>drawPoint(
      canvas,nodePoint(id,positions),index===0?"#5dff8b":"#ffffff",index===0?10:7
    ));
  }
  if(path.target.kind==="G"){
    drawPoint(
      canvas,nodePoint(path.target.ground_station_id,positions),
      "#ffb454",11,"G target"
    );
    layers.push("g-target");
  }else{
    const zone=zonesById[path.target.zone_id],center=zone.representative_point;
    drawZoneContext(canvas,{strong:true,zoneId:zone.zone_id});
    drawPoint(canvas,sphere(center.latitude_deg,center.longitude_deg,1.01),"#ffb454",11,"Z target");
    const assignment=displayBaseSlice().zone_service_assignments.find(
      value=>value.zone_id===zone.zone_id
    );
    if(assignment){
      const servicePoint=nodePoint(assignment.service_satellite_id,positions);
      drawPoint(canvas,servicePoint,"#e879f9",11,"service Satellite");
      drawSegment(
        canvas,servicePoint,sphere(center.latitude_deg,center.longitude_deg,1.01),
        "#e879f9",3,true
      );
    }
    layers.push("target-zone","zone-service-satellite");
  }
  layers.push("selected-path","path-nodes");
  canvas.dataset.layers=layers.join(",");
  const route=displayBaseSlice().routes.find(value=>value.route_ref===path.path_id);
  const serviceAssignment=path.target.kind==="Z"
    ?displayBaseSlice().zone_service_assignments.find(
      value=>value.zone_id===path.target.zone_id
    ):null;
  document.getElementById("route-detail").innerHTML=
    (isTransition()?'<p class="transition-note">Previous formal resolved path is red/dashed; current formal resolved path is green. Both recorded paths use the same Tool-local comparison geometry, so topology change—not Satellite motion—is emphasized. The browser does not recalculate routing.</p>':"")
    +section("Source identity",table(["field","value"],identityRows(path.source)))
    +section(isTransition()?"Source comparison Position":"Source Position",
      table(["field","value"],positionRows(path.source,positions)))
    +section("Selected target",table(["field","value"],[
      ["identity",targetIdentity(path.target)],["kind",path.target.kind],
      ["next hop",route?.next_hop??"terminal"],["hop count",path.hop_count],
      ["service Satellite",serviceAssignment?.service_satellite_id??"—"]
    ]))
    +(isTransition()?section("Formal path transition",table(
      ["endpoint","previous Slice","current Slice"],[
        ["resolved path",pair.previous?.nodes.join(" → ")??"unavailable",
          pair.current?.nodes.join(" → ")??"unavailable"],
        ["hop count",pair.previous?.hop_count??"—",pair.current?.hop_count??"—"]
      ]
    )):section("Formal resolved path",table(
      ["hop","from","to","Connection class","outbound parameters"],edgeRows
    )));
}

function differenceItems(){
  const difference=displayDifference();
  if(!difference)return [];
  const category=document.getElementById("difference-category").value;
  if(category==="connection-structure")return [
    ...difference.added_connections.map(value=>({
      kind:"added",value,previousConnection:null,currentConnection:value,
      nodes:[value.connection_id.endpoint_a,value.connection_id.endpoint_b]
    })),
    ...difference.removed_connection_ids.map(value=>({
      kind:"removed",value,
      previousConnection:displayPreviousSlice().connections.find(
        connection=>connection.connection_ref===value.connection_ref
      )??null,
      currentConnection:null,
      nodes:[value.connection_id.endpoint_a,value.connection_id.endpoint_b]
    }))
  ];
  if(category==="parameters")return difference.connection_parameter_patches.map(value=>({
    kind:"parameters",value,
    previousConnection:displayPreviousSlice().connections.find(
      connection=>connection.connection_ref===value.connection_ref
    )??null,
    currentConnection:displayCurrentSlice().connections.find(
      connection=>connection.connection_ref===value.connection_ref
    )??null,
    nodes:[value.connection_id.endpoint_a,value.connection_id.endpoint_b]
  }));
  if(category==="zone")return difference.zone_service_assignment_patches.map(value=>{
    const previousAssignment=displayPreviousSlice().zone_service_assignments.find(
      item=>item.zone_id===value.zone_id
    )?.service_satellite_id??null;
    const currentAssignment=displayCurrentSlice().zone_service_assignments.find(
      item=>item.zone_id===value.zone_id
    )?.service_satellite_id??null;
    return {
      kind:"zone",value,previousAssignment,currentAssignment,
      nodes:[previousAssignment,currentAssignment].filter(Boolean)
    };
  });
  return difference.route_patches.map(value=>{
    const previousRoute=displayPreviousSlice().routes.find(
      route=>route.route_ref===value.route_ref
    )??null;
    const currentRoute=displayCurrentSlice().routes.find(
      route=>route.route_ref===value.route_ref
    )??null;
    return {
      kind:"route",value,previousRoute,currentRoute,
      previousPath:displayPreviousSlice().paths.find(path=>path.path_id===value.route_ref)??null,
      currentPath:displayCurrentSlice().paths.find(path=>path.path_id===value.route_ref)??null,
      nodes:[...new Set([
        value.source,previousRoute?.next_hop,currentRoute?.next_hop,
        ...(displayPreviousSlice().paths.find(
          path=>path.path_id===value.route_ref
        )?.nodes??[]),
        ...(displayCurrentSlice().paths.find(
          path=>path.path_id===value.route_ref
        )?.nodes??[])
      ].filter(Boolean))]
    };
  });
}
function differenceLabel(item){
  const value=item.value;
  if(item.kind==="added")return `ADDED · ${value.connection_ref}`;
  if(item.kind==="removed")return `REMOVED · ${value.connection_ref}`;
  if(item.kind==="parameters")return `${value.direction} · ${value.connection_ref}`;
  if(item.kind==="zone")return `${value.operation.toUpperCase()} · Zone ${value.zone_id}`;
  return `${value.operation.toUpperCase()} · ${value.route_ref}`;
}
function renderDifferenceList(){
  const container=document.getElementById("difference-list"),items=differenceItems();
  selectedDifferenceIndex=Math.min(selectedDifferenceIndex,Math.max(0,items.length-1));
  container.innerHTML=section(
    "Category items",
    items.length
      ?`<div class="difference-items">${items.map((item,index)=>
        `<button class="difference-item" data-difference-index="${index}"
          aria-selected="${index===selectedDifferenceIndex}">${escapeHtml(differenceLabel(item))}</button>`
      ).join("")}</div>`
      :'<p class="empty">No patch in this category.</p>'
  );
  container.querySelectorAll(".difference-item").forEach(button=>{
    button.addEventListener("click",()=>{
      selectedDifferenceIndex=Number(button.dataset.differenceIndex);
      const item=differenceItems()[selectedDifferenceIndex];
      if(item)focusDifferenceItem(item);
      drawDifference();
    });
  });
}
function focusDifferenceItem(item){
  const positions=comparisonPositionMap();
  const values=item.nodes.map(id=>positions[id]?.geographic).filter(Boolean);
  if(item.kind==="zone"){
    values.push(zonesById[item.value.zone_id].representative_point);
  }else if(item.kind==="route"&&item.value.target.kind==="Z"){
    values.push(zonesById[item.value.target.zone_id].representative_point);
  }else if(item.kind==="route"&&item.value.target.kind==="G"){
    const target=positions[item.value.target.ground_station_id]?.geographic;
    if(target)values.push(target);
  }
  focusLocations("difference-globe",values,differenceLabel(item));
}
function directionalParameters(connection,direction){
  if(!connection)return null;
  return direction==="endpoint-a-to-b"
    ?connection.endpoint_a_to_b:connection.endpoint_b_to_a;
}
function drawFormalPath(canvas,path,positions,color,width,dashed=false){
  if(!path)return;
  for(let index=0;index<path.nodes.length-1;index++){
    drawSegment(
      canvas,nodePoint(path.nodes[index],positions),
      nodePoint(path.nodes[index+1],positions),color,width,dashed
    );
  }
}
function differenceDetail(item){
  if(!item){
    if(displayDifference()?.is_empty){
      return '<p class="empty"><strong>No discrete network-state change.</strong> The stable comparison context is retained; Position endpoint movement is not presented as Difference content.</p>';
    }
    return isTransition()
      ?'<p class="empty">No patch in this category.</p>'
      :'<p class="empty">Select a Δ marker to inspect a formal transition.</p>';
  }
  if(item.kind==="added"||item.kind==="removed"){
    const connection=item.currentConnection??item.previousConnection;
    return section("Connection transition",table(["field","previous","current"],[
      ["operation",item.kind==="added"?"absent":"present",item.kind==="added"?"present":"absent"],
      ["Connection",connection.connection_ref,connection.connection_ref],
      ["A→B",item.previousConnection?parameters(item.previousConnection.endpoint_a_to_b):"—",
        item.currentConnection?parameters(item.currentConnection.endpoint_a_to_b):"—"],
      ["B→A",item.previousConnection?parameters(item.previousConnection.endpoint_b_to_a):"—",
        item.currentConnection?parameters(item.currentConnection.endpoint_b_to_a):"—"]
    ]));
  }
  if(item.kind==="parameters"){
    const previous=directionalParameters(item.previousConnection,item.value.direction);
    const current=directionalParameters(item.currentConnection,item.value.direction);
    return section("Directional parameter transition",table(
      ["field","previous","current","changed by Difference"],
      ["bandwidth_bps","delay_us","packet_loss_ppm"].map(field=>[
        field,previous?.[field]??"—",current?.[field]??"—",
        Object.hasOwn(item.value.changed,field)?item.value.changed[field]:"unchanged"
      ])
    ))+section("Identity",table(["field","value"],[
      ["Connection",item.value.connection_ref],["direction",item.value.direction]
    ]));
  }
  if(item.kind==="zone"){
    return section("Zone service transition",table(["field","previous","current"],[
      ["Zone",item.value.zone_id,item.value.zone_id],
      ["service Satellite",item.previousAssignment??"unassigned",item.currentAssignment??"unassigned"],
      ["operation","—",item.value.operation]
    ]));
  }
  return section("Route next-hop transition",table(["field","previous","current"],[
    ["RouteKey",item.value.route_ref,item.value.route_ref],
    ["next hop",item.previousRoute?.next_hop??"removed",item.currentRoute?.next_hop??"removed"],
    ["formal resolved path",item.previousPath?.nodes.join(" → ")??"unavailable",
      item.currentPath?.nodes.join(" → ")??"unavailable"]
  ]));
}
function drawDifference(){
  const canvas=document.getElementById("difference-globe");
  const positions=comparisonPositionMap();
  drawBase(canvas);
  const category=document.getElementById("difference-category").value;
  const items=differenceItems();
  const layers=[...stableComparisonLayerNames()];
  renderDifferenceList();
  const item=items[selectedDifferenceIndex];
  if(document.getElementById("difference-orbit-layer").checked){
    const relevantOrbitRefs=item?orbitRefsForNodes(item.nodes):[];
    if(relevantOrbitRefs.length){
      relevantOrbitRefs.forEach(orbitRef=>
        drawStableOrbitContext(canvas,{strong:true,orbitRef})
      );
      layers.push("focused-relevant-orbits");
    }else if(displayDifference()?.is_empty&&drawStableOrbitContext(canvas)){
      layers.push("empty-difference-stable-orbits");
    }
  }
  if(document.getElementById("difference-satellite-layer").checked){
    drawStableSatelliteContext(canvas);
    layers.push("full-constellation-stable-context");
  }
  if(item){
    const selected=true,width=7;
    if(item.kind==="added"||item.kind==="removed"||item.kind==="parameters"){
      drawSegment(canvas,nodePoint(item.nodes[0],positions),nodePoint(item.nodes[1],positions),
        item.kind==="added"?"#5dff8b":item.kind==="removed"?"#ff6464":"#ff9f43",
        width,item.kind!=="added");
      item.nodes.forEach(id=>drawPoint(
        canvas,nodePoint(id,positions),item.kind==="removed"?"#ff8a8a":"#ffffff",
        selected?8:5
      ));
    }else if(item.kind==="zone"){
      const zone=zonesById[item.value.zone_id],center=zone.representative_point;
      const point=sphere(center.latitude_deg,center.longitude_deg,1.01);
      drawZoneContext(canvas,{strong:selected,zoneId:zone.zone_id});
      drawPoint(canvas,point,"#ffb454",selected?13:9,zone.zone_id);
      if(item.previousAssignment){
        drawSegment(
          canvas,nodePoint(item.previousAssignment,positions),point,
          "#ff6464",width,true,.9
        );
        drawPoint(canvas,nodePoint(item.previousAssignment,positions),"#ff6464",selected?9:6,"previous");
      }
      if(item.currentAssignment){
        drawSegment(
          canvas,nodePoint(item.currentAssignment,positions),point,
          "#5dff8b",width,false,.9
        );
        drawPoint(canvas,nodePoint(item.currentAssignment,positions),"#5dff8b",selected?9:6,"current");
      }
    }else{
      const source=item.value.source;
      drawPoint(canvas,nodePoint(source,positions),"#ffffff",selected?11:7,"source");
      if(item.value.target.kind==="G"){
        drawPoint(
          canvas,nodePoint(item.value.target.ground_station_id,positions),
          "#ffb454",selected?10:7,"G target"
        );
      }else{
        const zone=zonesById[item.value.target.zone_id],center=zone.representative_point;
        drawZoneContext(canvas,{strong:selected,zoneId:zone.zone_id});
        drawPoint(
          canvas,sphere(center.latitude_deg,center.longitude_deg,1.01),
          "#ffb454",selected?10:7,"Z target"
        );
      }
      if(item.previousRoute){
        drawSegment(
          canvas,nodePoint(source,positions),
          nodePoint(item.previousRoute.next_hop,positions),
          "#ff6464",width,true
        );
      }
      if(item.currentRoute){
        drawSegment(
          canvas,nodePoint(source,positions),nodePoint(item.currentRoute.next_hop,positions),
          "#5dff8b",width,false
        );
      }
      if(selected){
        drawFormalPath(canvas,item.previousPath,positions,"#ff646488",3,true);
        drawFormalPath(canvas,item.currentPath,positions,"#5dff8b99",3,false);
      }
    }
    layers.push("focused-selected-item");
  }else if(displayDifference()?.is_empty){
    layers.push("no-discrete-network-state-change");
  }
  layers.push(`difference-${category}`);
  canvas.dataset.layers=layers.join(",");
  document.getElementById("difference-detail").innerHTML=differenceDetail(item);
}

function populateStaticControls(){
  const orbitControls=[
    document.getElementById("orbit-select"),
    document.getElementById("isl-orbit-filter")
  ];
  orbitControls.forEach(select=>{
    select.append(new Option("All Orbits","*"));
    DATA.topology.orbits.forEach(orbit=>select.append(new Option(
      `AS${orbit.asn} / ${orbit.shell_id} / ${orbit.orbit_id}`,orbit.orbit_ref
    )));
    select.value=selectedOrbitRef;
  });
  const node=document.getElementById("isl-node-filter");
  node.append(new Option("All ISL nodes","*"));
  DATA.topology.nodes.filter(value=>value.kind==="satellite")
    .forEach(value=>node.append(new Option(value.node_id,value.node_id)));
}
function populateRouteSources(){
  const select=document.getElementById("route-source");
  const sources=[...new Set(displayPaths().map(path=>path.source))];
  select.replaceChildren();
  sources.forEach(value=>select.append(new Option(value,value)));
  if(sources.includes(selectedRouteSource))select.value=selectedRouteSource;
  selectedRouteSource=select.value;
  populateRouteTargets();
}
function differenceSummary(difference){
  if(!difference)return displayBaseSlice().slice_id===0
    ?"Slice 0 is the synthetic baseline; no previous Difference."
    :"Formal Slice after-state. Select an adjacent Δ marker for transition inspection.";
  if(difference.is_empty){
    return "No discrete network-state change · stable comparison geometry only";
  }
  return [
    `added Connections ${difference.added_connections.length}`,
    `removed Connections ${difference.removed_connection_ids.length}`,
    `parameter patches ${difference.connection_parameter_patches.length}`,
    `Zone patches ${difference.zone_service_assignment_patches.length}`,
    `Route patches ${difference.route_patches.length}`
  ].join(" · ");
}
function populateTimelineRail(){
  const rail=document.getElementById("timeline-rail");
  rail.replaceChildren();
  DATA.slices.forEach((slice,index)=>{
    if(index>0){
      const difference=document.createElement("button");
      difference.type="button";difference.className="timeline-marker difference";
      difference.dataset.timelineIndex=String(index*2-1);
      difference.dataset.kind="difference";
      difference.textContent=`Δ${index-1}→${index}`;
      rail.append(difference);
    }
    const marker=document.createElement("button");
    marker.type="button";marker.className="timeline-marker slice";
    marker.dataset.timelineIndex=String(index*2);
    marker.dataset.kind="slice";marker.textContent=`S${slice.slice_id}`;
    rail.append(marker);
  });
  rail.querySelectorAll(".timeline-marker").forEach(marker=>
    marker.addEventListener("click",()=>setTimelineIndex(Number(marker.dataset.timelineIndex)))
  );
}
function renderAll(){
  const baseSlice=displayBaseSlice();
  const currentSlice=displayCurrentSlice();
  range.value=String(timelineIndex);
  const label=timelineLabel();
  document.getElementById("slice-label").textContent=label;
  document.getElementById("timestamp-label").textContent=isTransition()
    ?`${displayPreviousSlice().timestamp??"none"} → ${currentSlice.timestamp}`
    :(baseSlice.timestamp??"timestamp: none (synthetic baseline)");
  document.getElementById("difference-summary").textContent=
    differenceSummary(displayDifference());
  document.querySelectorAll(".view-context").forEach(value=>value.textContent=label);
  document.querySelectorAll(".timeline-marker").forEach(marker=>
    marker.setAttribute("aria-current",String(Number(marker.dataset.timelineIndex)===timelineIndex))
  );
  document.getElementById("audit-json").textContent=JSON.stringify(
    isTransition()?{
      mode:"non-authoritative-transition-view",
      base_state:displayPreviousSlice(),
      difference:displayDifference(),
      reference_after_state:currentSlice,
      position_interpolation:false
    }:{
      mode:"formal-slice",
      slice:baseSlice
    },null,2
  );
  populateRouteSources();
  drawTopology();drawIsl();drawService();drawRouting();drawDifference();
}
function setTimelineIndex(index){
  timelineIndex=Math.max(0,Math.min(DATA.slices.length*2-2,index));
  selectedDifferenceIndex=0;renderAll();
}
function setSlice(index){
  setTimelineIndex(Math.max(0,Math.min(DATA.slices.length-1,index))*2);
}
function activateView(view){
  activeView=view;
  document.querySelectorAll(".view-tab").forEach(button=>
    button.setAttribute("aria-selected",String(button.dataset.view===view))
  );
  document.querySelectorAll(".view-panel").forEach(panel=>
    panel.hidden=panel.dataset.panel!==view
  );
  const canvas=document.querySelector(`[data-panel="${view}"] canvas`);
  if(canvas)requestAnimationFrame(()=>canvas.scrollIntoView({block:"center"}));
}

populateStaticControls();
populateTimelineRail();
document.querySelectorAll(".view-tab").forEach(button=>
  button.addEventListener("click",()=>activateView(button.dataset.view))
);
range.addEventListener("input",()=>setTimelineIndex(Number(range.value)));
document.getElementById("previous-slice").addEventListener("click",()=>setTimelineIndex(timelineIndex-1));
document.getElementById("next-slice").addEventListener("click",()=>setTimelineIndex(timelineIndex+1));
document.getElementById("play-pause").addEventListener("click",event=>{
  if(timer){clearInterval(timer);timer=null;event.target.textContent="Play";return;}
  event.target.textContent="Pause";
  timer=setInterval(()=>setTimelineIndex((timelineIndex+1)%(DATA.slices.length*2-1)),900);
});
function setOrbitFocus(value){
  selectedOrbitRef=value;
  document.getElementById("orbit-select").value=value;
  document.getElementById("isl-orbit-filter").value=value;
  drawTopology();drawIsl();
}
document.getElementById("orbit-select").addEventListener("change",event=>
  setOrbitFocus(event.target.value)
);
document.getElementById("topology-ground-layer").addEventListener("change",drawTopology);
document.getElementById("topology-zone-layer").addEventListener("change",drawTopology);
document.getElementById("isl-orbit-filter").addEventListener("change",event=>
  setOrbitFocus(event.target.value)
);
document.getElementById("isl-orbit-layer").addEventListener("change",drawIsl);
document.getElementById("isl-satellite-layer").addEventListener("change",drawIsl);
document.getElementById("isl-node-filter").addEventListener("change",event=>{
  selectedNodeId=event.target.value;
  if(selectedNodeId!=="*")focusNodes("isl-globe",[selectedNodeId]);
  drawIsl();
});
document.getElementById("service-orbit-layer").addEventListener("change",drawService);
document.getElementById("service-satellite-layer").addEventListener("change",drawService);
document.getElementById("route-source").addEventListener("change",()=>{
  selectedRouteSource=document.getElementById("route-source").value;
  populateRouteTargets();
  selectedRouteTarget=document.getElementById("route-target").value;
  const path=selectedPath();
  if(path)focusPath(path);
  drawRouting();
});
document.getElementById("route-target").addEventListener("change",()=>{
  selectedRouteTarget=document.getElementById("route-target").value;
  const path=selectedPath();
  if(path)focusPath(path);
  drawRouting();
});
document.getElementById("routing-orbit-layer").addEventListener("change",drawRouting);
document.getElementById("routing-satellite-layer").addEventListener("change",drawRouting);
document.getElementById("routing-ground-layer").addEventListener("change",drawRouting);
document.getElementById("difference-category").addEventListener("change",()=>{
  selectedDifferenceIndex=0;drawDifference();
});
document.getElementById("difference-orbit-layer").addEventListener("change",drawDifference);
document.getElementById("difference-satellite-layer").addEventListener("change",drawDifference);
document.getElementById("sync-camera").addEventListener("change",event=>{
  if(event.target.checked){
    const activeCanvas=document.querySelector(`[data-panel="${activeView}"] canvas`);
    const state=viewState[activeCanvas.id];
    sharedCamera={yaw:state.yaw,pitch:state.pitch,drag:null};
  }else{
    canvasIds.forEach(id=>{
      viewState[id]={yaw:sharedCamera.yaw,pitch:sharedCamera.pitch,drag:null};
    });
  }
  renderAll();
});
document.querySelectorAll(".reset-view").forEach(button=>button.addEventListener("click",()=>{
  const canvas=document.getElementById(button.dataset.canvas);
  const state=cameraFor(canvas);
  state.yaw=-.65;state.pitch=.28;state.drag=null;
  renderAll();
}));
document.querySelectorAll("canvas.globe").forEach(canvas=>{
  canvas.addEventListener("pointerdown",event=>{
    cameraFor(canvas).drag=[event.clientX,event.clientY];
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove",event=>{
    const state=cameraFor(canvas);
    if(!state.drag)return;
    state.yaw+=(event.clientX-state.drag[0])*.009;
    state.pitch=Math.max(-1.3,Math.min(1.3,state.pitch+(event.clientY-state.drag[1])*.009));
    state.drag=[event.clientX,event.clientY];renderAll();
  });
  canvas.addEventListener("pointerup",()=>cameraFor(canvas).drag=null);
});
renderAll();
"""


__all__ = ["render_slice_difference_page"]
