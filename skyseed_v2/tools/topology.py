"""Collect and render the public static Topology as an offline page."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from skyseed_v2.simulator import Topology

from ._html import json_for_script, standalone_page, write_json, write_text


EARTH_DISPLAY_RADIUS_KM = 6371.0


def collect_topology_data(topology: Topology) -> dict[str, Any]:
    """Collect public owner tuples and facade identity checks."""

    if not isinstance(topology, Topology):
        raise TypeError("topology must be a Topology")

    checks: list[dict[str, object]] = []
    hierarchy: list[dict[str, object]] = []
    orbit_rings: list[dict[str, object]] = []
    ground_stations: list[dict[str, object]] = []

    counts = {
        "autonomous_systems": 0,
        "shells": 0,
        "orbits": 0,
        "satellites": 0,
        "ground_stations": 0,
        "zones": 0,
    }

    def check(label: str, condition: bool) -> None:
        checks.append({"label": label, "status": "PASS" if condition else "FAIL"})

    for autonomous_system in topology.autonomous_systems:
        counts["autonomous_systems"] += 1
        check(
            f"AS{autonomous_system.asn} owner/facade identity",
            topology.get_autonomous_system(autonomous_system.asn)
            is autonomous_system,
        )
        as_record: dict[str, object] = {
            "asn": autonomous_system.asn,
            "shells": [],
            "ground_stations": [],
        }
        shell_records = as_record["shells"]
        assert isinstance(shell_records, list)
        for shell in autonomous_system.shells:
            counts["shells"] += 1
            check(
                f"AS{autonomous_system.asn}/{shell.shell_id} owner/facade identity",
                autonomous_system.get_shell(shell.shell_id) is shell
                and topology.get_shell(autonomous_system.asn, shell.shell_id)
                is shell,
            )
            shell_record: dict[str, object] = {
                "shell_id": shell.shell_id,
                "asn": shell.asn,
                "orbits": [],
            }
            orbit_records = shell_record["orbits"]
            assert isinstance(orbit_records, list)
            for orbit in shell.orbits:
                counts["orbits"] += 1
                check(
                    (
                        f"AS{autonomous_system.asn}/{shell.shell_id}/"
                        f"{orbit.orbit_id} owner/facade identity"
                    ),
                    shell.get_orbit(orbit.orbit_id) is orbit
                    and topology.get_orbit(
                        autonomous_system.asn,
                        shell.shell_id,
                        orbit.orbit_id,
                    )
                    is orbit,
                )
                satellite_records = []
                for satellite in orbit.satellites:
                    counts["satellites"] += 1
                    check(
                        f"{satellite.node_id.value} owner/facade identity",
                        orbit.get_satellite(satellite.node_id) is satellite
                        and topology.get_satellite(satellite.node_id) is satellite
                        and topology.get_node(satellite.node_id) is satellite,
                    )
                    satellite_records.append(
                        {
                            "satellite_id": satellite.satellite_id,
                            "node_id": satellite.node_id.value,
                            "orbit_index": satellite.orbit_index,
                        }
                    )
                ring = {
                    "asn": orbit.asn,
                    "shell_id": orbit.shell_id,
                    "orbit_id": orbit.orbit_id,
                    "altitude_km": orbit.altitude_km,
                    "inclination_deg": orbit.inclination_deg,
                    "right_ascension_deg": orbit.right_ascension_deg,
                    "satellite_count": len(orbit.satellites),
                    "satellite_ids": [
                        satellite.satellite_id for satellite in orbit.satellites
                    ],
                }
                orbit_rings.append(ring)
                orbit_records.append({**ring, "satellites": satellite_records})
            shell_records.append(shell_record)

        ground_station_records = as_record["ground_stations"]
        assert isinstance(ground_station_records, list)
        for ground_station in autonomous_system.ground_stations:
            counts["ground_stations"] += 1
            check(
                f"{ground_station.node_id.value} owner/facade identity",
                autonomous_system.get_ground_station(ground_station.node_id)
                is ground_station
                and topology.get_ground_station(ground_station.node_id)
                is ground_station
                and topology.get_node(ground_station.node_id) is ground_station,
            )
            record = {
                "asn": autonomous_system.asn,
                "ground_station_id": ground_station.ground_station_id,
                "node_id": ground_station.node_id.value,
                "latitude_deg": ground_station.latitude_deg,
                "longitude_deg": ground_station.longitude_deg,
                "elevation_m": ground_station.elevation_m,
                "station_type": ground_station.station_type,
                "operator": ground_station.operator,
            }
            ground_stations.append(record)
            ground_station_records.append(record)
        hierarchy.append(as_record)

    try:
        zone_layout = topology.get_zone_layout()
    except RuntimeError:
        zone_layout = None
    zone_layout_configured = zone_layout is not None
    if zone_layout is not None:
        check(
            "Topology/ZoneLayout facade identity",
            topology.get_zone_layout() is zone_layout,
        )
        counts["zones"] = len(zone_layout.zones)
        for zone in zone_layout.zones:
            check(
                f"Zone {zone.zone_id} owner/facade identity",
                zone_layout.get_zone(zone.zone_id) is zone
                and topology.get_zone(zone.zone_id) is zone,
            )

    expected = {
        "autonomous_systems": len(topology.autonomous_systems),
        "shells": sum(
            len(autonomous_system.shells)
            for autonomous_system in topology.autonomous_systems
        ),
        "orbits": sum(
            len(shell.orbits)
            for autonomous_system in topology.autonomous_systems
            for shell in autonomous_system.shells
        ),
        "satellites": sum(
            len(orbit.satellites)
            for autonomous_system in topology.autonomous_systems
            for shell in autonomous_system.shells
            for orbit in shell.orbits
        ),
        "ground_stations": sum(
            len(autonomous_system.ground_stations)
            for autonomous_system in topology.autonomous_systems
        ),
        "zones": 0 if zone_layout is None else len(zone_layout.zones),
    }
    check("public tuple enumeration counts", counts == expected)
    status = (
        "PASS"
        if all(item["status"] == "PASS" for item in checks)
        else "FAIL"
    )
    return {
        "format_version": 1,
        "tool": "topology",
        "status": status,
        "topology_created": True,
        "zone_layout_configured": zone_layout_configured,
        "counts": counts,
        "hierarchy": hierarchy,
        "orbit_rings": orbit_rings,
        "ground_stations": ground_stations,
        "consistency": {
            "status": status,
            "checks": checks,
            "passed": sum(item["status"] == "PASS" for item in checks),
            "failed": sum(item["status"] == "FAIL" for item in checks),
        },
        "display_notes": {
            "orbit": (
                "轨道图显示静态轨道平面，不表示任一时刻的卫星位置。"
            ),
            "earth_radius": (
                "6371 km 只用于把 altitude_km 映射为相对轨道环半径，"
                "不属于 Simulator 轨道传播合同。"
            ),
        },
    }


def _render_page(data: dict[str, Any]) -> str:
    counts = data["counts"]
    assert isinstance(counts, dict)
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
    status = str(data["status"])
    layout = "已配置" if data["zone_layout_configured"] else "未配置"
    consistency = data["consistency"]
    assert isinstance(consistency, dict)
    body = f"""
