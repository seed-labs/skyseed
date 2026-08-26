"""Render the lazy offline Slice / Difference inspection UI."""

from __future__ import annotations

from html import escape
from typing import Any

from .._display_geometry import display_geometry_policy
from .._earth_reference import _natural_earth_coastline_lines
from .._html import json_for_script, standalone_page


def render_slice_difference_page(data: dict[str, Any]) -> str:
    """Render one compact manifest; detailed Slice state stays in chunks."""

    inventory = data["inventory"]
    assert isinstance(inventory, dict)
    manifest = {key: value for key, value in data.items() if key != "slices"}
    stats = "".join(
        '<div class="card stat"><span class="muted">'
        f"{escape(label)}</span><strong>{inventory[key]}</strong></div>"
        for key, label in (
            ("slices", "Slices"),
            ("differences", "Differences"),
            ("satellites", "Satellites"),
            ("ground_stations", "Ground Stations"),
            ("zones", "Zones"),
        )
    )
    model_context = _render_model_context(data)
    body = f"""
<style>
.tool-navigation {{ margin-bottom:12px; }}
.timeline {{ display:grid; grid-template-columns:auto auto auto minmax(180px,1fr) auto auto; gap:8px; align-items:center; }}
.timeline input {{ width:100%; }}
.timeline label {{ color:var(--muted); white-space:nowrap; }}
.slice-context-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(145px,1fr)); gap:8px; }}
.slice-context-metric {{ padding:9px 10px; border:1px solid #20425a; border-radius:8px; background:#071c2d; }}
.slice-context-metric span {{ display:block; color:var(--muted); font-size:.78rem; }}
.slice-context-metric strong {{ display:block; margin-top:3px; font-size:1rem; overflow-wrap:anywhere; }}
.physical-context {{ margin:10px 0 0; line-height:1.65; }}
.model-context-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:12px; margin-top:12px; }}
.model-context-group {{ padding:10px; border:1px solid #20425a; border-radius:8px; }}
.model-context-group h3 {{ margin:0 0 8px; font-size:.96rem; }}
.model-context-group dl {{ margin:0; }}
.model-context-notes {{ margin-bottom:0; }}
.globe-toolbar {{ display:flex; flex-wrap:wrap; gap:7px; align-items:center; margin:9px 0; }}
.globe-toolbar button.active {{ border-color:var(--cyan); color:var(--cyan); box-shadow:0 0 0 1px var(--cyan) inset; }}
#clear-inspection {{ border-color:#ffbd73; color:#ffdcad; font-weight:700; }}
.globe-wrap {{ position:relative; }}
#slice-globe {{ touch-action:none; cursor:grab; aspect-ratio:55/28; min-height:420px; }}
#slice-globe.dragging {{ cursor:grabbing; }}
#slice-globe.inspecting {{ cursor:pointer; }}
#hover-label {{ position:absolute; left:12px; bottom:12px; max-width:calc(100% - 24px); padding:6px 9px; border-radius:7px; background:#061621e8; color:#e7f7ff; pointer-events:none; opacity:0; transition:opacity .12s; overflow-wrap:anywhere; }}
#hover-label.visible {{ opacity:1; }}
.legend {{ display:flex; flex-wrap:wrap; gap:10px; margin-left:auto; font-size:.84rem; color:var(--muted); }}
.legend span::before {{ content:""; display:inline-block; width:14px; height:3px; margin:0 5px 3px 0; background:var(--legend); }}
.context-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:12px; }}
.route-controls {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; margin-bottom:12px; }}
.route-controls label {{ display:grid; gap:4px; color:var(--muted); }}
.route-controls select {{ width:100%; }}
.detail-grid {{ display:grid; grid-template-columns:max-content 1fr; gap:5px 14px; margin:0; }}
.detail-grid dt {{ color:var(--muted); }}
.detail-grid dd {{ margin:0; overflow-wrap:anywhere; }}
.detail-actions {{ display:flex; flex-wrap:wrap; gap:7px; margin-top:12px; }}
.object-status {{ display:inline-block; margin:0 0 9px; padding:3px 8px; border-radius:999px; background:#123247; color:#aeeaff; }}
.object-status.absent {{ background:#552a2a; color:#ffc9c9; }}
.secondary-controls {{ margin-top:12px; }}
.control-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:9px; }}
.control-grid label {{ display:grid; gap:4px; color:var(--muted); }}
.control-grid select {{ width:100%; }}
.difference-legend {{ display:flex; flex-wrap:wrap; gap:12px; margin:10px 0; }}
.difference-legend span {{ white-space:nowrap; }}
.swatch {{ display:inline-block; width:15px; height:4px; margin:0 5px 3px 0; }}
.diagnostic {{ border-left:3px solid var(--amber); padding-left:9px; }}
.loading {{ color:var(--cyan); }}
@media(max-width:700px) {{ .timeline {{ grid-template-columns:auto auto auto; }} .timeline input,.timeline strong {{ grid-column:1/-1; }} .route-controls {{ grid-template-columns:1fr; }} #slice-globe {{ min-height:310px; }} }}
</style>
<nav class="tool-navigation"><a href="../index.html">← Tool Overview</a></nav>
<section class="card">
  <span class="status PASS">PASS</span>
  <h2>Slice / Difference timeline</h2>
  <p class="muted">One offline chunk is loaded at a time. Selection and display-only path restoration persist while the timeline moves.</p>
  <p>Each Slice is an exact formal sample at its timestamp. Difference records the change from the previous sample; the globe restores only retained sampled positions, Connections, assignments, and Routes.</p>
  <div class="grid stats">{stats}</div>
</section>
<section class="card">
  <div class="timeline">
    <button id="play-slices" type="button" aria-pressed="false">Play</button>
    <button id="previous-slice" type="button">Previous</button>
    <button id="next-slice" type="button">Next</button>
    <input id="slice-range" type="range" min="0" max="0" value="0" aria-label="Slice timeline">
    <strong id="slice-label" class="loading">Loading…</strong>
    <label>Speed <select id="playback-speed"><option value="2000">0.5×</option><option value="1000" selected>1×</option><option value="500">2×</option><option value="250">4×</option></select></label>
  </div>
</section>
<section class="card" aria-labelledby="slice-context-heading">
  <h2 id="slice-context-heading">Current Slice context</h2>
  <div id="slice-context" class="slice-context-grid"><p class="muted loading">Loading current Slice context…</p></div>
  <p id="slice-physical-context" class="muted physical-context"></p>
</section>
{model_context}
<section class="card">
  <h2>Dynamic Earth</h2>
  <p class="muted">Drag to rotate. Choose an inspection mode, then click a generously sized node, link, or Zone representative point.</p>
  <div class="globe-toolbar" role="toolbar" aria-label="Earth inspection mode">
    <button id="pick-node" class="active" data-pick-mode="node" type="button">Inspect nodes</button>
    <button id="pick-connection" data-pick-mode="connection" type="button">Inspect Connections</button>
    <button id="pick-zone" data-pick-mode="zone" type="button">Inspect Zones</button>
    <button id="clear-inspection" type="button">Clear inspection</button>
    <label><input id="show-difference" type="checkbox"> Difference mode</label>
    <div class="legend">
      <span style="--legend:#8be6f5">Satellite</span>
      <span style="--legend:#ffd08a">GroundStation</span>
      <span style="--legend:#7dffb2">Route</span>
      <span style="--legend:#ef8cff">Zone service</span>
    </div>
  </div>
  <div class="globe-wrap">
    <canvas id="slice-globe" width="1100" height="560"></canvas>
    <div id="hover-label" role="status"></div>
  </div>
</section>
<div class="context-grid">
  <section class="card">
    <h2>Selected object</h2>
    <div id="selected-detail" data-selection-kind="" data-selection-status="none"><p class="muted">Click an object on Dynamic Earth.</p></div>
  </section>
  <section class="card">
    <h2>Zone / Gateway Route</h2>
    <div class="route-controls">
      <label>Zone<select id="gateway-route-zone"><option value="">Select Zone</option></select></label>
      <label>Gateway<select id="gateway-route-gateway"><option value="">Select relevant Gateway</option></select></label>
      <label>Direction<select id="gateway-route-direction"><option value="zone-to-gateway">Zone → Gateway</option><option value="gateway-to-zone">Gateway → Zone</option></select></label>
    </div>
    <div id="path-detail" data-route-status="none"><p class="muted">Select a Zone, a currently published Gateway, and a direction.</p></div>
  </section>
</div>
<details class="card secondary-controls">
  <summary>Search / accessibility fallbacks</summary>
  <p class="muted">These locators mirror the click-first workflow; they do not create facts.</p>
  <div class="control-grid">
    <label>Satellite / GroundStation<select id="node-select"><option value="">Locate node</option></select></label>
    <label>Connection<select id="connection-select"><option value="">Locate Connection</option></select></label>
    <label>Zone<select id="zone-select"><option value="">Locate Zone</option></select></label>
    <label>Advanced Route source<select id="route-source"><option value="">Locate source</option></select></label>
    <label>Advanced G/Z target<select id="route-target"><option value="">Locate target</option></select></label>
  </div>
</details>
<details id="difference-panel" class="card">
  <summary>Difference from previous Slice</summary>
  <div id="difference-counts" class="muted">Slice 0 has no predecessor.</div>
  <div class="difference-legend">
    <span><i class="swatch" style="background:#59e391"></i>added Connection</span>
    <span><i class="swatch" style="background:#ff6f7d"></i>removed Connection</span>
    <span><i class="swatch" style="background:#ffd166"></i>parameter changed</span>
    <span><i class="swatch" style="background:#ef8cff"></i>Zone assignment changed</span>
  </div>
  <div id="difference-context"><p class="muted">Enable Difference mode or select an affected object for contextual details.</p></div>
</details>
<details class="card">
  <summary>Diagnostics</summary>
  <div id="diagnostics"><p class="muted">No current anomalies.</p></div>
</details>
"""
    script = (
        f"window.SKYSEED_SLICE_MANIFEST={json_for_script(manifest)};\n"
        f"const COASTLINES={json_for_script(_natural_earth_coastline_lines())};\n"
        f"const DISPLAY_GEOMETRY={json_for_script(display_geometry_policy())};\n"
        + r"""
const MANIFEST=window.SKYSEED_SLICE_MANIFEST;
window.__SKYSEED_SLICE_CHUNKS__=window.__SKYSEED_SLICE_CHUNKS__||{};
async function decodeExactChunk(value){if(!value||value.encoding!=="gzip-base64-json")return value;if(typeof DecompressionStream!=="function")throw new Error("This browser lacks gzip chunk support");const binary=atob(value.data),bytes=Uint8Array.from(binary,ch=>ch.charCodeAt(0)),stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip")),text=await new Response(stream).text();if(new TextEncoder().encode(text).length!==value.uncompressed_bytes)throw new Error("Slice chunk size audit failed");return JSON.parse(text);}
const canvas=document.getElementById("slice-globe"),ctx=canvas.getContext("2d"),hoverLabel=document.getElementById("hover-label");
const manifestById=new Map(MANIFEST.slice_manifest.map(item=>[Number(item.slice_id),item]));
const orderedIds=MANIFEST.slice_manifest.map(item=>Number(item.slice_id));
const staticNodes=new Map(MANIFEST.topology.nodes.map(node=>[node.node_id,node]));
const orbitByRef=new Map(MANIFEST.topology.orbits.map(orbit=>[orbit.orbit_ref,orbit]));
const zonesById=new Map(MANIFEST.topology.zones.map(zone=>[zone.zone_id,zone]));
const INITIAL_CAMERA={yaw:-.55,pitch:.28},MAX_PITCH=89*Math.PI/180;
let camera={...INITIAL_CAMERA},pointer=null,currentSlice=null,currentIndex=Math.max(0,orderedIds.findIndex(id=>id===1)),requestedSliceId=null,loadGeneration=0;
let playbackTimer=null,playbackGeneration=0,playbackActive=false;
let logicalWidth=1100,logicalHeight=560,nodeHits=[],connectionHits=[],zoneHits=[],hoverHit=null,pickMode="node";
let selectedNodeId="",selectedConnectionRef="",selectedZoneId="",activeSelectionKind="",showSatelliteZones=false;
let routeSourceId="",routeTargetRef="",selectedPath=[],routeStatus="none",differenceMode=false;
let routeZoneId="",routeGatewayId="",routeDirection="zone-to-gateway";
const pendingLoads=new Map(),range=document.getElementById("slice-range");range.max=String(Math.max(0,orderedIds.length-1));range.value=String(currentIndex);
const spherePoint=(lat,lon,r=1)=>{const a=lat*Math.PI/180,b=lon*Math.PI/180;return [r*Math.cos(a)*Math.cos(b),r*Math.sin(a),r*Math.cos(a)*Math.sin(b)];};
const rotate=point=>{const [x,y,z]=point,cy=Math.cos(camera.yaw),sy=Math.sin(camera.yaw),cp=Math.cos(camera.pitch),sp=Math.sin(camera.pitch),x1=x*cy+z*sy,z1=-x*sy+z*cy;return [x1,y*cp-z1*sp,y*sp+z1*cp];};
function prepareCanvas(){const box=canvas.getBoundingClientRect(),dpr=Math.max(1,window.devicePixelRatio||1);logicalWidth=Math.max(1,box.width);logicalHeight=Math.max(1,box.height);const w=Math.round(logicalWidth*dpr),h=Math.round(logicalHeight*dpr);if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;}ctx.setTransform(w/logicalWidth,0,0,h/logicalHeight,0,0);}
const scale=()=>Math.min(logicalWidth,logicalHeight)*.36;
const project=point=>{const [x,y,z]=rotate(point),s=scale();return [logicalWidth/2+x*s,logicalHeight/2-y*s,z];};
function drawEarth(){const radius=scale(),gradient=ctx.createRadialGradient(logicalWidth*.45,logicalHeight*.42,10,logicalWidth/2,logicalHeight/2,radius);gradient.addColorStop(0,"#174a68");gradient.addColorStop(1,"#071c2d");ctx.fillStyle=gradient;ctx.beginPath();ctx.arc(logicalWidth/2,logicalHeight/2,radius,0,Math.PI*2);ctx.fill();}
function drawPolyline(points,color,width,opacity=1,dashed=false){ctx.save();ctx.strokeStyle=color;ctx.lineWidth=width;ctx.globalAlpha=opacity;ctx.setLineDash(dashed?[7,5]:[]);ctx.beginPath();let previous=null;for(const value of points){const point=project(value);if(point[2]<0){previous=null;continue;}if(previous)ctx.lineTo(point[0],point[1]);else ctx.moveTo(point[0],point[1]);previous=point;}ctx.stroke();ctx.restore();}
function drawSegment(a,b,color,width,opacity=1,dashed=false){const pa=project(a),pb=project(b);if(pa[2]<0||pb[2]<0)return null;ctx.save();ctx.strokeStyle=color;ctx.lineWidth=width;ctx.globalAlpha=opacity;ctx.setLineDash(dashed?[7,5]:[]);ctx.beginPath();ctx.moveTo(pa[0],pa[1]);ctx.lineTo(pb[0],pb[1]);ctx.stroke();ctx.restore();return {x1:pa[0],y1:pa[1],x2:pb[0],y2:pb[1]};}
const shortNodeLabel=id=>{const node=staticNodes.get(id),suffix=id.split("/").at(-1);return `${node?.kind==="ground-station"?"GroundStation":"Satellite"} ${suffix}`;};
function drawSelectedLabel(x,y,text,color="#ffffff"){ctx.save();ctx.font="600 12px system-ui, sans-serif";const padding=6,width=Math.min(logicalWidth-16,ctx.measureText(text).width+padding*2),left=Math.max(8,Math.min(logicalWidth-width-8,x+12)),top=Math.max(8,Math.min(logicalHeight-28,y-30));ctx.fillStyle="#061621ed";ctx.strokeStyle=color;ctx.lineWidth=1.5;ctx.beginPath();ctx.roundRect(left,top,width,24,6);ctx.fill();ctx.stroke();ctx.fillStyle="#f4fbff";ctx.textBaseline="middle";ctx.fillText(text,left+padding,top+12,width-padding*2);ctx.restore();}
const positionMap=()=>new Map((currentSlice?.positions||[]).map(position=>[position.node_id,position]));
const connectionByRef=ref=>(currentSlice?.connections||[]).find(value=>value.connection_ref===ref)||null;
const assignmentByZone=id=>(currentSlice?.zone_service_assignments||[]).find(value=>value.zone_id===id)||null;
const servedZonesForSatellite=id=>(currentSlice?.zone_service_assignments||[]).filter(value=>value.service_satellite_id===id);
const physicalDisplayRadius=heightM=>1+Number(heightM)/DISPLAY_GEOMETRY.earth_reference_radius_m;
function nodePoint(id,positions,markerOffset=0){const value=positions.get(id),node=staticNodes.get(id);if(!value)return null;const physical=physicalDisplayRadius(value.geographic.ellipsoidal_height_m),surfaceOffset=node?.kind==="ground-station"?DISPLAY_GEOMETRY.surface_marker_offset_radii:0;return spherePoint(value.geographic.latitude_deg,value.geographic.longitude_deg,physical+surfaceOffset+markerOffset);}
function zoneBoundary(zone,r=1+DISPLAY_GEOMETRY.zone_marker_offset_radii){const g=zone.geometry,west=Number(g.west_longitude_deg),span=(Number(g.east_longitude_deg)-west+360)%360,south=Number(g.south_latitude_deg),north=Number(g.north_latitude_deg),points=[];for(let i=0;i<=24;i++)points.push(spherePoint(south,west+span*i/24,r));for(let i=1;i<=12;i++)points.push(spherePoint(south+(north-south)*i/12,west+span,r));for(let i=23;i>=0;i--)points.push(spherePoint(north,west+span*i/24,r));for(let i=11;i>=0;i--)points.push(spherePoint(south+(north-south)*i/12,west,r));return points;}
function drawOrbitGuides(){for(const guide of currentSlice?.orbit_guides||[]){const u=guide.basis_u_ecef_unit,v=guide.basis_v_ecef_unit,orbit=orbitByRef.get(guide.orbit_ref),radius=physicalDisplayRadius(Number(orbit?.altitude_km||0)*1000),points=[];for(let i=0;i<=96;i++){const a=i*Math.PI*2/96;points.push([(u.x*Math.cos(a)+v.x*Math.sin(a))*radius,(u.y*Math.cos(a)+v.y*Math.sin(a))*radius,(u.z*Math.cos(a)+v.z*Math.sin(a))*radius]);}drawPolyline(points,"#6b9bb0",.65,.16);}}
function differenceSets(){const d=currentSlice?.difference_from_previous;if(!d)return {added:new Set(),removed:new Set(),parameter:new Set(),zones:new Map()};return {added:new Set(d.added_connections.map(value=>value.connection_ref)),removed:new Set(d.removed_connection_ids.map(value=>value.connection_ref)),parameter:new Set(d.connection_parameter_patches.map(value=>value.connection_ref)),zones:new Map(d.zone_service_assignment_patches.map(value=>[value.zone_id,value]))};}
function drawZone(zone,color,width,opacity=.9,dashed=false){if(zone)drawPolyline(zoneBoundary(zone),color,width,opacity,dashed);}
function draw(){prepareCanvas();ctx.clearRect(0,0,logicalWidth,logicalHeight);nodeHits=[];connectionHits=[];zoneHits=[];drawEarth();for(const line of COASTLINES)drawPolyline(line.map(([lat,lon])=>spherePoint(lat,lon,1.002)),"#b7c9cf",.62,.3);if(!currentSlice)return;drawOrbitGuides();const positions=positionMap(),diff=differenceSets(),selectedConnectionValue=connectionByRef(selectedConnectionRef),selectedConnectionPresent=Boolean(selectedConnectionValue),selectedLabels=[];let diffAdded=0,diffRemoved=0,diffParameter=0,diffZones=0,zoneTethers=0,incidentContextDrawn=0,endpointContextDrawn=0,selectedConnectionHit=null;
for(const zone of MANIFEST.topology.zones){const rep=zone.representative_point,point=project(spherePoint(rep.latitude_deg,rep.longitude_deg,1+DISPLAY_GEOMETRY.zone_marker_offset_radii));if(point[2]>=0){zoneHits.push({kind:"zone",id:zone.zone_id,label:`Zone ${zone.zone_id}`,x:point[0],y:point[1]});ctx.save();ctx.globalAlpha=pickMode==="zone"?.38:.13;ctx.fillStyle="#d79cff";ctx.beginPath();ctx.arc(point[0],point[1],pickMode==="zone"?2.4:1.25,0,Math.PI*2);ctx.fill();ctx.restore();}}
if(selectedZoneId){const zone=zonesById.get(selectedZoneId);drawZone(zone,"#ffbd73",3,1);if(activeSelectionKind==="zone"){const rep=zone.representative_point,point=project(spherePoint(rep.latitude_deg,rep.longitude_deg,1+DISPLAY_GEOMETRY.zone_marker_offset_radii));if(point[2]>=0)selectedLabels.push({x:point[0],y:point[1],text:`Zone ${selectedZoneId}`,color:"#ffbd73"});}}
if(showSatelliteZones&&selectedNodeId&&staticNodes.get(selectedNodeId)?.kind==="satellite")for(const assignment of servedZonesForSatellite(selectedNodeId))drawZone(zonesById.get(assignment.zone_id),"#ef8cff",2,.78);
if(differenceMode)for(const [zoneId,patch] of diff.zones){drawZone(zonesById.get(zoneId),patch.operation==="removed"?"#ff6f7d":patch.operation==="added"?"#59e391":"#ef8cff",1.8,.75,true);diffZones++;}
for(const connection of currentSlice.connections){const id=connection.connection_id,a=nodePoint(id.endpoint_a,positions),b=nodePoint(id.endpoint_b,positions);if(!a||!b)continue;const selected=connection.connection_ref===selectedConnectionRef,hovered=hoverHit?.kind==="connection"&&hoverHit.id===connection.connection_ref,added=differenceMode&&diff.added.has(connection.connection_ref),parameter=differenceMode&&diff.parameter.has(connection.connection_ref),incidentContext=activeSelectionKind==="node"&&Boolean(selectedNodeId)&&(id.endpoint_a===selectedNodeId||id.endpoint_b===selectedNodeId);let color=id.kind==="inter-satellite"?"#5ea6c0":"#d4aa63",width=1,opacity=selectedConnectionPresent ? .08 : .24;if(selected){color="#f7fbff";width=4;opacity=1;}else if(hovered){color="#9cecff";width=3;opacity=.95;}else if(added){color="#59e391";width=2.4;opacity=.9;diffAdded++;}else if(parameter){color="#ffd166";width=2.2;opacity=.88;diffParameter++;}else if(incidentContext){color="#93aeb8";width=1.7;opacity=.58;}const segment=drawSegment(a,b,color,width,opacity);if(segment){if(incidentContext)incidentContextDrawn++;connectionHits.push({kind:"connection",id:connection.connection_ref,label:`${id.kind}: ${id.endpoint_a} ↔ ${id.endpoint_b}`,value:connection,...segment});if(selected){selectedConnectionHit=segment;if(activeSelectionKind==="connection")selectedLabels.push({x:(segment.x1+segment.x2)/2,y:(segment.y1+segment.y2)/2,text:`${id.kind}: ${shortNodeLabel(id.endpoint_a)} ↔ ${shortNodeLabel(id.endpoint_b)}`,color:"#ffffff"});}}}
if(differenceMode)for(const removed of currentSlice.difference_from_previous?.removed_connection_ids||[]){const id=removed.connection_id,a=nodePoint(id.endpoint_a,positions),b=nodePoint(id.endpoint_b,positions);if(!a||!b)continue;const segment=drawSegment(a,b,"#ff6f7d",2.3,.92,true);if(segment){connectionHits.push({kind:"connection",id:removed.connection_ref,label:`removed ${id.kind}: ${id.endpoint_a} ↔ ${id.endpoint_b}`,value:removed,removed:true,...segment});if(removed.connection_ref===selectedConnectionRef){selectedConnectionHit=segment;if(activeSelectionKind==="connection")selectedLabels.push({x:(segment.x1+segment.x2)/2,y:(segment.y1+segment.y2)/2,text:`removed ${id.kind}: ${shortNodeLabel(id.endpoint_a)} ↔ ${shortNodeLabel(id.endpoint_b)}`,color:"#ff6f7d"});}diffRemoved++;}}
const selectedAssignment=selectedZoneId?assignmentByZone(selectedZoneId):null;if(selectedZoneId&&selectedAssignment){const zone=zonesById.get(selectedZoneId),rep=zone.representative_point,a=spherePoint(rep.latitude_deg,rep.longitude_deg,1+DISPLAY_GEOMETRY.zone_marker_offset_radii),b=nodePoint(selectedAssignment.service_satellite_id,positions,DISPLAY_GEOMETRY.route_marker_offset_radii);if(b&&drawSegment(a,b,"#ef8cff",3,.95,true))zoneTethers++;}
if(differenceMode)for(const patch of diff.zones.values()){if(!patch.service_satellite_id)continue;const zone=zonesById.get(patch.zone_id),rep=zone.representative_point,b=nodePoint(patch.service_satellite_id,positions,DISPLAY_GEOMETRY.route_marker_offset_radii);if(b&&drawSegment(spherePoint(rep.latitude_deg,rep.longitude_deg,1+DISPLAY_GEOMETRY.zone_marker_offset_radii),b,"#ef8cff",1.5,.55,true))zoneTethers++;}
let routeChangedSegments=0;if(selectedPath.length>1)for(let index=1;index<selectedPath.length;index++){const source=selectedPath[index-1],patch=differenceMode&&currentSlice.difference_from_previous?.route_patches.find(value=>value[1]===source&&value[2]===routeTargetRef),a=nodePoint(source,positions,DISPLAY_GEOMETRY.route_marker_offset_radii),b=nodePoint(selectedPath[index],positions,DISPLAY_GEOMETRY.route_marker_offset_radii);if(patch)routeChangedSegments++;if(a&&b)drawSegment(a,b,patch?"#ffd166":"#7dffb2",patch?5:4,.98);}
for(const position of currentSlice.positions){const node=staticNodes.get(position.node_id),projected=project(nodePoint(position.node_id,positions,DISPLAY_GEOMETRY.route_marker_offset_radii));if(projected[2]<0)continue;const ground=node?.kind==="ground-station",selected=position.node_id===selectedNodeId,pathNode=selectedPath.includes(position.node_id),serviceNode=selectedAssignment?.service_satellite_id===position.node_id,endpointContext=activeSelectionKind==="connection"&&selectedConnectionPresent&&(selectedConnectionValue.connection_id.endpoint_a===position.node_id||selectedConnectionValue.connection_id.endpoint_b===position.node_id),hovered=hoverHit?.kind==="node"&&hoverHit.id===position.node_id,radius=ground?4.2:3.1;if(endpointContext)endpointContextDrawn++;ctx.save();ctx.globalAlpha=.92;ctx.fillStyle=ground?"#ffd08a":"#8be6f5";ctx.beginPath();ctx.arc(projected[0],projected[1],radius,0,Math.PI*2);ctx.fill();if(selected||pathNode||serviceNode||endpointContext||hovered){ctx.strokeStyle=selected?"#ffffff":serviceNode?"#ef8cff":pathNode?"#7dffb2":endpointContext?"#93aeb8":"#9cecff";ctx.lineWidth=selected?3:endpointContext?1.5:2;ctx.beginPath();ctx.arc(projected[0],projected[1],selected?9:endpointContext?6.5:7,0,Math.PI*2);ctx.stroke();}ctx.restore();if(selected&&activeSelectionKind==="node")selectedLabels.push({x:projected[0],y:projected[1],text:shortNodeLabel(position.node_id),color:ground?"#ffd08a":"#8be6f5"});nodeHits.push({kind:"node",id:position.node_id,label:`${node?.kind||"node"}: ${position.node_id}`,position,node,x:projected[0],y:projected[1]});}
const hoverRegion=hoverHit?.kind==="node"?nodeHits.find(item=>item.id===hoverHit.id):hoverHit?.kind==="connection"?connectionHits.find(item=>item.id===hoverHit.id):hoverHit?.kind==="zone"?zoneHits.find(item=>item.id===hoverHit.id):null,hoverText=hoverRegion?.kind==="node"?shortNodeLabel(hoverRegion.id):hoverRegion?.kind==="connection"?`${hoverRegion.value?.connection_id?.kind||"Connection"}: ${shortNodeLabel(hoverRegion.value?.connection_id?.endpoint_a||"")} ↔ ${shortNodeLabel(hoverRegion.value?.connection_id?.endpoint_b||"")}`:hoverRegion?.kind==="zone"?`Zone ${hoverRegion.id}`:"";for(const label of selectedLabels)drawSelectedLabel(label.x,label.y,label.text,label.color);if(hoverRegion&&hoverText&&!(hoverRegion.kind===activeSelectionKind&&((hoverRegion.kind==="node"&&hoverRegion.id===selectedNodeId)||(hoverRegion.kind==="connection"&&hoverRegion.id===selectedConnectionRef)||(hoverRegion.kind==="zone"&&hoverRegion.id===selectedZoneId))))drawSelectedLabel(hoverRegion.kind==="connection"?(hoverRegion.x1+hoverRegion.x2)/2:hoverRegion.x,hoverRegion.kind==="connection"?(hoverRegion.y1+hoverRegion.y2)/2:hoverRegion.y,hoverText,"#9cecff");canvas.dataset.selectedLabelKind=activeSelectionKind;canvas.dataset.selectedLabelText=selectedLabels[0]?.text||"";canvas.dataset.hoverLabelText=hoverText;
const firstSatellitePosition=currentSlice.positions.find(position=>staticNodes.get(position.node_id)?.kind==="satellite");canvas.dataset.earthReferenceRadiusM=String(DISPLAY_GEOMETRY.earth_reference_radius_m);canvas.dataset.firstSatelliteDisplayRadius=firstSatellitePosition?String(physicalDisplayRadius(firstSatellitePosition.geographic.ellipsoidal_height_m)):"";canvas.dataset.selectedConnectionId=selectedConnectionRef;canvas.dataset.selectedConnectionX=selectedConnectionHit?String((selectedConnectionHit.x1+selectedConnectionHit.x2)/2):"";canvas.dataset.selectedConnectionY=selectedConnectionHit?String((selectedConnectionHit.y1+selectedConnectionHit.y2)/2):"";
const affectedRoute=currentSlice.difference_from_previous?.route_patches.find(patch=>patch[0]!=="removed"&&currentSlice.routes.some(route=>route[0]===patch[1]&&route[1]===patch[2]));canvas.dataset.loadedSliceId=String(currentSlice.slice_id);canvas.dataset.loadedRouteEntries=String(currentSlice.routes.length);canvas.dataset.selectedNodeId=selectedNodeId;canvas.dataset.selectedConnectionRef=selectedConnectionRef;canvas.dataset.selectedZoneId=selectedZoneId;canvas.dataset.selectedZoneServiceSatellite=selectedAssignment?.service_satellite_id||"";canvas.dataset.selectedZoneOtherServiceZones=String(selectedAssignment?Math.max(0,servedZonesForSatellite(selectedAssignment.service_satellite_id).length-1):0);canvas.dataset.selectedZoneTethers=String(zoneTethers);canvas.dataset.satelliteServedZones=String(showSatelliteZones?servedZonesForSatellite(selectedNodeId).length:0);canvas.dataset.incidentContextConnections=String(incidentContextDrawn);canvas.dataset.endpointContextNodes=String(endpointContextDrawn);canvas.dataset.routeZoneId=routeZoneId;canvas.dataset.routeGatewayId=routeGatewayId;canvas.dataset.routeDirection=routeDirection;canvas.dataset.routeGatewayChoices=String(routeZoneId?gatewayCandidatesForZone(routeZoneId).length:0);canvas.dataset.routePathNodes=String(selectedPath.length);canvas.dataset.routeStatus=routeStatus;canvas.dataset.routeChangedSegments=String(routeChangedSegments);canvas.dataset.affectedRouteSource=affectedRoute?.[1]||"";canvas.dataset.affectedRouteTarget=affectedRoute?.[2]||"";canvas.dataset.differenceMode=String(differenceMode);canvas.dataset.differenceAddedDrawn=String(diffAdded);canvas.dataset.differenceRemovedDrawn=String(diffRemoved);canvas.dataset.differenceParameterDrawn=String(diffParameter);canvas.dataset.differenceZonesDrawn=String(diffZones);canvas.dataset.firstRemovedConnectionRef=currentSlice.difference_from_previous?.removed_connection_ids?.[0]?.connection_ref||"";publishHitTestData();}
const pointSegmentDistance=(px,py,s)=>{const dx=s.x2-s.x1,dy=s.y2-s.y1,n=dx*dx+dy*dy,t=n===0?0:Math.max(0,Math.min(1,((px-s.x1)*dx+(py-s.y1)*dy)/n));return Math.hypot(px-(s.x1+t*dx),py-(s.y1+t*dy));};
function hitAt(x,y,mode=pickMode){if(mode==="node"){let hit=null,d=Infinity;for(const item of nodeHits){const q=Math.hypot(x-item.x,y-item.y);if(q<d){hit=item;d=q;}}return d<=15?hit:null;}if(mode==="connection"){let hit=null,d=Infinity;for(const item of connectionHits){const q=pointSegmentDistance(x,y,item);if(q<d){hit=item;d=q;}}return d<=11?hit:null;}let hit=null,d=Infinity;for(const item of zoneHits){const q=Math.hypot(x-item.x,y-item.y);if(q<d){hit=item;d=q;}}return d<=12?hit:null;}
function publishHitTestData(){const positions=positionMap(),node=nodeHits[0],groundStation=nodeHits.find(item=>item.node?.kind==="ground-station"),zone=zoneHits[0],edge=connectionHits.find(item=>Math.hypot(item.x2-item.x1,item.y2-item.y1)>24)||connectionHits[0],connectedNode=edge?nodeHits.find(item=>item.id===edge.value?.connection_id?.endpoint_a||item.id===edge.value?.connection_id?.endpoint_b):null,visibleServiceZone=item=>{const assignment=assignmentByZone(item.id),point=assignment?nodePoint(assignment.service_satellite_id,positions):null;return point&&project(point)[2]>=0;},serviceZone=zoneHits.find(item=>visibleServiceZone(item)&&servedZonesForSatellite(assignmentByZone(item.id).service_satellite_id).length>1)||zoneHits.find(visibleServiceZone),serviceSatellite=nodeHits.find(item=>item.node?.kind==="satellite"&&servedZonesForSatellite(item.id).length);for(const [prefix,item,x,y] of [["firstNode",node,node?.x,node?.y],["connectedNode",connectedNode,connectedNode?.x,connectedNode?.y],["groundStation",groundStation,groundStation?.x,groundStation?.y],["firstZone",zone,zone?.x,zone?.y],["firstConnection",edge,edge?(edge.x1+edge.x2)/2:null,edge?(edge.y1+edge.y2)/2:null],["serviceZone",serviceZone,serviceZone?.x,serviceZone?.y],["serviceSatellite",serviceSatellite,serviceSatellite?.x,serviceSatellite?.y]]){canvas.dataset[`${prefix}Id`]=item?.id||"";canvas.dataset[`${prefix}X`]=x==null?"":String(x);canvas.dataset[`${prefix}Y`]=y==null?"":String(y);}}
function selectAt(clientX,clientY){const box=canvas.getBoundingClientRect(),hit=hitAt(clientX-box.left,clientY-box.top);if(!hit){clearActiveSelection();return;}const same=(hit.kind==="node"&&activeSelectionKind==="node"&&selectedNodeId===hit.id)||(hit.kind==="connection"&&activeSelectionKind==="connection"&&selectedConnectionRef===hit.id)||(hit.kind==="zone"&&activeSelectionKind==="zone"&&selectedZoneId===hit.id);if(same){clearActiveSelection();return;}switchVisualSelection(hit.kind);if(hit.kind==="node")showNode(hit.id);else if(hit.kind==="connection")showConnection(hit.id);else showZone(hit.id);}
function updateHover(clientX,clientY){const box=canvas.getBoundingClientRect(),next=hitAt(clientX-box.left,clientY-box.top);if(next?.kind===hoverHit?.kind&&next?.id===hoverHit?.id)return;hoverHit=next;hoverLabel.textContent=next?.label||"";hoverLabel.classList.toggle("visible",Boolean(next));canvas.classList.toggle("inspecting",Boolean(next));draw();}
canvas.addEventListener("pointerdown",event=>{pointer={startX:event.clientX,startY:event.clientY,lastX:event.clientX,lastY:event.clientY,moved:false};canvas.classList.add("dragging");canvas.setPointerCapture(event.pointerId);});
canvas.addEventListener("pointermove",event=>{if(!pointer){updateHover(event.clientX,event.clientY);return;}if(Math.hypot(event.clientX-pointer.startX,event.clientY-pointer.startY)>5)pointer.moved=true;if(pointer.moved){camera.yaw+=(event.clientX-pointer.lastX)*.008;camera.pitch=Math.max(-MAX_PITCH,Math.min(MAX_PITCH,camera.pitch+(event.clientY-pointer.lastY)*.008));hoverHit=null;draw();}pointer.lastX=event.clientX;pointer.lastY=event.clientY;});
canvas.addEventListener("pointerleave",()=>{if(!pointer){hoverHit=null;hoverLabel.classList.remove("visible");canvas.classList.remove("inspecting");draw();}});
canvas.addEventListener("pointerup",event=>{const click=pointer&&!pointer.moved;pointer=null;canvas.classList.remove("dragging");if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);if(click)selectAt(event.clientX,event.clientY);});canvas.addEventListener("pointercancel",()=>{pointer=null;canvas.classList.remove("dragging");});window.addEventListener("resize",draw);
document.addEventListener("keydown",event=>{if(event.key==="Escape")clearActiveSelection();});
function setPickMode(mode){pickMode=mode;for(const button of document.querySelectorAll("[data-pick-mode]"))button.classList.toggle("active",button.dataset.pickMode===mode);hoverHit=null;hoverLabel.classList.remove("visible");draw();}
function replaceOptions(select,values,placeholder,preserved=""){const options=[new Option(placeholder,"")];for(const [label,value] of values)options.push(new Option(label,value));if(preserved&&!values.some(([,value])=>value===preserved))options.push(new Option(`${preserved} (not current)`,preserved));select.replaceChildren(...options);select.value=preserved;}
function loadSlice(index){currentIndex=Math.max(0,Math.min(orderedIds.length-1,index));range.value=String(currentIndex);const id=orderedIds[currentIndex],generation=++loadGeneration;requestedSliceId=id;const known=window.__SKYSEED_SLICE_CHUNKS__[id];document.getElementById("slice-label").textContent=`Loading S${id}…`;const activateIfCurrent=value=>{if(generation===loadGeneration&&id===requestedSliceId)activateSlice(value);else if(id!==requestedSliceId)delete window.__SKYSEED_SLICE_CHUNKS__[id];return value;};if(known)return decodeExactChunk(known).then(activateIfCurrent);let promise=pendingLoads.get(id);if(!promise){const entry=manifestById.get(id);promise=new Promise((resolve,reject)=>{const element=document.createElement("script");element.src=entry.chunk;element.onload=async()=>{const value=window.__SKYSEED_SLICE_CHUNKS__[id];element.remove();if(!value){reject(new Error(`Slice chunk ${id} did not register`));return;}try{resolve(await decodeExactChunk(value));}catch(error){reject(error);}};element.onerror=()=>reject(new Error(`Unable to load offline Slice chunk ${entry.chunk}`));document.head.append(element);}).finally(()=>pendingLoads.delete(id));pendingLoads.set(id,promise);}return promise.then(activateIfCurrent);}
function updatePlaybackControl(){const button=document.getElementById("play-slices");button.textContent=playbackActive?"Pause":"Play";button.setAttribute("aria-pressed",String(playbackActive));document.body.dataset.slicePlaybackState=playbackActive?"playing":"paused";}
function pausePlayback(){playbackActive=false;playbackGeneration++;if(playbackTimer!==null)clearTimeout(playbackTimer);playbackTimer=null;updatePlaybackControl();}
function schedulePlayback(generation){if(!playbackActive||generation!==playbackGeneration)return;if(currentIndex>=orderedIds.length-1){pausePlayback();return;}const delay=Number(document.getElementById("playback-speed").value)||1000;playbackTimer=setTimeout(async()=>{if(!playbackActive||generation!==playbackGeneration)return;try{await loadSlice(currentIndex+1);schedulePlayback(generation);}catch(error){pausePlayback();document.getElementById("slice-label").textContent=`FAILED: ${error.message}`;}},delay);}
function playSlices(){if(playbackActive){pausePlayback();return;}if(currentIndex>=orderedIds.length-1){const firstReal=orderedIds.findIndex(id=>id===1);currentIndex=firstReal>=0?firstReal:0;}playbackActive=true;const generation=++playbackGeneration;updatePlaybackControl();loadSlice(currentIndex).then(()=>schedulePlayback(generation)).catch(error=>{pausePlayback();document.getElementById("slice-label").textContent=`FAILED: ${error.message}`;});}
function selectSlice(index,{keepPlayback=false}={}){const resume=keepPlayback&&playbackActive;pausePlayback();return loadSlice(index).then(()=>{if(resume){playbackActive=true;const generation=++playbackGeneration;updatePlaybackControl();schedulePlayback(generation);}});}
function gatewayCandidatesForZone(zoneId){const target=`Z|${zoneId}`,assignment=assignmentByZone(zoneId),candidates=[...new Set(currentSlice.routes.filter(([source,routeTarget])=>routeTarget===target&&staticNodes.get(source)?.kind==="ground-station").map(([source])=>source))].sort();return routeDirection==="gateway-to-zone"?candidates:candidates.filter(gateway=>assignment&&currentSlice.routes.some(([source,routeTarget])=>source===assignment.service_satellite_id&&routeTarget===`G|${gateway}`));}
function applyGatewayRouteSelection(chooseDefault=false){if(!routeZoneId)return;const candidates=gatewayCandidatesForZone(routeZoneId);if(chooseDefault&&!candidates.includes(routeGatewayId))routeGatewayId=candidates[0]||"";const assignment=assignmentByZone(routeZoneId);if(!routeGatewayId||!candidates.includes(routeGatewayId)||!assignment){routeSourceId="";routeTargetRef="";return;}if(routeDirection==="zone-to-gateway"){routeSourceId=assignment.service_satellite_id;routeTargetRef=`G|${routeGatewayId}`;}else{routeSourceId=routeGatewayId;routeTargetRef=`Z|${routeZoneId}`;}}
function addSliceContextMetric(root,label,value){const item=document.createElement("div"),name=document.createElement("span"),strong=document.createElement("strong");item.className="slice-context-metric";name.textContent=label;strong.textContent=value;item.append(name,strong);root.append(item);}
const contextPercent=(count,total)=>total?`${(100*count/total).toFixed(1)}%`:"n/a";
const contextNumber=(value,digits=1)=>value==null?"n/a":Number(value).toFixed(digits);
function renderSliceContext(){const root=document.getElementById("slice-context"),physical=document.getElementById("slice-physical-context"),summary=currentSlice?.summary;root.replaceChildren();if(!summary?.state_available){root.dataset.stateAvailable="false";addSliceContextMetric(root,"Dynamic state","Slice 0 · no materialized state");physical.textContent="Slice 0 is the formal empty predecessor for Difference production.";return;}root.dataset.stateAvailable="true";root.dataset.servedZones=String(summary.served_zones);root.dataset.unservedZones=String(summary.unserved_zones);root.dataset.coveredGroundStations=String(summary.covered_ground_stations);root.dataset.routeEntries=String(summary.route_entries);addSliceContextMetric(root,"Served Zones",`${summary.served_zones} / ${summary.total_zones} · ${contextPercent(summary.served_zones,summary.total_zones)}`);addSliceContextMetric(root,"Unserved Zones",`${summary.unserved_zones} / ${summary.total_zones} · ${contextPercent(summary.unserved_zones,summary.total_zones)}`);addSliceContextMetric(root,"Service Satellites",String(summary.distinct_service_satellites));addSliceContextMetric(root,"Covered GroundStations",`${summary.covered_ground_stations} / ${summary.total_ground_stations} · ${contextPercent(summary.covered_ground_stations,summary.total_ground_stations)}`);const connections=summary.connection_counts;addSliceContextMetric(root,"Active Connections",`${summary.connections} total · ${connections.same_orbit_isl} same-orbit · ${connections.cross_plane_isl} cross-plane · ${connections.ground_station_access} GS`);addSliceContextMetric(root,"Published Route entries",String(summary.route_entries));const geometry=summary.zone_service_geometry,elevation=geometry.elevation_deg,surface=geometry.surface_separation_km,slant=geometry.slant_distance_km;physical.textContent=`Zone → service-Satellite · elevation p5 ${contextNumber(elevation.p5)}° / median ${contextNumber(elevation.median)}° · surface separation p50 ${contextNumber(surface.median)} / p95 ${contextNumber(surface.p95)} / max ${contextNumber(surface.max)} km · ECEF slant p50 ${contextNumber(slant.median)} / p95 ${contextNumber(slant.p95)} / max ${contextNumber(slant.max)} km.`;}
function activateSlice(value){currentSlice=value;window.__SKYSEED_SLICE_CHUNKS__[value.slice_id]=value;for(const key of Object.keys(window.__SKYSEED_SLICE_CHUNKS__))if(Number(key)!==Number(value.slice_id))delete window.__SKYSEED_SLICE_CHUNKS__[key];document.getElementById("slice-label").textContent=`S${value.slice_id} · ${value.timestamp||"synthetic"}`;renderSliceContext();applyGatewayRouteSelection(false);populateFallbacks();refreshSelectedObject();restoreSelectedPath();renderDifference();renderDiagnostics();draw();}
function populateFallbacks(){const positions=currentSlice.positions||[],zones=MANIFEST.topology.zones.map(z=>[z.zone_id,z.zone_id]);replaceOptions(document.getElementById("gateway-route-zone"),zones,"Select Zone",routeZoneId);const gateways=routeZoneId?gatewayCandidatesForZone(routeZoneId):[];replaceOptions(document.getElementById("gateway-route-gateway"),gateways.map(id=>[shortNodeLabel(id),id]),gateways.length?"Select relevant Gateway":"No published Gateway",gateways.includes(routeGatewayId)?routeGatewayId:"");document.getElementById("gateway-route-direction").value=routeDirection;replaceOptions(document.getElementById("node-select"),positions.map(p=>[p.node_id,p.node_id]),"Locate node",selectedNodeId);replaceOptions(document.getElementById("connection-select"),currentSlice.connections.map(c=>[c.connection_ref,c.connection_ref]),"Locate Connection",selectedConnectionRef);replaceOptions(document.getElementById("zone-select"),zones,"Locate Zone",selectedZoneId);const sources=[...new Set(currentSlice.routes.map(route=>route[0]))].sort();replaceOptions(document.getElementById("route-source"),sources.map(v=>[v,v]),"Locate source",routeSourceId);const targets=routeSourceId?[...new Set(currentSlice.routes.filter(route=>route[0]===routeSourceId).map(route=>route[1]))].sort():[];replaceOptions(document.getElementById("route-target"),targets.map(v=>[v,v]),"Locate target",routeTargetRef);}
function detailRows(values){const list=document.createElement("dl");list.className="detail-grid";for(const [label,value] of values){const dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=label;dd.textContent=String(value);list.append(dt,dd);}return list;}
function actionButton(id,label,action){const button=document.createElement("button");button.id=id;button.type="button";button.textContent=label;button.addEventListener("click",action);return button;}
function setObjectDetail(kind,status,rows,actions=[]){const root=document.getElementById("selected-detail");root.dataset.selectionKind=kind;root.dataset.selectionStatus=status;root.replaceChildren();const badge=document.createElement("span");badge.className=`object-status${status==="absent"?" absent":""}`;badge.textContent=status==="present"?`present in Slice ${currentSlice.slice_id}`:`absent in Slice ${currentSlice.slice_id}`;root.append(badge,detailRows(rows));if(actions.length){const box=document.createElement("div");box.className="detail-actions";box.append(...actions);root.append(box);}}
function switchVisualSelection(kind){if(kind!=="node"){selectedNodeId="";showSatelliteZones=false;}if(kind!=="connection")selectedConnectionRef="";if(kind!=="zone")selectedZoneId="";activeSelectionKind=kind;}
function clearActiveSelection(){const clearedNode=activeSelectionKind==="node"?selectedNodeId:"",clearedZone=activeSelectionKind==="zone"?selectedZoneId:"",routeDependsOnSelection=(clearedNode&&(routeGatewayId===clearedNode||routeSourceId===clearedNode))||(clearedZone&&routeZoneId===clearedZone);selectedNodeId="";selectedConnectionRef="";selectedZoneId="";showSatelliteZones=false;activeSelectionKind="";const root=document.getElementById("selected-detail");root.dataset.selectionKind="";root.dataset.selectionStatus="none";root.innerHTML='<p class="muted">Click an object on Dynamic Earth.</p>';if(routeDependsOnSelection){routeZoneId="";routeGatewayId="";routeSourceId="";routeTargetRef="";populateFallbacks();restoreSelectedPath();}else{renderDifferenceContext();draw();}}
function clearInspection(){selectedNodeId="";selectedConnectionRef="";selectedZoneId="";activeSelectionKind="";showSatelliteZones=false;routeZoneId="";routeGatewayId="";routeSourceId="";routeTargetRef="";selectedPath=[];routeStatus="none";differenceMode=false;document.getElementById("show-difference").checked=false;document.getElementById("difference-panel").open=false;const root=document.getElementById("selected-detail");root.dataset.selectionKind="";root.dataset.selectionStatus="none";root.innerHTML='<p class="muted">Click an object on Dynamic Earth.</p>';populateFallbacks();restoreSelectedPath();renderDifferenceContext();draw();}
function currentNodeFacts(id){const connections=currentSlice.connections.filter(value=>value.connection_id.endpoint_a===id||value.connection_id.endpoint_b===id),isl=connections.filter(value=>value.connection_id.kind==="inter-satellite"),access=connections.filter(value=>value.connection_id.kind==="ground-station-access"),zones=servedZonesForSatellite(id);return {connections,isl,access,zones};}
function zoneServiceGeometry(_zone,assignment){if(!assignment)return null;const explanation=assignment.display_explanation;return {surface_separation_km:explanation.surface_separation_km,slant_distance_km:explanation.slant_distance_km};}
function showNode(id){if(!id)return;selectedNodeId=id;activeSelectionKind="node";const position=(currentSlice.positions||[]).find(value=>value.node_id===id),node=staticNodes.get(id),facts=currentNodeFacts(id),actions=[];if(node?.kind==="ground-station"&&routeZoneId&&gatewayCandidatesForZone(routeZoneId).includes(id))actions.push(actionButton("gateway-from-selection","Use as selected Zone's Gateway",()=>setGatewayRouteGateway(id)));if(node?.kind==="satellite")actions.push(actionButton("toggle-served-zones",showSatelliteZones?"Hide served Zones":"Highlight served Zones",()=>{showSatelliteZones=!showSatelliteZones;showNode(id);}));const rows=[["node_id",id],["kind",node?.kind||"unknown"]];if(node?.kind==="satellite")rows.push(["Orbit",node.orbit_ref],["Connection degree",facts.connections.length],["ISL degree",facts.isl.length],["GroundStation-access degree",facts.access.length],["served Zones",facts.zones.length]);else rows.push(["access degree",facts.access.length],["connected Satellites",facts.access.map(value=>value.connection_id.endpoint_a===id?value.connection_id.endpoint_b:value.connection_id.endpoint_a).join(", ")||"none"]);if(position)rows.push(["latitude / longitude",`${position.geographic.latitude_deg.toFixed(5)}° / ${position.geographic.longitude_deg.toFixed(5)}°`],["height",`${position.geographic.ellipsoidal_height_m.toFixed(1)} m`]);setObjectDetail("node",position?"present":"absent",rows,actions);document.getElementById("node-select").value=id;renderDifferenceContext();draw();}
function showConnection(ref){if(!ref)return;selectedConnectionRef=ref;activeSelectionKind="connection";const value=connectionByRef(ref),difference=currentSlice.difference_from_previous,removed=difference?.removed_connection_ids.find(item=>item.connection_ref===ref),identity=value?.connection_id||removed?.connection_id||{kind:ref.split("|")[0],endpoint_a:ref.split("|")[1]||"unknown",endpoint_b:ref.split("|")[2]||"unknown"},rows=[["connection",ref],["kind",identity.kind],["endpoint A",identity.endpoint_a],["endpoint B",identity.endpoint_b]];if(value)rows.push(["A → B delay / bandwidth / loss",`${value.endpoint_a_to_b.delay_us} μs / ${value.endpoint_a_to_b.bandwidth_bps} bps / ${value.endpoint_a_to_b.packet_loss_ppm} ppm`],["B → A delay / bandwidth / loss",`${value.endpoint_b_to_a.delay_us} μs / ${value.endpoint_b_to_a.bandwidth_bps} bps / ${value.endpoint_b_to_a.packet_loss_ppm} ppm`]);else rows.push(["current state",removed?"removed since previous Slice":"not present in current Slice"]);const actions=[actionButton("select-endpoint-a","Inspect endpoint A",()=>showNode(identity.endpoint_a)),actionButton("select-endpoint-b","Inspect endpoint B",()=>showNode(identity.endpoint_b))];setObjectDetail("connection",value?"present":"absent",rows,actions);populateFallbacks();renderDifferenceContext();draw();}
function showZone(id){if(!id)return;selectedZoneId=id;activeSelectionKind="zone";if(routeZoneId!==id){routeZoneId=id;routeGatewayId="";}applyGatewayRouteSelection(false);const zone=zonesById.get(id),assignment=assignmentByZone(id),serviceCount=assignment?servedZonesForSatellite(assignment.service_satellite_id).length:0,serviceGeometry=zoneServiceGeometry(zone,assignment),g=zone.geometry,rows=[["zone_id",id],["state",assignment?"served":"unserved"],["service Satellite",assignment?.service_satellite_id||"none"],["current representative-point elevation",assignment?`${assignment.display_explanation.representative_point_elevation_deg.toFixed(2)}°`:"n/a"],["approximate surface separation",serviceGeometry?`${serviceGeometry.surface_separation_km.toFixed(1)} km (Tool-derived sphere)`:"n/a"],["slant distance",serviceGeometry?`${serviceGeometry.slant_distance_km.toFixed(1)} km (formal ECEF-derived)`:"n/a"],["Zones served by that Satellite",assignment?serviceCount:"n/a"],["other Zones served by that Satellite",assignment?Math.max(0,serviceCount-1):"n/a"],["service eligibility","representative point only; full-area coverage is not guaranteed"],["shared service","multiple Zones may validly share one service Satellite"],["bounds",`${g.south_latitude_deg}, ${g.west_longitude_deg} → ${g.north_latitude_deg}, ${g.east_longitude_deg}`],["representative",`${zone.representative_point.latitude_deg}°, ${zone.representative_point.longitude_deg}°`],["priority",zone.priority]],actions=[];if(assignment)actions.push(actionButton("inspect-service-satellite","Inspect service Satellite",()=>showNode(assignment.service_satellite_id)));setObjectDetail("zone","present",rows,actions);populateFallbacks();restoreSelectedPath();renderDifferenceContext();draw();}
function refreshSelectedObject(){if(activeSelectionKind==="node"&&selectedNodeId)showNode(selectedNodeId);else if(activeSelectionKind==="connection"&&selectedConnectionRef)showConnection(selectedConnectionRef);else if(activeSelectionKind==="zone"&&selectedZoneId)showZone(selectedZoneId);}
function setGatewayRouteGateway(id){routeGatewayId=id;applyGatewayRouteSelection(false);populateFallbacks();restoreSelectedPath();}
function setGatewayRouteDirection(direction){routeDirection=direction;applyGatewayRouteSelection(false);populateFallbacks();restoreSelectedPath();}
function setRouteSource(id){routeZoneId="";routeGatewayId="";routeSourceId=id;populateFallbacks();restoreSelectedPath();}
function setRouteTarget(ref){routeZoneId="";routeGatewayId="";routeTargetRef=ref;populateFallbacks();restoreSelectedPath();}
function targetTerminal(ref){if(ref.startsWith("G|"))return ref.slice(2);const assignment=assignmentByZone(ref.slice(2));return assignment?.service_satellite_id||null;}
function routeEvidenceRows(assignment){if(!routeZoneId||!routeGatewayId||!assignment||selectedPath.length<2)return [];const terminal=assignment.service_satellite_id,ingress=routeDirection==="gateway-to-zone"?selectedPath[1]:selectedPath.at(-2);let sameOrbit=0,crossOrbit=0;for(let index=1;index<selectedPath.length;index++){const first=staticNodes.get(selectedPath[index-1]),second=staticNodes.get(selectedPath[index]);if(first?.kind!=="satellite"||second?.kind!=="satellite")continue;if(first.orbit_ref===second.orbit_ref)sameOrbit++;else crossOrbit++;}const direct=currentSlice.connections.some(value=>value.connection_id.kind==="ground-station-access"&&new Set([value.connection_id.endpoint_a,value.connection_id.endpoint_b]).has(routeGatewayId)&&new Set([value.connection_id.endpoint_a,value.connection_id.endpoint_b]).has(terminal)),terminalAccess=currentSlice.connections.filter(value=>value.connection_id.kind==="ground-station-access"&&(value.connection_id.endpoint_a===terminal||value.connection_id.endpoint_b===terminal)).map(value=>value.connection_id.endpoint_a===terminal?value.connection_id.endpoint_b:value.connection_id.endpoint_a);return [["Gateway ingress Satellite",ingress],["Zone terminal Satellite",terminal],["formal path composition",`${sameOrbit} same-Orbit ISL / ${crossOrbit} cross-Orbit ISL / 1 GroundStation access`],["direct Gateway/terminal formal access",direct?"selected":"not selected; geometric eligibility/rank is not a retained Slice fact"],["terminal GS-access occupancy",terminalAccess.length?`${terminalAccess.length}: ${terminalAccess.join(", ")}`:"none"],...(!direct&&terminalAccess.length?[["allocation context","terminal access capacity is occupied by the listed formal GroundStations; this Tool does not recompute ConnectionModel candidates"]]:[])];}
function restoreSelectedPath(){const detail=document.getElementById("path-detail");selectedPath=[];routeStatus="none";const primaryRows=routeZoneId?[["Zone",routeZoneId],["Gateway",routeGatewayId||"none"],["direction",routeDirection==="zone-to-gateway"?"Zone → Gateway":"Gateway → Zone"]]:[];if(!routeSourceId||!routeTargetRef){const candidates=routeZoneId?gatewayCandidatesForZone(routeZoneId):[];detail.dataset.routeStatus="none";detail.replaceChildren(detailRows([...primaryRows,["state",routeZoneId&&routeGatewayId&&!candidates.includes(routeGatewayId)?"selected Gateway is not published for this Zone in the current Slice":routeZoneId&&!candidates.length?"no relevant/published Gateway for this Zone in the current Slice":"select a Zone, relevant Gateway, and direction"]]));draw();return;}const terminal=targetTerminal(routeTargetRef),routes=new Map(currentSlice.routes.map(([source,target,next])=>[`${source}\u0000${target}`,next]));if(!terminal){routeStatus="target-unserved";detail.dataset.routeStatus=routeStatus;detail.replaceChildren(detailRows([...primaryRows,["source",routeSourceId],["target",routeTargetRef],["state","Zone is not currently served"]]));renderDifferenceContext();draw();return;}const seen=new Set([routeSourceId]);let current=routeSourceId;selectedPath=[routeSourceId];while(current!==terminal){const next=routes.get(`${current}\u0000${routeTargetRef}`);if(!next){selectedPath=[];routeStatus="absent";detail.dataset.routeStatus=routeStatus;detail.replaceChildren(detailRows([...primaryRows,["source",routeSourceId],["target",routeTargetRef],["terminal",terminal],["state","no complete published route in this Slice"]]));renderDifferenceContext();draw();return;}if(seen.has(next)){selectedPath=[];routeStatus="invalid";detail.dataset.routeStatus=routeStatus;detail.textContent="Projected formal next-hop chain contains a loop.";draw();return;}seen.add(next);selectedPath.push(next);current=next;}routeStatus="present";detail.dataset.routeStatus=routeStatus;const assignment=routeZoneId?assignmentByZone(routeZoneId):null;detail.replaceChildren(detailRows([...primaryRows,...(routeZoneId?[["Zone edge",`representative point ↔ ${assignment?.service_satellite_id||"unserved"}`]]:[]),["source",routeSourceId],["target",routeTargetRef],["terminal",terminal],["hop count",selectedPath.length-1],...routeEvidenceRows(assignment),["path",selectedPath.join(" → ")],["authority","browser display restoration from current compact next-hop tuples"]]));const actions=document.createElement("div");actions.className="detail-actions";actions.append(actionButton("clear-route","Clear Route",()=>{routeZoneId="";routeGatewayId="";routeSourceId="";routeTargetRef="";populateFallbacks();restoreSelectedPath();}));detail.append(actions);renderDifferenceContext();draw();}
function renderDifference(){const difference=currentSlice.difference_from_previous,counts=document.getElementById("difference-counts");if(!difference){counts.textContent="Slice 0 has no predecessor.";renderDifferenceContext();return;}const summary=currentSlice.summary.difference;counts.textContent=`+${summary.connection_additions} / −${summary.connection_removals} Connections · ${summary.connection_parameter_patches} parameter · ${summary.zone_patches} Zone · ${summary.route_patches} Route patches`;renderDifferenceContext();}
function renderDifferenceContext(){const root=document.getElementById("difference-context"),d=currentSlice?.difference_from_previous;root.replaceChildren();if(!d){root.innerHTML='<p class="muted">Slice 0 has no predecessor.</p>';return;}const rows=[];if(selectedConnectionRef){const added=d.added_connections.some(value=>value.connection_ref===selectedConnectionRef),removed=d.removed_connection_ids.some(value=>value.connection_ref===selectedConnectionRef),parameters=d.connection_parameter_patches.filter(value=>value.connection_ref===selectedConnectionRef);rows.push(["selected Connection",added?"added":removed?"removed":parameters.length?`parameter changed (${parameters.length} directions)`:"unchanged"]);}if(selectedZoneId){const patch=d.zone_service_assignment_patches.find(value=>value.zone_id===selectedZoneId);rows.push(["selected Zone",patch?`${patch.operation} → ${patch.service_satellite_id||"unserved"}`:"unchanged"]);}if(routeSourceId&&routeTargetRef){const patches=d.route_patches.filter(value=>value[1]===routeSourceId&&value[2]===routeTargetRef),counts={added:0,changed:0,removed:0};for(const patch of patches)counts[patch[0]]=(counts[patch[0]]||0)+1;rows.push(["selected Route",patches.length?`${patches.length} patches: +${counts.added||0} / ~${counts.changed||0} / −${counts.removed||0}`:"unchanged"]);}if(!rows.length)rows.push(["context","Select a Connection, Zone, or Route to inspect its Difference."]);root.append(detailRows(rows));}
function renderDiagnostics(){const root=document.getElementById("diagnostics");root.replaceChildren();if(!currentSlice.diagnostics.length){const p=document.createElement("p");p.className="muted";p.textContent="No current anomalies.";root.append(p);return;}for(const item of currentSlice.diagnostics){const p=document.createElement("p");p.className="diagnostic";p.textContent=`${item.kind}: ${item.node_id} — ${item.message}`;root.append(p);}}
for(const button of document.querySelectorAll("[data-pick-mode]"))button.addEventListener("click",()=>setPickMode(button.dataset.pickMode));
document.getElementById("clear-inspection").addEventListener("click",clearInspection);
document.getElementById("gateway-route-zone").addEventListener("change",event=>showZone(event.target.value));document.getElementById("gateway-route-gateway").addEventListener("change",event=>setGatewayRouteGateway(event.target.value));document.getElementById("gateway-route-direction").addEventListener("change",event=>setGatewayRouteDirection(event.target.value));document.getElementById("node-select").addEventListener("change",event=>showNode(event.target.value));document.getElementById("connection-select").addEventListener("change",event=>showConnection(event.target.value));document.getElementById("zone-select").addEventListener("change",event=>showZone(event.target.value));document.getElementById("route-source").addEventListener("change",event=>setRouteSource(event.target.value));document.getElementById("route-target").addEventListener("change",event=>setRouteTarget(event.target.value));
document.getElementById("play-slices").addEventListener("click",playSlices);document.getElementById("playback-speed").addEventListener("change",()=>{if(playbackActive){const generation=++playbackGeneration;if(playbackTimer!==null)clearTimeout(playbackTimer);schedulePlayback(generation);}});range.addEventListener("input",event=>selectSlice(Number(event.target.value),{keepPlayback:true}));document.getElementById("previous-slice").addEventListener("click",()=>selectSlice(currentIndex-1));document.getElementById("next-slice").addEventListener("click",()=>selectSlice(currentIndex+1));document.getElementById("show-difference").addEventListener("change",event=>{differenceMode=event.target.checked;document.getElementById("difference-panel").open=differenceMode;draw();});
updatePlaybackControl();
loadSlice(currentIndex).catch(error=>{document.getElementById("slice-label").textContent=`FAILED: ${error.message}`;});
"""
    )
    return standalone_page(
        title="Slice / Difference Inspection",
        eyebrow="Slice / Difference inspection",
        body=body,
        script=script,
        language_strings={
            "Tool Overview": "工具总览",
            "Slice / Difference timeline": "Slice / Difference 时间线",
            "Each Slice is an exact formal sample at its timestamp. Difference records the change from the previous sample; the globe restores only retained sampled positions, Connections, assignments, and Routes.": "每个 Slice 都是其 timestamp 上的精确正式样本。Difference 记录相对上一样本的变化；globe 只恢复已保留的采样位置、Connection、assignment 和 Route。",
            "Previous": "上一个",
            "Next": "下一个",
            "Play": "播放",
            "Pause": "暂停",
            "Speed": "速度",
            "Dynamic Earth": "动态 Earth",
            "Selected object": "所选对象",
            "Zone / Gateway Route": "Zone / Gateway Route",
            "Difference from previous Slice": "与上一 Slice 的 Difference",
            "Diagnostics": "诊断",
            "No current anomalies.": "当前无异常。",
            **_model_context_language_strings(data),
        },
    )


