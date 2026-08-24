"""Render the Zone Tool's non-authoritative offline UI."""

from __future__ import annotations

from html import escape
from typing import Any

from .._html import json_for_script, standalone_page


def render_zone_page(data: dict[str, Any]) -> str:
    counts = data["counts"]
    assert isinstance(counts, dict)
    status = str(data["status"])
    policy = (
        "Default policy: maximum priority, then representative-center distance, "
        "then zone_id."
        if data["model_is_default"]
        else "Custom membership model: only its authoritative result is shown."
    )
    policy_chinese = (
        "默认策略：最高优先级，其次代表点中心距离，最后按 zone_id。"
        if data["model_is_default"]
        else "自定义 membership model：页面只展示其 authoritative result。"
    )
    zones = "".join(
        f'<option value="{escape(str(zone["zone_id"]))}">'
        f'{escape(str(zone["zone_id"]))}</option>'
        for zone in data["zones"]
    )
    body = f"""
<style>
.zone-visuals {{ display:grid; grid-template-columns:minmax(0,1.4fr) minmax(320px,1fr); gap:14px; }}
.zone-visuals h3 {{ margin-top:8px; }}
#zone-globe {{ touch-action:none; cursor:grab; }}
#zone-globe.dragging {{ cursor:grabbing; }}
#zone-map {{ min-height:360px; }}
.zone-boundary {{ fill:none; stroke-width:2; vector-effect:non-scaling-stroke; }}
.zone-probe {{ stroke:#07111f; stroke-width:1; vector-effect:non-scaling-stroke; }}
.zone-station {{ fill:#ffd166; stroke:#07111f; stroke-width:1.5; vector-effect:non-scaling-stroke; }}
@media(max-width:900px) {{ .zone-visuals {{ grid-template-columns:1fr; }} }}
</style>
<section class="card">
  <span class="status {status}">{status}</span>
  <h2>Formal Zone observation</h2>
  <p><span>Model</span>: {escape(str(data['model_type']))}. <span>{escape(policy)}</span></p>
  <p>physical matches and authoritative resolution are read from Topology; the browser does not recompute them.</p>
</section>
<section class="card">
  <div class="controls">
    <select id="zone-filter"><option value="*">All Zones</option>{zones}</select>
    <select id="category-filter"><option value="*">All categories</option></select>
    <select id="status-filter">
      <option value="focus">Focus</option><option value="all">All</option>
      <option value="anomalies">Anomalies</option><option value="multi">Multiple matches</option>
    </select>
    <button id="zone-reset" type="button">Reset view</button>
  </div>
  <p><span id="probe-visible">0</span> / <span id="probe-total">{counts['probes']}</span>
  · <span id="mode-label">focus</span></p>
  <div class="zone-visuals">
    <div><h3>Rotatable Zone globe</h3><canvas id="zone-globe" width="1000" height="520"></canvas></div>
    <div><h3>Longitude / latitude reference map</h3><svg id="zone-map" viewBox="0 0 1000 500" role="img"></svg></div>
  </div>
</section>
<section class="card">
  <table><thead><tr><th>Probe</th><th>Physical matches</th><th>Authoritative result</th></tr></thead><tbody id="probe-table"></tbody></table>
  <p>The globe uses unit radius. Rectangle boundaries are display_boundary samples, not claimed great-circle arcs. UI changes do not call formal APIs again.</p>
</section>
"""
    script = f"""
const DATA={json_for_script(data)};
const canonicalLongitude=value=>((value+180)%360+360)%360-180;
const spherePoint=(lat,lon,r=1)=>{{
  const a=lat*Math.PI/180,b=lon*Math.PI/180;
  return [r*Math.cos(a)*Math.cos(b),r*Math.sin(a),r*Math.cos(a)*Math.sin(b)];
}};
const canvas=document.getElementById("zone-globe"),ctx=canvas.getContext("2d");
const map=document.getElementById("zone-map"),table=document.getElementById("probe-table");
const zoneFilter=document.getElementById("zone-filter"),categoryFilter=document.getElementById("category-filter"),statusFilter=document.getElementById("status-filter");
const INITIAL_CAMERA={{yaw:-.55,pitch:.24}},MAX_PITCH=89*Math.PI/180;
let camera={{...INITIAL_CAMERA,drag:null}};
const colors=["#72f1e1","#ffd166","#e879f9","#7cb8ff","#9be564","#ff8f70","#b8a1ff"];
const colorByZone=Object.fromEntries(DATA.zones.map((zone,index)=>[zone.zone_id,colors[index%colors.length]]));
const rotate=point=>{{const [x,y,z]=point,cy=Math.cos(camera.yaw),sy=Math.sin(camera.yaw),cp=Math.cos(camera.pitch),sp=Math.sin(camera.pitch),x1=x*cy+z*sy,z1=-x*sy+z*cy;return [x1,y*cp-z1*sp,y*sp+z1*cp];}};
const globeScale=()=>Math.min(canvas.width,canvas.height)*.37;
const project=point=>{{const [x,y,z]=rotate(point),scale=globeScale();return [canvas.width/2+x*scale,canvas.height/2-y*scale,z];}};
const probeZones=probe=>new Set([...(probe.source_zone_ids||[]),...probe.physical_matches.map(value=>value.zone_id),...(probe.authoritative_result?[probe.authoritative_result.zone_id]:[])]);
const matchesStatus=(probe,mode)=>mode==="all"||(mode==="focus"&&probe.focus_visible)||(mode==="anomalies"&&(probe.invariant_status!=="PASS"||probe.failure_reasons.length>0))||(mode==="multi"&&probe.physical_matches.length>1);
const visibleProbes=()=>DATA.probes.filter(probe=>(zoneFilter.value==="*"||probeZones(probe).has(zoneFilter.value))&&(categoryFilter.value==="*"||probe.categories.includes(categoryFilter.value))&&matchesStatus(probe,statusFilter.value));
const visibleZones=()=>DATA.zones.filter(zone=>zoneFilter.value==="*"||zone.zone_id===zoneFilter.value);
const drawPolyline=(values,color,width,front,opacity)=>{{ctx.save();ctx.strokeStyle=color;ctx.lineWidth=width;ctx.globalAlpha=opacity;ctx.beginPath();let open=false,segments=0;for(const value of values){{const point=project(value),visible=front?point[2]>=0:point[2]<0;if(!visible){{open=false;continue;}}if(!open){{ctx.moveTo(point[0],point[1]);open=true;}}else{{ctx.lineTo(point[0],point[1]);segments++;}}}}ctx.stroke();ctx.restore();return segments;}};
const drawEarth=()=>{{const radius=globeScale(),gradient=ctx.createRadialGradient(canvas.width*.45,canvas.height*.42,10,canvas.width/2,canvas.height/2,radius);gradient.addColorStop(0,"#27739d");gradient.addColorStop(1,"#08233a");ctx.fillStyle=gradient;ctx.beginPath();ctx.arc(canvas.width/2,canvas.height/2,radius,0,Math.PI*2);ctx.fill();}};
function drawGlobe(probes){{
  ctx.clearRect(0,0,canvas.width,canvas.height);let boundarySegments=0,probePoints=0;
  for(const zone of visibleZones())boundarySegments+=drawPolyline(zone.display_boundary.map(([lat,lon])=>spherePoint(lat,lon,1.008)),colorByZone[zone.zone_id],1,false,.2);
  drawEarth();
  for(let latitude=-60;latitude<=60;latitude+=30)drawPolyline(Array.from({{length:145}},(_,step)=>spherePoint(latitude,-180+step*2.5)),"#9cd8ed55",1,true,1);
  for(let longitude=-150;longitude<180;longitude+=30)drawPolyline(Array.from({{length:73}},(_,step)=>spherePoint(-90+step*2.5,longitude)),"#9cd8ed55",1,true,1);
  for(const zone of visibleZones())boundarySegments+=drawPolyline(zone.display_boundary.map(([lat,lon])=>spherePoint(lat,lon,1.008)),colorByZone[zone.zone_id],3,true,.95);
  for(const station of DATA.ground_stations){{const point=project(spherePoint(station.latitude_deg,station.longitude_deg,1.014));if(point[2]<0)continue;ctx.fillStyle="#ffd166";ctx.beginPath();ctx.arc(point[0],point[1],5,0,Math.PI*2);ctx.fill();}}
  for(const probe of probes){{const point=project(spherePoint(probe.latitude_deg,probe.longitude_deg,1.018));if(point[2]<0)continue;ctx.fillStyle=probe.physical_matches.length>1?"#e879f9":"#ffffff";ctx.beginPath();ctx.arc(point[0],point[1],probe.categories.includes("representative-center")?5:3,0,Math.PI*2);ctx.fill();probePoints++;}}
  canvas.dataset.cameraYaw=String(camera.yaw);canvas.dataset.cameraPitch=String(camera.pitch);canvas.dataset.boundarySegments=String(boundarySegments);canvas.dataset.visibleProbes=String(probePoints);
}}
const mapPoint=(lat,lon)=>[(canonicalLongitude(lon)+180)/360*1000,(90-lat)/180*500];
function addSvg(name,attributes,parent=map){{const value=document.createElementNS(map.namespaceURI,name);for(const [key,item] of Object.entries(attributes))value.setAttribute(key,String(item));parent.append(value);return value;}}
function boundaryPaths(boundary){{const result=[];let current=[];for(const [lat,lon] of boundary){{const point=mapPoint(lat,lon);if(current.length&&Math.abs(point[0]-current.at(-1)[0])>500){{result.push(current);current=[];}}current.push(point);}}if(current.length)result.push(current);return result;}}
function drawMap(probes){{
  map.replaceChildren();
  for(let lon=-180;lon<=180;lon+=30)addSvg("line",{{x1:(lon+180)/360*1000,y1:0,x2:(lon+180)/360*1000,y2:500,stroke:"#355672","stroke-width":1}});
  for(let lat=-60;lat<=60;lat+=30)addSvg("line",{{x1:0,y1:(90-lat)/180*500,x2:1000,y2:(90-lat)/180*500,stroke:"#355672","stroke-width":1}});
  for(const zone of visibleZones())for(const part of boundaryPaths(zone.display_boundary))addSvg("polyline",{{points:part.map(value=>value.join(",")).join(" "),stroke:colorByZone[zone.zone_id],class:"zone-boundary","data-zone":zone.zone_id}});
  for(const station of DATA.ground_stations){{const [cx,cy]=mapPoint(station.latitude_deg,station.longitude_deg);addSvg("circle",{{cx,cy,r:5,class:"zone-station",title:station.ground_station_id}});}}
  for(const probe of probes){{const [cx,cy]=mapPoint(probe.latitude_deg,probe.longitude_deg);addSvg("circle",{{cx,cy,r:probe.categories.includes("representative-center")?5:3,fill:probe.physical_matches.length>1?"#e879f9":"#edf6ff",class:"zone-probe","data-probe":probe.probe_id}});}}
  map.dataset.boundaryPaths=String(map.querySelectorAll(".zone-boundary").length);map.dataset.visibleProbes=String(map.querySelectorAll(".zone-probe").length);
}}
function renderTable(probes){{table.innerHTML=probes.map(probe=>`<tr data-probe="${{probe.probe_id}}"><td>${{probe.probe_id}}<br><span class="muted">${{probe.categories.join(", ")}}</span></td><td>${{probe.physical_matches.map(value=>value.zone_id).join(", ")||"none"}}</td><td>${{probe.authoritative_result?probe.authoritative_result.zone_id:"none"}}</td></tr>`).join("");}}
function render(){{const probes=visibleProbes();document.getElementById("probe-visible").textContent=String(probes.length);document.getElementById("mode-label").textContent=statusFilter.value;renderTable(probes);drawMap(probes);drawGlobe(probes);}}
const categories=[...new Set(DATA.probes.flatMap(probe=>probe.categories))].sort();categoryFilter.innerHTML='<option value="*">All categories</option>'+categories.map(value=>`<option value="${{value}}">${{value}}</option>`).join("");
for(const control of [zoneFilter,categoryFilter,statusFilter])control.addEventListener("change",render);
document.getElementById("zone-reset").addEventListener("click",()=>{{camera={{...INITIAL_CAMERA,drag:null}};render();}});
canvas.addEventListener("pointerdown",event=>{{camera.drag=[event.clientX,event.clientY];canvas.classList.add("dragging");canvas.setPointerCapture(event.pointerId);}});
canvas.addEventListener("pointermove",event=>{{if(!camera.drag)return;camera.yaw+=(event.clientX-camera.drag[0])*.008;camera.pitch=Math.max(-MAX_PITCH,Math.min(MAX_PITCH,camera.pitch+(event.clientY-camera.drag[1])*.008));camera.drag=[event.clientX,event.clientY];drawGlobe(visibleProbes());}});
canvas.addEventListener("pointerup",event=>{{camera.drag=null;canvas.classList.remove("dragging");if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);}});
canvas.addEventListener("pointercancel",()=>{{camera.drag=null;canvas.classList.remove("dragging");}});
render();
"""
    return standalone_page(
        title="Zone Inspection",
        eyebrow="Zone observation",
        body=body,
        script=script,
        language_strings={
            "Zone Inspection": "Zone 检查",
            "Zone observation": "Zone 观测",
            "Formal Zone observation": "正式 Zone 观测",
            "Model": "模型",
            policy: policy_chinese,
            "physical matches and authoritative resolution are read from Topology; the browser does not recompute them.": "physical matches 与 authoritative resolution 均读取自 Topology；浏览器不会重新计算。",
            "All Zones": "全部 Zone",
            "All categories": "全部类别",
            "Rotatable Zone globe": "可旋转 Zone 球面",
            "Longitude / latitude reference map": "经纬度参考图",
            "Focus": "聚焦",
            "All": "全部",
            "Anomalies": "异常",
            "Multiple matches": "多重匹配",
            "focus": "聚焦",
            "Probe": "探针",
            "Physical matches": "Physical match",
            "Authoritative result": "Authoritative result",
            "The globe uses unit radius. Rectangle boundaries are display_boundary samples, not claimed great-circle arcs. UI changes do not call formal APIs again.": "球体采用单位半径；矩形边界只是 display_boundary 采样，不声称是大圆线。切换 UI 不重新调用正式 API。",
        },
    )


__all__ = ["render_zone_page"]