<section class="card">
  <span class="status {status}">{status}</span>
  <h2>Topology 已创建 · consistency {status}</h2>
  <p>ZoneLayout：{layout}；一致性检查 {consistency['passed']} 通过 /
  {consistency['failed']} 失败。</p>
  <div class="warning"><strong>重要：</strong>
  轨道图显示静态轨道平面，不表示任一时刻的卫星位置。</div>
</section>
<section><h2>物化数量</h2><div class="grid stats">{stats}</div></section>
<section class="grid two">
  <div class="card">
    <h2>静态对象树</h2>
    <p class="muted">默认折叠；逐层展开，点击对象查看真实字段。</p>
    <div id="tree"></div>
  </div>
  <div class="card">
    <h2>对象详情</h2>
    <pre id="tree-detail" class="detail">点击 AS、Shell、Orbit、Satellite 或 GroundStation。</pre>
  </div>
</section>
<section class="card">
  <h2>静态轨道平面与 GroundStation 球面参考</h2>
  <p class="muted">拖动旋转；点击轨道环或 GroundStation 点查看公开字段。
  不调用 Timeline，不计算 timestamp position，不放置 Satellite 点。</p>
  <div class="controls"><button id="orbit-reset" type="button">重置视角</button></div>
  <canvas id="orbit-canvas" width="1100" height="560" aria-label="静态轨道平面"></canvas>
  <pre id="orbit-detail" class="detail">点击轨道环或 GroundStation 球面点。</pre>
  <p class="muted">Earth 球体采用单位半径，仅用于显示已有经纬度与经纬网；
  6371 km 只用于把 altitude_km 映射为相对 ring 半径，不属于 Simulator
  轨道传播合同。没有执行 Satellite propagation；static orbit ring
  不是任一时刻的卫星位置。</p>
