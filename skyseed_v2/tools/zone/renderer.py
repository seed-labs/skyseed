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
    zones = "".join(
        f'<option value="{escape(str(zone["zone_id"]))}">'
        f'{escape(str(zone["zone_id"]))}</option>'
        for zone in data["zones"]
    )
    body = f"""
<section class="card">
  <span class="status {status}">{status}</span>
  <h2>Formal Zone observation</h2>
  <p>Model: {escape(str(data['model_type']))}. {escape(policy)}</p>
  <p>physical matches and authoritative resolution are read from Topology;
  the browser does not recompute them.</p>
</section>
<section class="card">
  <div class="controls">
    <select id="zone-filter"><option value="*">all zones</option>{zones}</select>
    <select id="category-filter"><option value="*">all categories</option></select>
    <select id="status-filter">
      <option value="focus">focus</option><option value="all">all</option>
      <option value="anomalies">anomalies</option><option value="multi">multi</option>
    </select>
  </div>
  <p><span id="probe-visible">0</span> / <span id="probe-total">{counts['probes']}</span>
  · <span id="mode-label">focus</span></p>
  <canvas id="zone-globe" width="1000" height="520"></canvas>
  <svg id="zone-map" viewBox="0 0 1000 500"></svg>
</section>
<section class="card">
  <table><tbody id="probe-table"></tbody></table>
  <p>球体采用单位半径；矩形边界只是 display_boundary 采样，
  不声称是大圆线。切换 UI 不重新调用正式 API。</p>
</section>
"""
    script = f"""
const DATA={json_for_script(data)};
const canonicalLongitude=value=>((value+180)%360+360)%360-180;
const longitudeAt=(geometry,fraction)=>canonicalLongitude(
  geometry.west_longitude_deg+
  ((geometry.east_longitude_deg-geometry.west_longitude_deg+360)%360)*fraction
);
const spherePoint=(lat,lon,r=1)=>{{
  const a=lat*Math.PI/180,b=lon*Math.PI/180;
  return [r*Math.cos(a)*Math.cos(b),r*Math.sin(a),r*Math.cos(a)*Math.sin(b)];
}};
const stationVectors=DATA.ground_stations.map(
  station=>spherePoint(station.latitude_deg,station.longitude_deg,1.02)
);
const table=document.getElementById("probe-table");
DATA.probes.forEach(probe=>{{
  const row=document.createElement("tr");
  row.innerHTML=`<td>${{probe.probe_id}}</td><td>${{
    probe.physical_matches.map(value=>value.zone_id).join(", ")
  }}</td><td>${{
    probe.authoritative_result?probe.authoritative_result.zone_id:"none"
  }}</td>`;
  table.append(row);
}});
document.getElementById("probe-visible").textContent=String(DATA.probes.length);
const canvas=document.getElementById("zone-globe"),ctx=canvas.getContext("2d");
ctx.fillStyle="#17415f";ctx.beginPath();ctx.arc(500,260,190,0,Math.PI*2);ctx.fill();
stationVectors.forEach(point=>{{
  ctx.fillStyle="#ffd166";ctx.beginPath();
  ctx.arc(500+point[0]*190,260-point[1]*190,5,0,Math.PI*2);ctx.fill();
}});
DATA.zones.forEach(zone=>zone.display_boundary.forEach(point=>longitudeAt(
  zone.geometry,.5
)));
"""
    return standalone_page(
        title="Zone Tool：范围、Membership 与 Resolution",
        eyebrow="Zone observation",
        body=body,
        script=script,
    )


__all__ = ["render_zone_page"]
