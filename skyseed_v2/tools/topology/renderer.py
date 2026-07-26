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
<section class="card">
  <span class="status {status}">{status}</span>
  <h2>Topology 已创建</h2>
  <div class="warning">轨道图显示静态轨道平面，不表示任一时刻的卫星位置。</div>
</section>
<section><h2>物化数量</h2><div class="grid stats">{stats}</div></section>
<section class="grid two">
  <div class="card"><h2>静态对象树</h2><div id="tree"></div></div>
  <div class="card"><h2>对象详情</h2><pre id="detail"></pre></div>
</section>
<section class="card">
  <h2>静态轨道平面与 GroundStation 球面参考</h2>
  <button id="orbit-reset" type="button">重置视角</button>
  <canvas id="orbit-canvas" width="1100" height="560"></canvas>
  <p>Earth 球体采用单位半径。没有执行 Satellite propagation；
  static orbit ring 不是任一时刻的卫星位置。</p>
</section>
<section class="card">
  <h2>一致性摘要</h2>
  <table><tbody id="checks"></tbody></table>
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
const spherePoint=(lat,lon,r=1)=>{{
  const a=lat*Math.PI/180,b=lon*Math.PI/180;
  return [r*Math.cos(a)*Math.cos(b),r*Math.sin(a),r*Math.cos(a)*Math.sin(b)];
}};
const draw=()=>{{
  ctx.clearRect(0,0,canvas.width,canvas.height);
  ctx.fillStyle="#17415f";ctx.beginPath();
  ctx.arc(canvas.width/2,canvas.height/2,180,0,Math.PI*2);ctx.fill();
  DATA.ground_stations.forEach(gs=>{{
    const point=spherePoint(gs.latitude_deg,gs.longitude_deg,1.02);
    ctx.fillStyle="#ffd166";ctx.beginPath();
    ctx.arc(canvas.width/2+point[0]*180,canvas.height/2-point[1]*180,6,0,Math.PI*2);
    ctx.fill();
  }});
}};
document.getElementById("orbit-reset").onclick=draw;draw();
const checks=document.getElementById("checks");
DATA.consistency.checks.forEach(check=>{{
  const row=document.createElement("tr");
  row.innerHTML=`<td>${{check.label}}</td><td>${{check.status}}</td>`;
  checks.append(row);
}});
"""
    return standalone_page(
        title="Topology Tool：静态物化事实",
        eyebrow="Topology inspection",
        body=body,
        script=script,
    )


__all__ = ["render_topology_page"]