</section>
<section class="card">
  <h2>GroundStation 离线经纬度视图</h2>
  <svg id="ground-map" viewBox="0 0 1000 500" role="img" aria-label="地面站经纬度世界视图"></svg>
  <pre id="ground-detail" class="detail">点击 GroundStation 点查看 NodeId、AS 与公开字段。</pre>
</section>
<section class="card">
  <h2>一致性摘要</h2>
  <p class="muted">比较 public tuple enumeration、owner lookup、Topology facade lookup 与对象 identity。</p>
  <table><thead><tr><th>检查</th><th>状态</th></tr></thead><tbody id="checks"></tbody></table>
</section>
"""
    script = f"""
const DATA={json_for_script(data)};
const detail=(id,value)=>document.getElementById(id).textContent=
  Object.entries(value).map(([k,v])=>`${{k}}: ${{Array.isArray(v)?v.join(", "):v}}`).join("\\n");

function objectButton(label, value) {{
  const button=document.createElement("button");
  button.textContent=label;
  button.addEventListener("click",()=>detail("tree-detail",value));
  return button;
}}
function branch(label, value, children) {{
  const node=document.createElement("details");
  const summary=document.createElement("summary");
  summary.append(objectButton(label,value));
  node.append(summary,...children);
  return node;
}}
const tree=document.getElementById("tree");
DATA.hierarchy.forEach(asValue=>{{
  const asChildren=[];
  asValue.ground_stations.forEach(gs=>asChildren.push(
    objectButton(`GroundStation ${{gs.ground_station_id}}`,gs)
  ));
  asValue.shells.forEach(shell=>{{
    const shellChildren=shell.orbits.map(orbit=>branch(
      `Orbit ${{orbit.orbit_id}} · ${{orbit.satellite_count}} Satellites`,
      {{
        asn:orbit.asn,shell_id:orbit.shell_id,orbit_id:orbit.orbit_id,
        altitude_km:orbit.altitude_km,inclination_deg:orbit.inclination_deg,
        right_ascension_deg:orbit.right_ascension_deg,
        satellite_count:orbit.satellite_count
      }},
      orbit.satellites.map(satellite=>objectButton(
        `Satellite ${{satellite.satellite_id}}`,satellite
      ))
    ));
    asChildren.push(branch(`Shell ${{shell.shell_id}}`,
      {{asn:shell.asn,shell_id:shell.shell_id,orbit_count:shell.orbits.length}},
      shellChildren));
  }});
  tree.append(branch(`AS${{asValue.asn}}`,
    {{asn:asValue.asn,shell_count:asValue.shells.length,
      ground_station_count:asValue.ground_stations.length}},asChildren));
}});