def _render_model_context(data: dict[str, Any]) -> str:
    context = data.get("model_context")
    if not isinstance(context, dict):
        return ""
    groups: list[str] = []
    models = context.get("models")
    if isinstance(models, dict):
        for key, title in (
            ("position", "Position model"),
            ("connection", "Connection model"),
            ("route", "Route model"),
        ):
            model = models.get(key)
            if not isinstance(model, dict):
                continue
            groups.append(
                _model_context_group(
                    title,
                    (
                        ("selected", model.get("selected")),
                        ("determines", model.get("role")),
                        ("configuration", model.get("configuration")),
                    ),
                )
            )
    shells = context.get("satellite_shells")
    if isinstance(shells, list) and shells:
        for shell_index, shell in enumerate(shells, start=1):
            if not isinstance(shell, dict):
                continue
            orbit_shape = f"{shell['orbit_count']} Orbits"
            if shell.get("satellites_per_orbit") is not None:
                orbit_shape += f" × {shell['satellites_per_orbit']} Satellites"
            groups.append(
                _model_context_group(
                    (
                        "Satellite shell"
                        if len(shells) == 1
                        else f"Satellite shell {shell_index}"
                    ),
                    (
                        ("Satellites", shell["satellite_count"]),
                        ("layout", orbit_shape),
                        ("altitude", _unit(shell.get("altitude_km"), "km")),
                        (
                            "inclination",
                            _unit(shell.get("inclination_deg"), "°"),
                        ),
                    ),
                )
            )
    zone_service = context.get("zone_service")
    if isinstance(zone_service, dict):
        groups.append(
            _model_context_group(
                "Zone service",
                (
                    ("Zones", context.get("zone_count", "n/a")),
                    (
                        "minimum elevation",
                        _unit(
                            zone_service.get("minimum_elevation_deg"), "°"
                        ),
                    ),
                    (
                        "Approx. service-envelope surface radius",
                        "≈ "
                        + _unit(
                            zone_service.get(
                                "approx_service_envelope_surface_radius_km"
                            ),
                            "km",
                        ),
                    ),
                    (
                        "authority",
                        zone_service.get(
                            "service_envelope_authority", "projected fact"
                        ),
                    ),
                ),
            )
        )
    ground_station_access = context.get("ground_station_access")
    if isinstance(ground_station_access, dict):
        groups.append(
            _model_context_group(
                "GroundStation access",
                (
                    (
                        "minimum elevation",
                        _unit(
                            ground_station_access.get(
                                "minimum_elevation_deg"
                            ),
                            "°",
                        ),
                    ),
                    (
                        "maximum slant distance",
                        _unit(
                            ground_station_access.get(
                                "maximum_slant_distance_km"
                            ),
                            "km",
                        ),
                    ),
                    (
                        "GroundStation capacity",
                        ground_station_access.get("ground_station_capacity"),
                    ),
                    (
                        "Satellite capacity",
                        ground_station_access.get("satellite_capacity"),
                    ),
                ),
            )
        )
    isl = context.get("isl")
    if isinstance(isl, dict):
        groups.append(
            _model_context_group(
                "ISL",
                (
                    ("same orbit", isl.get("same_orbit_policy")),
                    ("cross plane", isl.get("cross_plane_policy")),
                    (
                        "maximum distance",
                        _unit(isl.get("maximum_distance_km"), "km"),
                    ),
                    ("Earth clearance", isl.get("earth_clearance")),
                ),
            )
        )
    routing = context.get("routing")
    if isinstance(routing, dict):
        groups.append(
            _model_context_group(
                "Routing",
                (
                    ("model", routing.get("model")),
                    (
                        "Gateway candidates / Zone",
                        routing.get("gateway_candidates_per_zone"),
                    ),
                    ("metric", routing.get("metric")),
                ),
            )
        )
    notes = context.get("interpretation_notes")
    note_markup = ""
    if isinstance(notes, list):
        note_markup = (
            '<ul class="muted model-context-notes">'
            + "".join(f"<li>{escape(str(note))}</li>" for note in notes)
            + "</ul>"
        )
    if not groups:
        return ""
    return (
        '<details id="model-context" class="card">'
        "<summary>Current model / strategy · Why this Slice looks like this</summary>"
        '<p class="muted">Scenario/model values are projected at artifact '
        "generation. Approximate geometry is display-only and does not alter "
        "formal eligibility.</p>"
        f'<div class="model-context-grid">{"".join(groups)}</div>'
        f"{note_markup}</details>"
    )


