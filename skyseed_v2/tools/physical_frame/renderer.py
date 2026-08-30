"""Render one detached PhysicalFrame as an offline physical-only view."""

from __future__ import annotations

from html import escape
from typing import Any

from .._earth_reference import _natural_earth_coastline_lines
from .._html import json_for_script, standalone_page


def render_physical_frame_page(data: dict[str, Any]) -> str:
    inventory = data["inventory"]
    assert isinstance(inventory, dict)
    stats = "".join(
        f'<div class="card stat"><span class="muted">{escape(label)}</span>'
        f"<strong>{inventory[key]}</strong></div>"
        for key, label in (
            ("positions", "Position"),
            ("satellites", "Satellite"),
            ("ground_stations", "GroundStation"),
            ("orbits", "Orbit"),
        )
    )
    timestamp = escape(str(data["timestamp"]))
    body = f"""
<style>
.tool-navigation {{ margin-bottom:12px; }}
.frame-map {{ touch-action:none; cursor:crosshair; aspect-ratio:55/28; }}
.map-toolbar {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:10px; }}
.legend {{ display:flex; gap:14px; flex-wrap:wrap; }}
.legend i {{ display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:5px; }}
.satellite-key {{ background:#7de8f5; }}
.ground-station-key {{ background:#ffd166; }}
.selected-detail {{ min-height:10rem; }}
.detail-grid {{ display:grid; grid-template-columns:max-content 1fr; gap:5px 14px; margin:0; }}
.detail-grid dt {{ color:var(--muted); }}
.detail-grid dd {{ margin:0; overflow-wrap:anywhere; }}
</style>
<nav class="tool-navigation"><a href="../index.html">← Tool Overview</a></nav>
<section class="card">
  <span class="status PASS">PASS</span>
  <h2>PhysicalFrame observed</h2>
  <p><strong>Timestamp:</strong> <code>{timestamp}</code></p>
  <p>This view reads one exact physical frame. Its timestamp is the frame identity; the Tool adds no sequence identity and performs no physical projection.</p>
</section>
<section><h2>Physical inventory</h2><div class="grid stats">{stats}</div></section>
<section class="card selected-detail">
  <h2>Selected physical node</h2>
  <div id="selected-node" data-selected-node-id=""><p class="muted">Click or tap a node subpoint on the map.</p></div>
</section>
<section class="card">
  <h2>WGS84 node subpoints</h2>
  <div class="map-toolbar">
    <button id="clear-selection" type="button">Clear selection</button>
    <span class="legend"><span><i class="satellite-key"></i>Satellite</span><span><i class="ground-station-key"></i>GroundStation</span></span>
  </div>
  <canvas class="frame-map" id="frame-canvas" width="1100" height="560"></canvas>
  <p class="muted">The map uses formal WGS84 geodetic views. Ellipsoidal height and exact ECEF coordinates remain in the selected-node detail; marker size is display-only.</p>
</section>
"""
    script = f"""
const DATA={json_for_script(data)};
const COASTLINES={json_for_script(_natural_earth_coastline_lines())};
const canvas=document.getElementById("frame-canvas"),ctx=canvas.getContext("2d"),detail=document.getElementById("selected-node");
let logicalWidth=1100,logicalHeight=560,hitRegions=[],selectedNodeId="",hoverNodeId="";
function prepareCanvas(){{
  const box=canvas.getBoundingClientRect(),dpr=Math.max(1,window.devicePixelRatio||1);logicalWidth=Math.max(1,box.width);logicalHeight=Math.max(1,box.height);
  const width=Math.max(1,Math.round(logicalWidth*dpr)),height=Math.max(1,Math.round(logicalHeight*dpr));if(canvas.width!==width||canvas.height!==height){{canvas.width=width;canvas.height=height;}}ctx.setTransform(width/logicalWidth,0,0,height/logicalHeight,0,0);
  canvas.dataset.devicePixelRatio=String(dpr);canvas.dataset.logicalWidth=String(logicalWidth);canvas.dataset.logicalHeight=String(logicalHeight);
}}
const mapPoint=(latitude,longitude)=>[Number(longitude+180)/360*logicalWidth,Number(90-latitude)/180*logicalHeight];
function drawBackground(){{
  ctx.fillStyle="#071a29";ctx.fillRect(0,0,logicalWidth,logicalHeight);ctx.strokeStyle="#85b8c72b";ctx.lineWidth=.7;
  for(let latitude=-60;latitude<=60;latitude+=30){{const [,y]=mapPoint(latitude,0);ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(logicalWidth,y);ctx.stroke();}}
  for(let longitude=-150;longitude<180;longitude+=30){{const [x]=mapPoint(0,longitude);ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,logicalHeight);ctx.stroke();}}
  ctx.strokeStyle="#b7c9cf7a";ctx.lineWidth=.75;let segments=0;
  for(const line of COASTLINES){{ctx.beginPath();let previous=null;for(const [latitude,longitude] of line){{const point=mapPoint(latitude,longitude);if(!previous||Math.abs(point[0]-previous[0])>logicalWidth/2)ctx.moveTo(point[0],point[1]);else{{ctx.lineTo(point[0],point[1]);segments++;}}previous=point;}}ctx.stroke();}}
  canvas.dataset.coastlineSegments=String(segments);
}}
function drawLabel(label,x,y){{
  ctx.save();ctx.font="600 12px system-ui, sans-serif";const padding=5,width=Math.min(logicalWidth-12,ctx.measureText(label).width+padding*2),left=Math.max(6,Math.min(logicalWidth-width-6,x+8)),top=Math.max(6,Math.min(logicalHeight-28,y-27));ctx.fillStyle="#06111eea";ctx.strokeStyle="#8be6f5";ctx.beginPath();ctx.roundRect(left,top,width,22,4);ctx.fill();ctx.stroke();ctx.fillStyle="#eefcff";ctx.textBaseline="middle";ctx.fillText(label,left+padding,top+11,width-padding*2);ctx.restore();
}}
function draw(){{
  prepareCanvas();drawBackground();hitRegions=[];let selectedPoint=null,hoverPoint=null;
  for(const node of DATA.nodes){{const physical=node.position.geodetic,point=mapPoint(physical.latitude_deg,physical.longitude_deg),selected=node.node_id===selectedNodeId,hovered=node.node_id===hoverNodeId,radius=selected?6:hovered?4.5:node.kind==="ground-station"?3.2:2.1;ctx.save();ctx.globalAlpha=selected?1:(hovered?0.95:(node.kind==="ground-station"?0.95:0.72));ctx.fillStyle=node.kind==="ground-station"?"#ffd166":"#7de8f5";ctx.beginPath();ctx.arc(point[0],point[1],radius,0,Math.PI*2);ctx.fill();if(selected){{ctx.strokeStyle="#f7fbff";ctx.lineWidth=1.2;ctx.beginPath();ctx.arc(point[0],point[1],9,0,Math.PI*2);ctx.stroke();selectedPoint=point;}}else if(hovered)hoverPoint=point;ctx.restore();hitRegions.push({{node,x:point[0],y:point[1]}});}}
  const labeled=selectedPoint||hoverPoint,nodeId=selectedPoint?selectedNodeId:hoverNodeId;if(labeled&&nodeId)drawLabel(nodeId,labeled[0],labeled[1]);canvas.dataset.positionMarkers=String(hitRegions.length);canvas.dataset.selectedNodeId=selectedNodeId;
}}
function detailRow(label,value){{const term=document.createElement("dt"),description=document.createElement("dd");term.textContent=label;description.textContent=String(value);return [term,description];}}
function showNode(node){{
  selectedNodeId=node.node_id;hoverNodeId="";detail.replaceChildren();detail.dataset.selectedNodeId=node.node_id;const list=document.createElement("dl");list.className="detail-grid";const position=node.position,ecef=position.ecef_m,geodetic=position.geodetic,rows=[["node_id",node.node_id],["kind",node.kind],["ASN",node.asn],["ECEF x / y / z",`${{ecef.x.toFixed(3)}} / ${{ecef.y.toFixed(3)}} / ${{ecef.z.toFixed(3)}} m`],["latitude / longitude",`${{geodetic.latitude_deg.toFixed(6)}}° / ${{geodetic.longitude_deg.toFixed(6)}}°`],["ellipsoidal height",`${{geodetic.ellipsoidal_height_m.toFixed(3)}} m`]];
  if(node.kind==="satellite")rows.push(["Orbit",node.orbit_ref],["orbit_index",node.orbit_index],["simultaneous GroundStation access capability",node.max_simultaneous_ground_station_accesses??"unspecified"]);else{{rows.push(["station_type",node.station_type],["simultaneous Satellite access capability",node.max_simultaneous_satellite_accesses??"unspecified"]);if(String(node.operator||"").trim())rows.push(["operator",node.operator]);}}
  for(const [label,value] of rows)list.append(...detailRow(label,value));detail.append(list);draw();
}}
function clearSelection(){{selectedNodeId="";hoverNodeId="";detail.dataset.selectedNodeId="";detail.innerHTML='<p class="muted">Click or tap a node subpoint on the map.</p>';draw();}}
function hitAt(clientX,clientY){{const box=canvas.getBoundingClientRect(),x=clientX-box.left,y=clientY-box.top;let best=null,distance=Infinity;for(const value of hitRegions){{const candidate=Math.hypot(x-value.x,y-value.y);if(candidate<distance){{best=value;distance=candidate;}}}}return distance<=9?best:null;}}
canvas.addEventListener("click",event=>{{const hit=hitAt(event.clientX,event.clientY);if(!hit){{clearSelection();return;}}if(selectedNodeId===hit.node.node_id)clearSelection();else showNode(hit.node);}});
canvas.addEventListener("pointermove",event=>{{const hit=hitAt(event.clientX,event.clientY),next=hit?.node.node_id||"";if(next!==hoverNodeId){{hoverNodeId=next;draw();}}}});
canvas.addEventListener("pointerleave",()=>{{if(hoverNodeId){{hoverNodeId="";draw();}}}});document.getElementById("clear-selection").onclick=clearSelection;document.addEventListener("keydown",event=>{{if(event.key==="Escape")clearSelection();}});window.addEventListener("resize",draw);draw();
"""
    return standalone_page(
        title="Physical Frame Inspection",
        eyebrow="Physical Frame inspection",
        body=body,
        script=script,
        language_strings={
            "Physical Frame Inspection": "物理帧检查",
            "Physical Frame inspection": "物理帧检查",
            "Tool Overview": "工具总览",
            "PhysicalFrame observed": "已观察 PhysicalFrame",
            "Timestamp:": "时间戳：",
            "This view reads one exact physical frame. Its timestamp is the frame identity; the Tool adds no sequence identity and performs no physical projection.": "本视图读取一个精确物理帧。时间戳就是帧身份；Tool 不添加序列身份，也不执行物理投影。",
            "Physical inventory": "物理对象数量",
            "Selected physical node": "已选物理节点",
            "Click or tap a node subpoint on the map.": "点击地图上的节点星下点。",
            "WGS84 node subpoints": "WGS84 节点星下点",
            "Clear selection": "清除选择",
            "The map uses formal WGS84 geodetic views. Ellipsoidal height and exact ECEF coordinates remain in the selected-node detail; marker size is display-only.": "地图使用正式 WGS84 大地坐标视图。椭球高度和精确 ECEF 坐标保留在已选节点详情中；标记大小仅用于显示。",
        },
    )


__all__ = ["render_physical_frame_page"]