const canvas=document.getElementById("orbit-canvas");
const ctx=canvas.getContext("2d");
function initialViewForLocations(locations,fallback){{
  if(!locations.length)return fallback;
  const vector=locations.reduce((sum,value)=>{{
    const latitude=value.latitude_deg*Math.PI/180,longitude=value.longitude_deg*Math.PI/180;
    sum[0]+=Math.cos(latitude)*Math.cos(longitude);
    sum[1]+=Math.sin(latitude);
    sum[2]+=Math.cos(latitude)*Math.sin(longitude);
    return sum;
  }},[0,0,0]);
  if(Math.hypot(...vector)<1e-9)return fallback;
  return {{
    yaw:Math.atan2(vector[2],vector[0])-Math.PI/2,
    pitch:Math.atan2(vector[1],Math.hypot(vector[0],vector[2]))
  }};
}}
const initialView=initialViewForLocations(DATA.ground_stations,{{yaw:-0.55,pitch:0.32}});
let yaw=initialView.yaw,pitch=initialView.pitch,drag=null,ringPaths=[],stationPoints=[];
const rotate=([x,y,z])=>{{
  const cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
  const x1=x*cy+z*sy,z1=-x*sy+z*cy;
  return [x1,y*cp-z1*sp,y*sp+z1*cp];
}};
const project=(point,scale)=>{{
  const [x,y,z]=rotate(point); return [canvas.width/2+x*scale,canvas.height/2-y*scale,z];
}};
function line3(points,color,width,frontOnly=false) {{
  let open=false;
  ctx.strokeStyle=color;ctx.lineWidth=width;ctx.beginPath();
  points.forEach((point,index)=>{{
    const [x,y,z]=project(point,188);
    if(frontOnly&&z<0){{open=false;return;}}
    if(!open){{ctx.moveTo(x,y);open=true;}}else ctx.lineTo(x,y);
    if(index===points.length-1)ctx.stroke();
  }});
  ctx.stroke();
}}
function spherePoint(lat,lon,r=1) {{
  const a=lat*Math.PI/180,b=lon*Math.PI/180;
  return [r*Math.cos(a)*Math.cos(b),r*Math.sin(a),r*Math.cos(a)*Math.sin(b)];
}}
function ringPoints(ring) {{
  const inc=ring.inclination_deg*Math.PI/180,raan=ring.right_ascension_deg*Math.PI/180;
  const radius=1+ring.altitude_km/{EARTH_DISPLAY_RADIUS_KM};
  const u=[Math.cos(raan),0,Math.sin(raan)];
  const v=[-Math.sin(raan)*Math.cos(inc),Math.sin(inc),Math.cos(raan)*Math.cos(inc)];
  return Array.from({{length:181}},(_,i)=>{{
    const t=i*Math.PI*2/180;
    return u.map((n,j)=>radius*(n*Math.cos(t)+v[j]*Math.sin(t)));
  }});
}}
function draw() {{
  ctx.clearRect(0,0,canvas.width,canvas.height);
  const gradient=ctx.createRadialGradient(canvas.width*.43,canvas.height*.39,8,
    canvas.width/2,canvas.height/2,188);
  gradient.addColorStop(0,"#276c92");gradient.addColorStop(1,"#09263e");
  ctx.fillStyle=gradient;ctx.beginPath();ctx.arc(canvas.width/2,canvas.height/2,188,0,Math.PI*2);ctx.fill();
  for(let lat=-60;lat<=60;lat+=30)
    line3(Array.from({{length:73}},(_,i)=>spherePoint(lat,-180+i*5)),"#79b9d344",1,true);
  for(let lon=-150;lon<180;lon+=30)
    line3(Array.from({{length:37}},(_,i)=>spherePoint(-90+i*5,lon)),"#79b9d344",1,true);
  ringPaths=[];
  DATA.orbit_rings.forEach((ring,index)=>{{
    const points=ringPoints(ring);
    line3(points,`hsl(${{(index*83+175)%360}} 82% 67%)`,2.4);
    ringPaths.push({{ring,points:points.map(p=>project(p,188))}});
  }});
  stationPoints=[];
  DATA.ground_stations.forEach(gs=>{{
    const point=project(spherePoint(gs.latitude_deg,gs.longitude_deg,1.015),188);
    if(point[2]<0)return;
    ctx.fillStyle="#ffd166";ctx.strokeStyle="#07111f";ctx.lineWidth=3;
    ctx.beginPath();ctx.arc(point[0],point[1],7,0,Math.PI*2);ctx.fill();ctx.stroke();
    stationPoints.push({{groundStation:gs,point}});
  }});
  ctx.fillStyle="#b9d8e8";ctx.font="14px system-ui";
  ctx.fillText("N",canvas.width/2-5,canvas.height/2-174);
  ctx.fillText("拖动旋转 · 点击 ring / GroundStation",18,28);
}}
canvas.addEventListener("pointerdown",event=>{{drag=[event.clientX,event.clientY];canvas.setPointerCapture(event.pointerId);}});
canvas.addEventListener("pointermove",event=>{{
  if(!drag)return;yaw+=(event.clientX-drag[0])*.009;pitch=Math.max(-1.3,Math.min(1.3,pitch+(event.clientY-drag[1])*.009));
  drag=[event.clientX,event.clientY];draw();
}});
canvas.addEventListener("pointerup",()=>drag=null);
document.getElementById("orbit-reset").addEventListener("click",()=>{{
  yaw=initialView.yaw;pitch=initialView.pitch;draw();
}});
canvas.addEventListener("click",event=>{{
  if(drag)return;
  const rect=canvas.getBoundingClientRect(),x=(event.clientX-rect.left)*canvas.width/rect.width,
    y=(event.clientY-rect.top)*canvas.height/rect.height;
  let station=null,stationDistance=14;
  stationPoints.forEach(item=>{{
    const d=Math.hypot(item.point[0]-x,item.point[1]-y);
    if(d<stationDistance){{stationDistance=d;station=item.groundStation;}}
  }});
  if(station){{detail("orbit-detail",station);return;}}
  let best=null,distance=18;
  ringPaths.forEach(item=>item.points.forEach(point=>{{
    const d=Math.hypot(point[0]-x,point[1]-y);
    if(d<distance){{distance=d;best=item.ring;}}
  }}));
  if(best)detail("orbit-detail",best);
}});
draw();