def _model_context_group(
    title: str,
    rows: tuple[tuple[str, object], ...],
) -> str:
    visible = [row for row in rows if row[1] is not None]
    return (
        '<section class="model-context-group">'
        f"<h3>{escape(title)}</h3><dl class=\"detail-grid\">"
        + "".join(
            f"<dt>{escape(label)}</dt><dd>{escape(str(value))}</dd>"
            for label, value in visible
        )
        + "</dl></section>"
    )


def _unit(value: object, suffix: str) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value} {suffix}"


def _model_context_language_strings(data: dict[str, Any]) -> dict[str, str]:
    """Return UTF-8-safe prose translations for the projected model panel."""

    strings = {
        "Current model / strategy · Why this Slice looks like this": (
            "当前模型 / 策略 · 为什么这个 Slice 呈现为当前状态"
        ),
        "Scenario/model values are projected at artifact generation. Approximate geometry is display-only and does not alter formal eligibility.": (
            "场景与模型参数在产物生成时投影；近似几何只用于显示，不改变正式可用性判断。"
        ),
        "Position model": "Position 模型",
        "Connection model": "Connection 模型",
        "Route model": "Route 模型",
        "selected": "当前选择",
        "determines": "决定什么",
        "configuration": "当前配置",
        "Satellite shell": "卫星壳层",
        "Zone service": "Zone 服务",
        "GroundStation access": "GroundStation 接入",
        "Routing": "路由",
        "Satellites": "卫星数",
        "layout": "布局",
        "altitude": "高度",
        "inclination": "倾角",
        "Zones": "Zone 数",
        "minimum elevation": "最低仰角",
        "Approx. service-envelope surface radius": "近似服务包络地表半径",
        "authority": "证据属性",
        "maximum slant distance": "最大斜距",
        "GroundStation capacity": "GroundStation 容量",
        "Satellite capacity": "Satellite 容量",
        "same orbit": "同轨道",
        "cross plane": "跨轨道面",
        "maximum distance": "最大距离",
        "Earth clearance": "地球遮挡约束",
        "model": "模型",
        "Gateway candidates / Zone": "每个 Zone 的 Gateway 候选数",
        "metric": "度量",
    }
    context = data.get("model_context")
    models = context.get("models") if isinstance(context, dict) else None
    if isinstance(models, dict):
        role_translations = {
            "position": "在每个 Slice timestamp 计算精确的正式节点位置。",
            "connection": (
                "根据当前正式几何关系选择有效 ISL、GroundStation 接入和 Zone 服务事实。"
            ),
            "route": (
                "在有效 Connection 图上，为当前 Gateway/Zone 需求发布稀疏 next hop。"
            ),
        }
        for key, chinese in role_translations.items():
            value = models.get(key)
            if isinstance(value, dict) and isinstance(value.get("role"), str):
                strings[value["role"]] = chinese
    return strings


__all__ = ["render_slice_difference_page"]
