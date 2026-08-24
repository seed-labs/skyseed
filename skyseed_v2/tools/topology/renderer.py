"""Render the Topology Tool's non-authoritative offline UI."""

from __future__ import annotations

from html import escape
from typing import Any

from .._html import json_for_script, standalone_page


def render_topology_page(data: dict[str, Any]) -> str:
    counts = data["counts"]
    consistency = data["consistency"]
    assert isinstance(counts, dict)
    assert isinstance(consistency, dict)
    status = str(data["status"])
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
    body = f"""
<style>
.globe-controls {{ display:flex; gap:9px; align-items:center; flex-wrap:wrap; margin-bottom:10px; }}
#orbit-canvas {{ touch-action:none; cursor:grab; }}
#orbit-canvas.dragging {{ cursor:grabbing; }}
</style>
<section class="card">
  <span class="status {status}">{status}</span>
  <h2>Topology materialized</h2>
  <div class="warning">The orbit view shows static orbit planes, not satellite positions at a particular time.</div>
</section>
<section><h2>Materialized inventory</h2><div class="grid stats">{stats}</div></section>
<section class="grid two">
  <div class="card"><h2>Static object tree</h2><div id="tree"></div></div>
  <div class="card"><h2>Object details</h2><pre id="detail"></pre></div>
</section>
<section class="card">
  <h2>Static orbit planes and GroundStation globe reference</h2>
  <div class="globe-controls"><button id="orbit-reset" type="button">Reset view</button>
  <span class="muted">Drag to rotate · orbit altitude is display-only scaled</span></div>
  <canvas id="orbit-canvas" width="1100" height="560"></canvas>
  <p>The Earth globe uses unit radius. No Satellite propagation is performed; a static orbit ring is not a satellite position at any time.</p>
</section>
<section class="card">
  <h2>Consistency summary</h2>
  <table><thead><tr><th>Check</th><th>Status</th></tr></thead><tbody id="checks"></tbody></table>
</section>
"""
    script = f"""
const DATA={json_for_script(data)};
const detail=value=>document.getElementById("detail").textContent=
  JSON.stringify(value,null,2);
const tree=document.getElementById("tree");
DATA.hierarchy.forEach(value=>{{
  const button=document.createElement("button");
  button.textContent=`AS${{value.asn}}`;button.onclick=()=>detail(value);
  tree.append(button);
}});
const canvas=document.getElementById("orbit-canvas"),ctx=canvas.getContext("2d");
const INITIAL_CAMERA={{yaw:-.55,pitch:.28}},MAX_PITCH=89*Math.PI/180;
let camera={{...INITIAL_CAMERA,drag:null}};
const spherePoint=(lat,lon,r=1)=>{{
  const a=lat*Math.PI/180,b=lon*Math.PI/180;
  return [r*Math.cos(a)*Math.cos(b),r*Math.sin(a),r*Math.cos(a)*Math.sin(b)];
}};
const orbitPoint=(ring,angle)=>{{
  const node=angle*Math.PI/180,inclination=ring.inclination_deg*Math.PI/180;
  const ascending=ring.right_ascension_deg*Math.PI/180;
  const radius=1+Math.min(.24,Number(ring.altitude_km)/6371);
  const x=radius*(Math.cos(ascending)*Math.cos(node)-Math.sin(ascending)*Math.sin(node)*Math.cos(inclination));
  const east=radius*(Math.sin(ascending)*Math.cos(node)+Math.cos(ascending)*Math.sin(node)*Math.cos(inclination));
  const north=radius*Math.sin(node)*Math.sin(inclination);
  return [x,north,east];
}};
const rotate=point=>{{
  const [x,y,z]=point,cy=Math.cos(camera.yaw),sy=Math.sin(camera.yaw);
  const cp=Math.cos(camera.pitch),sp=Math.sin(camera.pitch);
  const x1=x*cy+z*sy,z1=-x*sy+z*cy;
  return [x1,y*cp-z1*sp,y*sp+z1*cp];
}};
const scale=()=>Math.min(canvas.width,canvas.height)*.34;
const project=point=>{{const [x,y,z]=rotate(point),s=scale();return [canvas.width/2+x*s,canvas.height/2-y*s,z];}};
const drawPolyline=(points,color,width,front,dashed=false)=>{{
  ctx.save();ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dashed?[6,5]:[]);ctx.beginPath();
  let open=false,segments=0;
  for(const value of points){{const point=project(value),visible=front?point[2]>=0:point[2]<0;
    if(!visible){{open=false;continue;}}
    if(!open){{ctx.moveTo(point[0],point[1]);open=true;}}else{{ctx.lineTo(point[0],point[1]);segments++;}}
  }}
  ctx.stroke();ctx.restore();return segments;
}};
const drawEarth=()=>{{
  const radius=scale(),gradient=ctx.createRadialGradient(canvas.width*.45,canvas.height*.42,12,canvas.width/2,canvas.height/2,radius);
  gradient.addColorStop(0,"#27739d");gradient.addColorStop(1,"#08233a");
  ctx.fillStyle=gradient;ctx.beginPath();ctx.arc(canvas.width/2,canvas.height/2,radius,0,Math.PI*2);ctx.fill();
}};
const draw=()=>{{
  ctx.clearRect(0,0,canvas.width,canvas.height);
  let orbitSegments=0,gridSegments=0;
  DATA.orbit_rings.forEach((ring,index)=>{{
    const points=Array.from({{length:181}},(_,step)=>orbitPoint(ring,step*2));
    orbitSegments+=drawPolyline(points,`hsla(${{(index*47)%360}},70%,72%,.2)`,1,false,true);
  }});
  drawEarth();
  for(let latitude=-60;latitude<=60;latitude+=30){{
    const points=Array.from({{length:145}},(_,step)=>spherePoint(latitude,-180+step*2.5));
    gridSegments+=drawPolyline(points,"#9cd8ed55",1,true);
  }}
  for(let longitude=-150;longitude<180;longitude+=30){{
    const points=Array.from({{length:73}},(_,step)=>spherePoint(-90+step*2.5,longitude));
    gridSegments+=drawPolyline(points,"#9cd8ed55",1,true);
  }}
  DATA.orbit_rings.forEach((ring,index)=>{{
    const points=Array.from({{length:181}},(_,step)=>orbitPoint(ring,step*2));
    orbitSegments+=drawPolyline(points,`hsl(${{(index*47)%360}} 78% 72%)`,1.8,true);
  }});
  DATA.ground_stations.forEach(gs=>{{
    const point=project(spherePoint(gs.latitude_deg,gs.longitude_deg,1.015));
    if(point[2]<0)return;
    ctx.fillStyle="#ffd166";ctx.beginPath();
    ctx.arc(point[0],point[1],6,0,Math.PI*2);
    ctx.fill();
  }});
  canvas.dataset.cameraYaw=String(camera.yaw);canvas.dataset.cameraPitch=String(camera.pitch);
  canvas.dataset.orbitSegments=String(orbitSegments);canvas.dataset.gridSegments=String(gridSegments);
}};
document.getElementById("orbit-reset").onclick=()=>{{camera={{...INITIAL_CAMERA,drag:null}};draw();}};
canvas.addEventListener("pointerdown",event=>{{camera.drag=[event.clientX,event.clientY];canvas.classList.add("dragging");canvas.setPointerCapture(event.pointerId);}});
canvas.addEventListener("pointermove",event=>{{if(!camera.drag)return;camera.yaw+=(event.clientX-camera.drag[0])*.008;camera.pitch=Math.max(-MAX_PITCH,Math.min(MAX_PITCH,camera.pitch+(event.clientY-camera.drag[1])*.008));camera.drag=[event.clientX,event.clientY];draw();}});
canvas.addEventListener("pointerup",event=>{{camera.drag=null;canvas.classList.remove("dragging");if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);}});
canvas.addEventListener("pointercancel",()=>{{camera.drag=null;canvas.classList.remove("dragging");}});
draw();
const checks=document.getElementById("checks");
DATA.consistency.checks.forEach(check=>{{
  const row=document.createElement("tr");
  row.innerHTML=`<td>${{check.label}}</td><td>${{check.status}}</td>`;
  checks.append(row);
}});
"""
    return standalone_page(
        title="Topology Inspection",
        eyebrow="Topology inspection",
        body=body,
        script=script,
        language_strings={
            "Topology Inspection": "拓扑检查",
            "Topology inspection": "拓扑检查",
            "Topology materialized": "拓扑已物化",
            "The orbit view shows static orbit planes, not satellite positions at a particular time.": "轨道视图显示静态轨道平面，不表示任一时刻的卫星位置。",
            "Materialized inventory": "物化对象数量",
            "Static object tree": "静态对象树",
            "Object details": "对象详情",
            "Static orbit planes and GroundStation globe reference": "静态轨道平面与 GroundStation 球面参考",
            "Drag to rotate · orbit altitude is display-only scaled": "拖动旋转 · 轨道高度仅作显示比例缩放",
            "The Earth globe uses unit radius. No Satellite propagation is performed; a static orbit ring is not a satellite position at any time.": "Earth 球体采用单位半径；没有执行 Satellite propagation，静态轨道环不是任一时刻的卫星位置。",
            "Consistency summary": "一致性摘要",
            "Check": "检查项",
            "Status": "状态",
        },
    )


__all__ = ["render_topology_page"]
