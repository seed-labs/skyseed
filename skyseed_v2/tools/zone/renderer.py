"""Render the Zone Tool's non-authoritative offline UI."""

from __future__ import annotations

from html import escape
from typing import Any

from .._earth_reference import _natural_earth_coastline_lines
from .._html import json_for_script, standalone_page


def render_zone_page(data: dict[str, Any]) -> str:
    counts = data["counts"]
    assert isinstance(counts, dict)
    status = str(data["status"])
    body = f"""
<style>
.zone-visuals {{ display:grid; grid-template-columns:minmax(0,1.4fr) minmax(320px,1fr); gap:14px; }}
.zone-visuals h3 {{ margin-top:8px; }}
#zone-globe {{ touch-action:none; cursor:grab; aspect-ratio:25/13; }}
#zone-globe.dragging {{ cursor:grabbing; }}
#zone-map {{ min-height:360px; }}
.zone-boundary {{ fill:none; stroke:#76c9dc; stroke-width:.65; stroke-opacity:.34; vector-effect:non-scaling-stroke; pointer-events:none; }}
.zone-area {{ fill:#65c5dc; fill-opacity:.018; stroke:none; cursor:pointer; }}
.zone-area:hover {{ fill-opacity:.09; }}
.zone-area.selected {{ fill-opacity:.12; }}
.zone-boundary.selected {{ stroke:#b9f4ff; stroke-width:1.45; stroke-opacity:.96; }}
.zone-map-label {{ fill:#e4fbff; font:600 12px system-ui,sans-serif; paint-order:stroke; stroke:#06111e; stroke-width:3; stroke-linejoin:round; pointer-events:none; }}
.selected-detail {{ min-height:9rem; }}
.detail-grid {{ display:grid; grid-template-columns:max-content 1fr; gap:5px 14px; margin:0; }}
.detail-grid dt {{ color:var(--muted); }}
.detail-grid dd {{ margin:0; overflow-wrap:anywhere; }}
.tool-navigation {{ margin-bottom:12px; }}
@media(max-width:900px) {{ .zone-visuals {{ grid-template-columns:1fr; }} }}
</style>
<nav class="tool-navigation"><a href="../index.html">← Tool Overview</a></nav>
<section class="card">
  <span class="status {status}">{status}</span>
  <h2>Formal static Zone layout</h2>
  <p><strong>{counts['zones']}</strong> Zones · allow_overlap={escape(str(data['allow_overlap']))}</p>
  <p>Geometry is read from PhysicalWorld. Selection and highlighting are display-only and do not resolve Zone membership.</p>
  <p>A Zone is a formal geographic/service identity. Its representative point and boundary are inspection aids; they are not a network node or a Satellite-to-Zone link.</p>
</section>
<section class="card selected-detail">
  <h2>Selected Zone</h2>
  <div id="selected-zone" data-selected-zone=""><p class="muted">Click or tap a Zone on the globe or map.</p></div>
</section>
<section class="card">
  <div class="controls"><button id="zone-reset" type="button">Reset view</button></div>
  <div class="zone-visuals">
    <div><h3>Rotatable Zone globe</h3><canvas id="zone-globe" width="1000" height="520"></canvas></div>
    <div><h3>Longitude / latitude reference map</h3><svg id="zone-map" viewBox="0 0 1000 500" role="img"></svg></div>
  </div>
  <p>The globe uses unit radius. Natural Earth coastline is a Tool-private orientation reference only. Rectangle boundaries are display_boundary samples, not claimed great-circle arcs. Browser hit testing is UI navigation only.</p>
</section>
"""
    script = f"""
const DATA={json_for_script(data)};
const COASTLINES={json_for_script(_natural_earth_coastline_lines())};
const canonicalLongitude=value=>((value+180)%360+360)%360-180;
const spherePoint=(lat,lon,r=1)=>{{const a=lat*Math.PI/180,b=lon*Math.PI/180;return [r*Math.cos(a)*Math.cos(b),r*Math.sin(a),r*Math.cos(a)*Math.sin(b)];}};
const canvas=document.getElementById("zone-globe"),ctx=canvas.getContext("2d"),map=document.getElementById("zone-map"),selectedDetail=document.getElementById("selected-zone");
const INITIAL_CAMERA={{yaw:-.55,pitch:.24}},MAX_PITCH=89*Math.PI/180;
let camera={{...INITIAL_CAMERA}},pointer=null,selectedZoneId=null,hoveredZoneId=null,hoverPoint=null,logicalWidth=1000,logicalHeight=520;
const rotate=point=>{{const [x,y,z]=point,cy=Math.cos(camera.yaw),sy=Math.sin(camera.yaw),cp=Math.cos(camera.pitch),sp=Math.sin(camera.pitch),x1=x*cy+z*sy,z1=-x*sy+z*cy;return [x1,y*cp-z1*sp,y*sp+z1*cp];}};
function prepareCanvas(){{
  const box=canvas.getBoundingClientRect(),dpr=Math.max(1,window.devicePixelRatio||1);logicalWidth=Math.max(1,box.width);logicalHeight=Math.max(1,box.height);
  const backingWidth=Math.max(1,Math.round(logicalWidth*dpr)),backingHeight=Math.max(1,Math.round(logicalHeight*dpr));if(canvas.width!==backingWidth||canvas.height!==backingHeight){{canvas.width=backingWidth;canvas.height=backingHeight;}}
  ctx.setTransform(backingWidth/logicalWidth,0,0,backingHeight/logicalHeight,0,0);canvas.dataset.devicePixelRatio=String(dpr);canvas.dataset.logicalWidth=String(logicalWidth);canvas.dataset.logicalHeight=String(logicalHeight);
}}
const globeScale=()=>Math.min(logicalWidth,logicalHeight)*.37;
const project=point=>{{const [x,y,z]=rotate(point),scale=globeScale();return [logicalWidth/2+x*scale,logicalHeight/2-y*scale,z];}};
const drawPolyline=(values,color,width,front,opacity)=>{{ctx.save();ctx.strokeStyle=color;ctx.lineWidth=width;ctx.globalAlpha=opacity;ctx.beginPath();let previous=null,segments=0;for(const value of values){{const point=project(value),visible=front?point[2]>=0:point[2]<0;if(!visible){{previous=null;continue;}}if(!previous)ctx.moveTo(point[0],point[1]);else{{ctx.lineTo(point[0],point[1]);segments++;}}previous=point;}}ctx.stroke();ctx.restore();return segments;}};
const drawEarth=()=>{{const radius=globeScale(),gradient=ctx.createRadialGradient(logicalWidth*.45,logicalHeight*.42,10,logicalWidth/2,logicalHeight/2,radius);gradient.addColorStop(0,"#174a68");gradient.addColorStop(1,"#071c2d");ctx.fillStyle=gradient;ctx.beginPath();ctx.arc(logicalWidth/2,logicalHeight/2,radius,0,Math.PI*2);ctx.fill();ctx.strokeStyle="#6ca7bd33";ctx.lineWidth=.7;ctx.stroke();}};
function drawLabel(textValue,x,y){{const label=String(textValue);ctx.save();ctx.font="600 12px system-ui, sans-serif";ctx.textBaseline="middle";const padding=5,height=22,width=ctx.measureText(label).width+padding*2,left=Math.max(4,Math.min(logicalWidth-width-4,x+8)),top=Math.max(4,Math.min(logicalHeight-height-4,y-height-6));ctx.fillStyle="#06111ee8";ctx.strokeStyle="#8be6f588";ctx.lineWidth=.7;ctx.beginPath();ctx.roundRect(left,top,width,height,4);ctx.fill();ctx.stroke();ctx.fillStyle="#dffaff";ctx.fillText(label,left+padding,top+height/2);ctx.restore();}}
function drawGlobe(){{
  prepareCanvas();ctx.clearRect(0,0,logicalWidth,logicalHeight);let boundarySegments=0,coastlineSegments=0;
  for(const zone of DATA.zones){{const selected=zone.zone_id===selectedZoneId,hovered=zone.zone_id===hoveredZoneId;boundarySegments+=drawPolyline(zone.display_boundary.map(([lat,lon])=>spherePoint(lat,lon,1.006)),selected?"#b9f4ff":hovered?"#9cecff":"#76c9dc",selected?1.25:hovered?1:.55,false,selected ? .14 : hovered ? .1 : .045);}}
  drawEarth();
  for(let latitude=-60;latitude<=60;latitude+=30)drawPolyline(Array.from({{length:145}},(_,step)=>spherePoint(latitude,-180+step*2.5)),"#91b8c7",.45,true,.12);
  for(let longitude=-150;longitude<180;longitude+=30)drawPolyline(Array.from({{length:73}},(_,step)=>spherePoint(-90+step*2.5,longitude)),"#91b8c7",.45,true,.12);
  for(const line of COASTLINES)coastlineSegments+=drawPolyline(line.map(([lat,lon])=>spherePoint(lat,lon,1.002)),"#b7c9cf",.58,true,.27);
  for(const zone of DATA.zones){{const points=zone.display_boundary.map(([lat,lon])=>spherePoint(lat,lon,1.006)),selected=zone.zone_id===selectedZoneId,hovered=zone.zone_id===hoveredZoneId;if(selected)drawPolyline(points,"#8be6f5",2.7,true,.11);boundarySegments+=drawPolyline(points,selected?"#b9f4ff":hovered?"#9cecff":"#76c9dc",selected?1.45:hovered?1.15:.65,true,selected ? .96 : hovered ? .72 : .34);}}
  const selectedZone=DATA.zones.find(zone=>zone.zone_id===selectedZoneId);let selectedZonePoint=null;if(selectedZone){{const center=project(spherePoint(selectedZone.geometry.representative_center[0],selectedZone.geometry.representative_center[1],1.012));if(center[2]>=0){{selectedZonePoint=center;drawLabel(selectedZone.zone_id,center[0],center[1]);}}}}
  const hoveredZone=DATA.zones.find(zone=>zone.zone_id===hoveredZoneId);if(!selectedZone&&hoveredZone&&hoverPoint)drawLabel(hoveredZone.zone_id,hoverPoint.x,hoverPoint.y);canvas.dataset.selectedLabel=selectedZone?.zone_id||"";canvas.dataset.hoverLabel=!selectedZone&&hoveredZone?hoveredZone.zone_id:"";
  canvas.dataset.cameraYaw=String(camera.yaw);canvas.dataset.cameraPitch=String(camera.pitch);canvas.dataset.boundarySegments=String(boundarySegments);canvas.dataset.coastlineSegments=String(coastlineSegments);canvas.dataset.selectedZone=selectedZoneId||"";canvas.dataset.selectedZoneX=selectedZonePoint?String(selectedZonePoint[0]):"";canvas.dataset.selectedZoneY=selectedZonePoint?String(selectedZonePoint[1]):"";
  const target=DATA.zones.map(zone=>{{const point=project(spherePoint(zone.geometry.representative_center[0],zone.geometry.representative_center[1]));return {{zone,point}};}}).find(value=>value.point[2]>=0);canvas.dataset.firstZoneX=target?String(target.point[0]):"";canvas.dataset.firstZoneY=target?String(target.point[1]):"";
}}
const mapLongitude=lon=>lon===180 ? 1000 : (canonicalLongitude(lon)+180)/360*1000;
const mapPoint=(lat,lon)=>[mapLongitude(lon),(90-lat)/180*500];
function addSvg(name,attributes,parent=map){{const value=document.createElementNS(map.namespaceURI,name);for(const [key,item] of Object.entries(attributes))value.setAttribute(key,String(item));parent.append(value);return value;}}
function boundaryPaths(boundary){{const result=[];let current=[];for(const [lat,lon] of boundary){{const point=mapPoint(lat,lon);if(current.length&&Math.abs(point[0]-current.at(-1)[0])>500){{result.push(current);current=[];}}current.push(point);}}if(current.length)result.push(current);return result;}}
const longitudeSegments=geometry=>geometry.wrapped?[[geometry.west_longitude_deg,180],[-180,geometry.east_longitude_deg]]:[[geometry.west_longitude_deg,geometry.east_longitude_deg]];
function drawMap(){{
  map.replaceChildren();for(let lon=-180;lon<=180;lon+=30)addSvg("line",{{x1:(lon+180)/360*1000,y1:0,x2:(lon+180)/360*1000,y2:500,stroke:"#355672","stroke-width":.6,"stroke-opacity":.55}});for(let lat=-60;lat<=60;lat+=30)addSvg("line",{{x1:0,y1:(90-lat)/180*500,x2:1000,y2:(90-lat)/180*500,stroke:"#355672","stroke-width":.6,"stroke-opacity":.55}});
  for(const zone of DATA.zones){{const geometry=zone.geometry;for(const [west,east] of longitudeSegments(geometry)){{const [x1,y1]=mapPoint(geometry.north_latitude_deg,west),[x2,y2]=mapPoint(geometry.south_latitude_deg,east);addSvg("rect",{{x:Math.min(x1,x2),y:Math.min(y1,y2),width:Math.abs(x2-x1),height:Math.abs(y2-y1),class:`zone-area${{zone.zone_id===selectedZoneId?" selected":""}}`,"data-zone":zone.zone_id}});}}}}
  for(const zone of DATA.zones)for(const part of boundaryPaths(zone.display_boundary))addSvg("polyline",{{points:part.map(value=>value.join(",")).join(" "),class:`zone-boundary${{zone.zone_id===selectedZoneId?" selected":""}}`,"data-zone":zone.zone_id}});
  const selectedZone=DATA.zones.find(zone=>zone.zone_id===selectedZoneId);if(selectedZone){{const [rawX,y]=mapPoint(selectedZone.geometry.representative_center[0],selectedZone.geometry.representative_center[1]),x=Math.max(8,Math.min(992,rawX)),anchor=rawX<40?"start":rawX>960?"end":"middle";const label=addSvg("text",{{x,y:Math.max(15,Math.min(490,y-7)),class:"zone-map-label","text-anchor":anchor}});label.textContent=selectedZone.zone_id;}}
  map.dataset.boundaryPaths=String(map.querySelectorAll(".zone-boundary").length);map.dataset.zoneAreas=String(map.querySelectorAll(".zone-area").length);map.dataset.selectedZone=selectedZoneId||"";map.dataset.selectedLabel=selectedZone?.zone_id||"";
}}
function detailRow(label,value){{const term=document.createElement("dt"),description=document.createElement("dd");term.textContent=label;description.textContent=String(value);return [term,description];}}
function showSelectedZone(zone){{selectedDetail.replaceChildren();selectedDetail.dataset.selectedZone=zone.zone_id;const geometry=zone.geometry,list=document.createElement("dl");list.className="detail-grid";for(const [label,value] of [["zone_id",zone.zone_id],["south / north",`${{geometry.south_latitude_deg}}° / ${{geometry.north_latitude_deg}}°`],["west / east",`${{geometry.west_longitude_deg}}° / ${{geometry.east_longitude_deg}}°`],["representative center",`${{geometry.representative_center[0]}}°, ${{geometry.representative_center[1]}}°`],["priority",zone.priority],["wrapped",geometry.wrapped]])list.append(...detailRow(label,value));selectedDetail.append(list);}}
function clearSelectedZone(){{selectedZoneId=null;hoveredZoneId=null;hoverPoint=null;selectedDetail.dataset.selectedZone="";selectedDetail.innerHTML='<p class="muted">Click or tap a Zone on the globe or map.</p>';drawMap();drawGlobe();}}
function selectZone(zone){{selectedZoneId=zone.zone_id;hoveredZoneId=null;hoverPoint=null;showSelectedZone(zone);drawMap();drawGlobe();}}
function toggleZone(zone){{if(zone?.zone_id===selectedZoneId)clearSelectedZone();else if(zone)selectZone(zone);else clearSelectedZone();}}
function zoneContainsForDisplay(zone,latitude,longitude){{const geometry=zone.geometry;if(latitude<geometry.south_latitude_deg||latitude>geometry.north_latitude_deg)return false;if(Math.abs(latitude)===90)return true;return geometry.wrapped?longitude>=geometry.west_longitude_deg||longitude<=geometry.east_longitude_deg:longitude>=geometry.west_longitude_deg&&longitude<=geometry.east_longitude_deg;}}
function zoneAtCanvas(clientX,clientY){{const box=canvas.getBoundingClientRect(),px=clientX-box.left,py=clientY-box.top,scale=globeScale(),x1=(px-logicalWidth/2)/scale,yRotated=-(py-logicalHeight/2)/scale,radiusSquared=x1*x1+yRotated*yRotated;if(radiusSquared>1)return null;const zRotated=Math.sqrt(Math.max(0,1-radiusSquared)),cp=Math.cos(camera.pitch),sp=Math.sin(camera.pitch),y=yRotated*cp+zRotated*sp,z1=-yRotated*sp+zRotated*cp,cy=Math.cos(camera.yaw),sy=Math.sin(camera.yaw),x=x1*cy-z1*sy,z=x1*sy+z1*cy,latitude=Math.asin(Math.max(-1,Math.min(1,y)))*180/Math.PI,longitude=canonicalLongitude(Math.atan2(z,x)*180/Math.PI);return DATA.zones.find(zone=>zoneContainsForDisplay(zone,latitude,longitude))||null;}}
map.addEventListener("click",event=>{{const target=event.target.closest("[data-zone]");if(!target)return;const zone=DATA.zones.find(value=>value.zone_id===target.dataset.zone);toggleZone(zone);}});document.getElementById("zone-reset").addEventListener("click",()=>{{camera={{...INITIAL_CAMERA}};drawGlobe();}});
canvas.addEventListener("pointerdown",event=>{{pointer={{startX:event.clientX,startY:event.clientY,lastX:event.clientX,lastY:event.clientY,moved:false}};canvas.classList.add("dragging");canvas.setPointerCapture(event.pointerId);}});
canvas.addEventListener("pointermove",event=>{{if(!pointer){{const zone=zoneAtCanvas(event.clientX,event.clientY),box=canvas.getBoundingClientRect();hoveredZoneId=zone?.zone_id||null;hoverPoint=zone?{{x:event.clientX-box.left,y:event.clientY-box.top}}:null;drawGlobe();return;}}const total=Math.hypot(event.clientX-pointer.startX,event.clientY-pointer.startY);if(total>5)pointer.moved=true;if(pointer.moved){{hoveredZoneId=null;hoverPoint=null;camera.yaw+=(event.clientX-pointer.lastX)*.008;camera.pitch=Math.max(-MAX_PITCH,Math.min(MAX_PITCH,camera.pitch+(event.clientY-pointer.lastY)*.008));drawGlobe();}}pointer.lastX=event.clientX;pointer.lastY=event.clientY;}});
canvas.addEventListener("pointerup",event=>{{const wasClick=pointer&&!pointer.moved;pointer=null;canvas.classList.remove("dragging");if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);if(wasClick)toggleZone(zoneAtCanvas(event.clientX,event.clientY));}});canvas.addEventListener("pointerleave",()=>{{if(!pointer){{hoveredZoneId=null;hoverPoint=null;drawGlobe();}}}});canvas.addEventListener("pointercancel",()=>{{pointer=null;canvas.classList.remove("dragging");}});document.addEventListener("keydown",event=>{{if(event.key==="Escape")clearSelectedZone();}});window.addEventListener("resize",drawGlobe);drawMap();drawGlobe();
"""
    return standalone_page(
        title="Zone Inspection",
        eyebrow="Zone layout inspection",
        body=body,
        script=script,
        language_strings={
            "Zone Inspection": "Zone 检查",
            "Zone layout inspection": "Zone 布局检查",
            "Tool Overview": "工具总览",
            "Formal static Zone layout": "正式静态 Zone 布局",
            "Geometry is read from PhysicalWorld. Selection and highlighting are display-only and do not resolve Zone membership.": "几何信息读取自 PhysicalWorld。选择与高亮仅用于显示，不执行 Zone membership resolution。",
            "A Zone is a formal geographic/service identity. Its representative point and boundary are inspection aids; they are not a network node or a Satellite-to-Zone link.": "Zone 是正式的地理 / 服务 identity。其代表点和边界只是检查辅助；它们不是网络节点，也不是 Satellite-to-Zone 链路。",
            "Selected Zone": "已选 Zone",
            "Click or tap a Zone on the globe or map.": "在球面或地图上点击 Zone。",
            "Rotatable Zone globe": "可旋转 Zone 球面",
            "Longitude / latitude reference map": "经纬度参考图",
            "The globe uses unit radius. Natural Earth coastline is a Tool-private orientation reference only. Rectangle boundaries are display_boundary samples, not claimed great-circle arcs. Browser hit testing is UI navigation only.": "球体采用单位半径；Natural Earth 海岸线仅作 Tool 私有方位参考。矩形边界只是 display_boundary 采样，不声称是大圆线。浏览器命中测试仅用于 UI 导航。",
        },
    )


__all__ = ["render_zone_page"]