const map=document.getElementById("ground-map");
const svg=(name,attrs)=>{{
  const node=document.createElementNS("http:"+"//www.w3.org/2000/svg",name);
  Object.entries(attrs).forEach(([key,value])=>node.setAttribute(key,value));return node;
}};
for(let lon=-180;lon<=180;lon+=30)map.append(svg("line",{{x1:(lon+180)/360*1000,y1:0,x2:(lon+180)/360*1000,y2:500,stroke:"#31526b","stroke-width":1}}));
for(let lat=-90;lat<=90;lat+=30)map.append(svg("line",{{x1:0,y1:(90-lat)/180*500,x2:1000,y2:(90-lat)/180*500,stroke:"#31526b","stroke-width":1}}));
DATA.ground_stations.forEach(gs=>{{
  const point=svg("circle",{{cx:(gs.longitude_deg+180)/360*1000,cy:(90-gs.latitude_deg)/180*500,r:8,fill:"#ffd166",stroke:"#07111f","stroke-width":3}});
  point.style.cursor="pointer";point.addEventListener("click",()=>detail("ground-detail",gs));map.append(point);
}});
const checks=document.getElementById("checks");
DATA.consistency.checks.forEach(check=>{{
  const row=document.createElement("tr"),label=document.createElement("td"),status=document.createElement("td");
  label.textContent=check.label;status.innerHTML=`<span class="status ${{check.status}}">${{check.status}}</span>`;
  row.append(label,status);checks.append(row);
}});
"""
    return standalone_page(
        title="Topology Tool：静态物化事实",
        eyebrow="Topology inspection",
        body=body,
        script=script,
    )


def write_topology_tool(topology: Topology, directory: Path) -> dict[str, object]:
    """Write topology JSON, summary and standalone page."""

    data = collect_topology_data(topology)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "topology.json", data)
    write_text(directory / "index.html", _render_page(data))
    counts = data["counts"]
    assert isinstance(counts, dict)
    consistency = data["consistency"]
    assert isinstance(consistency, dict)
    write_text(
        directory / "summary.txt",
        "\n".join(
            (
                f"status={data['status']}",
                "topology_created=true",
                f"zone_layout_configured={str(data['zone_layout_configured']).lower()}",
                *(f"{key}={value}" for key, value in counts.items()),
                f"consistency_passed={consistency['passed']}",
                f"consistency_failed={consistency['failed']}",
                (
                    "warning=轨道图显示静态轨道平面，"
                    "不表示任一时刻的卫星位置。"
                ),
                "",
            )
        ),
    )
    return {
        "status": data["status"],
        "message": (
            f"{counts['autonomous_systems']} AS / "
            f"{counts['orbits']} Orbit / {counts['satellites']} Satellite"
        ),
        "counts": counts,
    }
