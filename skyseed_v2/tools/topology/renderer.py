"""Render the Topology Tool's non-authoritative offline UI."""

from __future__ import annotations

from html import escape
from typing import Any

from .._display_geometry import display_geometry_policy
from .._earth_reference import _natural_earth_coastline_lines
from .._html import json_for_script, standalone_page


def render_topology_page(data: dict[str, Any]) -> str:
    counts = data["counts"]
    consistency = data["consistency"]
    assert isinstance(counts, dict)
    assert isinstance(consistency, dict)
    status = str(data["status"])
    failed_checks = consistency["checks"]
    assert isinstance(failed_checks, list)
    stats = "".join(
        f'<div class="card stat"><span class="muted">{escape(label)}</span>'
        f"<strong>{counts[key]}</strong></div>"
        for key, label in (
            ("autonomous_systems", "AS"),
            ("shells", "Shell"),
            ("orbits", "Orbit"),
            ("satellites", "Satellite"),
            ("ground_stations", "GroundStation"),
            ("zones", "Zone"),
        )
    )
    diagnostics = (
        '<p class="muted">No consistency anomalies.</p>'
        if not failed_checks
        else '<table><thead><tr><th>Check</th><th>Status</th></tr></thead>'
        '<tbody id="checks"></tbody></table>'
    )
    body = f"""
<style>
.globe-controls {{ display:flex; gap:9px; align-items:center; flex-wrap:wrap; margin-bottom:10px; }}
#orbit-canvas {{ touch-action:none; cursor:grab; aspect-ratio:55/28; }}
#orbit-canvas.dragging {{ cursor:grabbing; }}
.selected-detail {{ min-height:9rem; }}
.detail-grid {{ display:grid; grid-template-columns:max-content 1fr; gap:5px 14px; margin:0; }}
.detail-grid dt {{ color:var(--muted); }}
.detail-grid dd {{ margin:0; overflow-wrap:anywhere; }}
.tool-navigation {{ margin-bottom:12px; }}
</style>
<nav class="tool-navigation"><a href="../index.html">← Tool Overview</a></nav>
<section class="card">
  <span class="status {status}">{status}</span>
  <h2>Topology materialized</h2>
  <div class="warning">The orbit view shows static orbit planes, not satellite positions at a particular time.</div>
  <p>Use this Tool for formal static inventory and identity. Orbit rings describe structure; time-varying Satellite positions, Connections, and Routes belong to the Slice Tool.</p>
</section>
<section><h2>Materialized inventory</h2><div class="grid stats">{stats}</div></section>
<section class="card selected-detail">
  <h2>Selected static object</h2>
  <div id="selected-object" data-selected-kind="" data-selected-id=""><p class="muted">Click or tap an orbit ring or GroundStation point.</p></div>
</section>
<section class="card">
  <h2>Static orbit planes and GroundStation globe reference</h2>
  <div class="globe-controls"><button id="orbit-reset" type="button">Reset view</button>
  <span class="muted">Drag to rotate · physical altitude is proportional on the unit-Earth display</span></div>
  <canvas id="orbit-canvas" width="1100" height="560"></canvas>
  <p>The Earth globe uses unit radius and the Tool-private IUGG mean Earth radius to map formal altitude proportionally. This spherical WGS84 approximation is display-only. Natural Earth coastline is an orientation reference only; no Satellite propagation is performed.</p>
</section>
<section class="card">
  <h2>Diagnostics</h2>
  {diagnostics}
</section>
"""
    script = f"""
const DATA={json_for_script(data)};
const COASTLINES={json_for_script(_natural_earth_coastline_lines())};
const DISPLAY_GEOMETRY={json_for_script(display_geometry_policy())};
const canvas=document.getElementById("orbit-canvas"),ctx=canvas.getContext("2d");
const selectedDetail=document.getElementById("selected-object");
const INITIAL_CAMERA={{yaw:-.55,pitch:.28}},MAX_PITCH=89*Math.PI/180;
let camera={{...INITIAL_CAMERA}},pointer=null,selected=null,hovered=null;
let logicalWidth=1100,logicalHeight=560,orbitHitRegions=[],groundStationHitRegions=[];
const ringKey=ring=>`${{ring.asn}}/${{ring.shell_id}}/${{ring.orbit_id}}`;
const spherePoint=(lat,lon,r=1)=>{{const a=lat*Math.PI/180,b=lon*Math.PI/180;return [r*Math.cos(a)*Math.cos(b),r*Math.sin(a),r*Math.cos(a)*Math.sin(b)];}};
const orbitPoint=(ring,angle)=>{{
  const node=angle*Math.PI/180,inclination=ring.inclination_deg*Math.PI/180,ascending=ring.right_ascension_deg*Math.PI/180,radius=1+Number(ring.altitude_km)*1000/DISPLAY_GEOMETRY.earth_reference_radius_m;
  const x=radius*(Math.cos(ascending)*Math.cos(node)-Math.sin(ascending)*Math.sin(node)*Math.cos(inclination)),east=radius*(Math.sin(ascending)*Math.cos(node)+Math.cos(ascending)*Math.sin(node)*Math.cos(inclination)),north=radius*Math.sin(node)*Math.sin(inclination);
  return [x,north,east];
}};
const rotate=point=>{{const [x,y,z]=point,cy=Math.cos(camera.yaw),sy=Math.sin(camera.yaw),cp=Math.cos(camera.pitch),sp=Math.sin(camera.pitch),x1=x*cy+z*sy,z1=-x*sy+z*cy;return [x1,y*cp-z1*sp,y*sp+z1*cp];}};
function prepareCanvas(){{
  const box=canvas.getBoundingClientRect(),dpr=Math.max(1,window.devicePixelRatio||1);logicalWidth=Math.max(1,box.width);logicalHeight=Math.max(1,box.height);
  const backingWidth=Math.max(1,Math.round(logicalWidth*dpr)),backingHeight=Math.max(1,Math.round(logicalHeight*dpr));
  if(canvas.width!==backingWidth||canvas.height!==backingHeight){{canvas.width=backingWidth;canvas.height=backingHeight;}}
  ctx.setTransform(backingWidth/logicalWidth,0,0,backingHeight/logicalHeight,0,0);
  canvas.dataset.devicePixelRatio=String(dpr);canvas.dataset.logicalWidth=String(logicalWidth);canvas.dataset.logicalHeight=String(logicalHeight);
}}
const scale=()=>Math.min(logicalWidth,logicalHeight)*.34;
const project=point=>{{const [x,y,z]=rotate(point),s=scale();return [logicalWidth/2+x*s,logicalHeight/2-y*s,z];}};
const drawPolyline=(points,color,width,front,opacity,hitTarget=null)=>{{
  ctx.save();ctx.strokeStyle=color;ctx.lineWidth=width;ctx.globalAlpha=opacity;ctx.beginPath();let previous=null,segments=0;
  for(const value of points){{const point=project(value),visible=front?point[2]>=0:point[2]<0;if(!visible){{previous=null;continue;}}if(!previous)ctx.moveTo(point[0],point[1]);else{{ctx.lineTo(point[0],point[1]);segments++;if(hitTarget)orbitHitRegions.push({{ring:hitTarget,x1:previous[0],y1:previous[1],x2:point[0],y2:point[1]}});}}previous=point;}}
  ctx.stroke();ctx.restore();return segments;
}};
const drawEarth=()=>{{
  const radius=scale(),gradient=ctx.createRadialGradient(logicalWidth*.45,logicalHeight*.42,10,logicalWidth/2,logicalHeight/2,radius);gradient.addColorStop(0,"#174a68");gradient.addColorStop(1,"#071c2d");ctx.fillStyle=gradient;ctx.beginPath();ctx.arc(logicalWidth/2,logicalHeight/2,radius,0,Math.PI*2);ctx.fill();ctx.strokeStyle="#6ca7bd33";ctx.lineWidth=.7;ctx.stroke();
}};
function drawLabel(textValue,x,y){{
  const label=String(textValue);ctx.save();ctx.font="600 12px system-ui, sans-serif";ctx.textBaseline="middle";const padding=5,height=22,width=ctx.measureText(label).width+padding*2,left=Math.max(4,Math.min(logicalWidth-width-4,x+8)),top=Math.max(4,Math.min(logicalHeight-height-4,y-height-6));
  ctx.fillStyle="#06111ee8";ctx.strokeStyle="#8be6f588";ctx.lineWidth=.7;ctx.beginPath();ctx.roundRect(left,top,width,height,4);ctx.fill();ctx.stroke();ctx.fillStyle="#dffaff";ctx.fillText(label,left+padding,top+height/2);ctx.restore();
}}
const bestOrbitLabelPoint=points=>points.map(project).filter(point=>point[2]>=0).reduce((best,point)=>!best||point[1]<best[1]?point:best,null);
const draw=()=>{{
  prepareCanvas();ctx.clearRect(0,0,logicalWidth,logicalHeight);orbitHitRegions=[];groundStationHitRegions=[];canvas.dataset.selectedLabel="";
  let orbitSegments=0,gridSegments=0,coastlineSegments=0,selectedLabelPoint=null,selectedLabelText="",hoverLabelPoint=null,hoverLabelText="";
  for(const ring of DATA.orbit_rings){{const points=Array.from({{length:181}},(_,step)=>orbitPoint(ring,step*2)),isSelected=selected?.kind==="orbit"&&selected.id===ringKey(ring);orbitSegments+=drawPolyline(points,isSelected?"#b9f4ff":"#78cfe8",isSelected?1.45:.85,false,isSelected ? .22 : .075);}}
  drawEarth();
  for(let latitude=-60;latitude<=60;latitude+=30)gridSegments+=drawPolyline(Array.from({{length:145}},(_,step)=>spherePoint(latitude,-180+step*2.5)),"#91b8c7",.48,true,.14);
  for(let longitude=-150;longitude<180;longitude+=30)gridSegments+=drawPolyline(Array.from({{length:73}},(_,step)=>spherePoint(-90+step*2.5,longitude)),"#91b8c7",.48,true,.14);
  for(const line of COASTLINES)coastlineSegments+=drawPolyline(line.map(([lat,lon])=>spherePoint(lat,lon,1.002)),"#b7c9cf",.62,true,.32);
  for(const ring of DATA.orbit_rings){{
    const points=Array.from({{length:181}},(_,step)=>orbitPoint(ring,step*2)),isSelected=selected?.kind==="orbit"&&selected.id===ringKey(ring),isHovered=hovered?.kind==="orbit"&&hovered.id===ringKey(ring);if(isSelected)drawPolyline(points,"#8be6f5",3.2,true,.13);orbitSegments+=drawPolyline(points,isSelected?"#c9f8ff":isHovered?"#a9edfa":"#78cfe8",isSelected?1.7:isHovered?1.35:.9,true,isSelected ? .94 : isHovered ? .75 : .38,ring);if(isSelected){{selectedLabelPoint=bestOrbitLabelPoint(points);selectedLabelText=ring.orbit_id;}}else if(isHovered){{hoverLabelPoint=bestOrbitLabelPoint(points);hoverLabelText=ring.orbit_id;}}
  }}
  for(const gs of DATA.ground_stations){{
    const point=project(spherePoint(gs.latitude_deg,gs.longitude_deg,1+DISPLAY_GEOMETRY.surface_marker_offset_radii));if(point[2]<0)continue;const isSelected=selected?.kind==="ground-station"&&selected.id===gs.node_id,isHovered=hovered?.kind==="ground-station"&&hovered.id===gs.node_id;
    ctx.save();ctx.fillStyle=isSelected?"#dffaff":isHovered?"#a9edfa":"#c9dce4";ctx.globalAlpha=isSelected?1:isHovered ? .92 : .76;ctx.beginPath();ctx.arc(point[0],point[1],isSelected?4:isHovered?3.3:2.25,0,Math.PI*2);ctx.fill();if(isSelected){{ctx.globalAlpha=.72;ctx.strokeStyle="#8be6f5";ctx.lineWidth=1;ctx.beginPath();ctx.arc(point[0],point[1],6.2,0,Math.PI*2);ctx.stroke();selectedLabelPoint=point;selectedLabelText=gs.ground_station_id;}}else if(isHovered){{hoverLabelPoint=point;hoverLabelText=gs.ground_station_id;}}ctx.restore();groundStationHitRegions.push({{groundStation:gs,x:point[0],y:point[1]}});
  }}
  if(selectedLabelPoint)drawLabel(selectedLabelText,selectedLabelPoint[0],selectedLabelPoint[1]);if(hoverLabelPoint)drawLabel(hoverLabelText,hoverLabelPoint[0],hoverLabelPoint[1]);canvas.dataset.selectedLabel=selectedLabelText;canvas.dataset.hoverLabel=hoverLabelText;
  canvas.dataset.cameraYaw=String(camera.yaw);canvas.dataset.cameraPitch=String(camera.pitch);canvas.dataset.orbitSegments=String(orbitSegments);canvas.dataset.gridSegments=String(gridSegments);canvas.dataset.coastlineSegments=String(coastlineSegments);canvas.dataset.earthReferenceRadiusM=String(DISPLAY_GEOMETRY.earth_reference_radius_m);canvas.dataset.firstOrbitAltitudeKm=String(Number(DATA.orbit_rings[0]?.altitude_km||0));canvas.dataset.firstOrbitDisplayRadius=String(1+Number(DATA.orbit_rings[0]?.altitude_km||0)*1000/DISPLAY_GEOMETRY.earth_reference_radius_m);canvas.dataset.selectedKind=selected?.kind||"";canvas.dataset.selectedId=selected?.id||"";
  const firstStation=groundStationHitRegions[0],firstOrbit=orbitHitRegions.find(segment=>{{const x=(segment.x1+segment.x2)/2,y=(segment.y1+segment.y2)/2;return groundStationHitRegions.every(station=>Math.hypot(x-station.x,y-station.y)>12);}})||orbitHitRegions[0];canvas.dataset.firstGroundStationX=firstStation?String(firstStation.x):"";canvas.dataset.firstGroundStationY=firstStation?String(firstStation.y):"";canvas.dataset.firstOrbitX=firstOrbit?String((firstOrbit.x1+firstOrbit.x2)/2):"";canvas.dataset.firstOrbitY=firstOrbit?String((firstOrbit.y1+firstOrbit.y2)/2):"";
}};
function detailRow(label,value){{const term=document.createElement("dt"),description=document.createElement("dd");term.textContent=label;description.textContent=String(value);return [term,description];}}
function showSelection(kind,value){{
  selectedDetail.replaceChildren();selectedDetail.dataset.selectedKind=kind;const list=document.createElement("dl");list.className="detail-grid";
  if(kind==="orbit"){{selected={{kind,id:ringKey(value)}};selectedDetail.dataset.selectedId=selected.id;const accessCaps=value.satellites.map(satellite=>satellite.max_simultaneous_ground_station_accesses),specifiedCaps=[...new Set(accessCaps.filter(capability=>capability!==null))].sort((a,b)=>a-b),accessSummary=specifiedCaps.length?specifiedCaps.join(", "):"unspecified";for(const [label,item] of [["orbit_id",value.orbit_id],["shell_id",value.shell_id],["ASN",value.asn],["altitude",`${{value.altitude_km}} km`],["inclination",`${{value.inclination_deg}}°`],["right ascension",`${{value.right_ascension_deg}}°`],["satellite_count",value.satellite_count],["simultaneous GroundStation access capabilities",accessSummary]])list.append(...detailRow(label,item));}}
  else{{selected={{kind,id:value.node_id}};selectedDetail.dataset.selectedId=selected.id;const rows=[["ground_station_id",value.ground_station_id],["node_id",value.node_id],["ASN",value.asn],["latitude / longitude",`${{value.latitude_deg}}° / ${{value.longitude_deg}}°`],["elevation",`${{value.elevation_m}} m`],["station_type",value.station_type],["simultaneous Satellite access capability",value.max_simultaneous_satellite_accesses??"unspecified"]];if(String(value.operator||"").trim())rows.push(["operator",value.operator]);for(const [label,item] of rows)list.append(...detailRow(label,item));}}
  selectedDetail.append(list);draw();
}}
const pointSegmentDistance=(px,py,segment)=>{{const dx=segment.x2-segment.x1,dy=segment.y2-segment.y1,lengthSquared=dx*dx+dy*dy,amount=lengthSquared===0?0:Math.max(0,Math.min(1,((px-segment.x1)*dx+(py-segment.y1)*dy)/lengthSquared));return Math.hypot(px-(segment.x1+amount*dx),py-(segment.y1+amount*dy));}};
function hitAtCanvas(clientX,clientY){{const box=canvas.getBoundingClientRect(),x=clientX-box.left,y=clientY-box.top;let station=null,stationDistance=Infinity;for(const candidate of groundStationHitRegions){{const distance=Math.hypot(x-candidate.x,y-candidate.y);if(distance<stationDistance){{station=candidate;stationDistance=distance;}}}}if(station&&stationDistance<=9)return {{kind:"ground-station",id:station.groundStation.node_id,value:station.groundStation}};let orbit=null,orbitDistance=Infinity;for(const candidate of orbitHitRegions){{const distance=pointSegmentDistance(x,y,candidate);if(distance<orbitDistance){{orbit=candidate;orbitDistance=distance;}}}}return orbit&&orbitDistance<=8?{{kind:"orbit",id:ringKey(orbit.ring),value:orbit.ring}}:null;}}
function clearSelection(){{selected=null;hovered=null;selectedDetail.dataset.selectedKind="";selectedDetail.dataset.selectedId="";selectedDetail.innerHTML='<p class="muted">Click or tap an orbit ring or GroundStation point.</p>';draw();}}
function selectAtCanvas(clientX,clientY){{const hit=hitAtCanvas(clientX,clientY);if(!hit){{clearSelection();return;}}if(selected?.kind===hit.kind&&selected.id===hit.id){{clearSelection();return;}}hovered=null;showSelection(hit.kind,hit.value);}}
function hoverAtCanvas(clientX,clientY){{const hit=hitAtCanvas(clientX,clientY),next=hit?{{kind:hit.kind,id:hit.id}}:null;if(next?.kind===hovered?.kind&&next?.id===hovered?.id)return;hovered=next;draw();}}
document.getElementById("orbit-reset").onclick=()=>{{camera={{...INITIAL_CAMERA}};draw();}};
canvas.addEventListener("pointerdown",event=>{{pointer={{startX:event.clientX,startY:event.clientY,lastX:event.clientX,lastY:event.clientY,moved:false}};canvas.classList.add("dragging");canvas.setPointerCapture(event.pointerId);}});
canvas.addEventListener("pointermove",event=>{{if(!pointer){{hoverAtCanvas(event.clientX,event.clientY);return;}}const total=Math.hypot(event.clientX-pointer.startX,event.clientY-pointer.startY);if(total>5)pointer.moved=true;if(pointer.moved){{hovered=null;camera.yaw+=(event.clientX-pointer.lastX)*.008;camera.pitch=Math.max(-MAX_PITCH,Math.min(MAX_PITCH,camera.pitch+(event.clientY-pointer.lastY)*.008));draw();}}pointer.lastX=event.clientX;pointer.lastY=event.clientY;}});
canvas.addEventListener("pointerup",event=>{{const wasClick=pointer&&!pointer.moved;pointer=null;canvas.classList.remove("dragging");if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);if(wasClick)selectAtCanvas(event.clientX,event.clientY);}});
canvas.addEventListener("pointerleave",()=>{{if(!pointer){{hovered=null;draw();}}}});canvas.addEventListener("pointercancel",()=>{{pointer=null;canvas.classList.remove("dragging");}});document.addEventListener("keydown",event=>{{if(event.key==="Escape")clearSelection();}});window.addEventListener("resize",draw);draw();
    const checks=document.getElementById("checks");if(checks)DATA.consistency.checks.forEach(check=>{{const row=document.createElement("tr"),label=document.createElement("td"),value=document.createElement("td");label.textContent=check.label;value.textContent=check.status;row.append(label,value);checks.append(row);}});
"""
    return standalone_page(
        title="Topology Inspection",
        eyebrow="Topology inspection",
        body=body,
        script=script,
        language_strings={
            "Topology Inspection": "拓扑检查",
            "Topology inspection": "拓扑检查",
            "Tool Overview": "工具总览",
            "Topology materialized": "拓扑已物化",
            "The orbit view shows static orbit planes, not satellite positions at a particular time.": "轨道视图显示静态轨道平面，不表示任一时刻的卫星位置。",
            "Use this Tool for formal static inventory and identity. Orbit rings describe structure; time-varying Satellite positions, Connections, and Routes belong to the Slice Tool.": "本 Tool 用于检查正式静态 inventory 和 identity。轨道环描述结构；随时间变化的 Satellite 位置、Connection 和 Route 应在 Slice Tool 中查看。",
            "Materialized inventory": "物化对象数量",
            "Selected static object": "已选静态对象",
            "Click or tap an orbit ring or GroundStation point.": "点击轨道环或 GroundStation 点。",
            "Static orbit planes and GroundStation globe reference": "静态轨道平面与 GroundStation 球面参考",
            "Drag to rotate · physical altitude is proportional on the unit-Earth display": "拖动旋转 · 物理高度在单位 Earth 显示中保持比例",
            "The Earth globe uses unit radius and the Tool-private IUGG mean Earth radius to map formal altitude proportionally. This spherical WGS84 approximation is display-only. Natural Earth coastline is an orientation reference only; no Satellite propagation is performed.": "Earth 球体采用单位半径，并以 Tool 私有的 IUGG 平均 Earth 半径按比例映射正式高度。该球形 WGS84 近似仅用于显示。Natural Earth 海岸线仅作方位参考；不执行 Satellite propagation。",
            "Diagnostics": "诊断",
            "No consistency anomalies.": "无一致性异常。",
            "Check": "检查项",
            "Status": "状态",
        },
    )


__all__ = ["render_topology_page"]
