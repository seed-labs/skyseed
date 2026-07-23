"""Evaluate deterministic Zone probes through the public Topology facade."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from skyseed_v2.simulator import Topology

from ._html import json_for_script, standalone_page, write_json, write_text
from .zone_probes import select_zone_probes


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _canonical_longitude(longitude_deg: float) -> float:
    canonical = (longitude_deg + 180.0) % 360.0 - 180.0
    return 0.0 if canonical == 0.0 else canonical


def _sample_rectangle_boundary(geometry: Any) -> list[list[float]]:
    """Sample the existing rectangle bounds for display, including wrapping."""

    longitude_steps = 32
    latitude_steps = 16
    span = (
        geometry.east_longitude_deg - geometry.west_longitude_deg
    ) % 360.0
    eastward = [
        _canonical_longitude(
            geometry.west_longitude_deg + span * step / longitude_steps
        )
        for step in range(longitude_steps + 1)
    ]
    north = [
        [geometry.north_latitude_deg, longitude] for longitude in eastward
    ]
    east = [
        [
            geometry.north_latitude_deg
            - (
                geometry.north_latitude_deg
                - geometry.south_latitude_deg
            )
            * step
            / latitude_steps,
            geometry.east_longitude_deg,
        ]
        for step in range(1, latitude_steps + 1)
    ]
    south = [
        [geometry.south_latitude_deg, longitude]
        for longitude in reversed(eastward[:-1])
    ]
    west = [
        [
            geometry.south_latitude_deg
            + (
                geometry.north_latitude_deg
                - geometry.south_latitude_deg
            )
            * step
            / latitude_steps,
            geometry.west_longitude_deg,
        ]
        for step in range(1, latitude_steps)
    ]
    return north + east + south + west


def _annotate_focus_visibility(probes: list[dict[str, object]]) -> None:
    """Mark a deterministic useful subset without removing any probe."""

    ordinary_by_zone: dict[str, list[dict[str, object]]] = {}
    for probe in probes:
        reasons: list[str] = []
        categories = tuple(str(value) for value in probe["categories"])
        origins = probe["origins"]
        assert isinstance(origins, list)
        physical_matches = probe["physical_matches"]
        assert isinstance(physical_matches, list)
        if probe["invariant_status"] == "FAIL":
            reasons.append("FAIL")
        if len(physical_matches) > 1:
            reasons.append("multi-match")
        if any(
            origin["origin_kind"] == "tool-selected relationship candidate"
            for origin in origins
        ):
            reasons.append("zone-pair-relation")
        if any("pole" in category for category in categories):
            reasons.append("pole")
        if any("dateline" in category for category in categories):
            reasons.append("dateline")
        if "representative-center" in categories:
            reasons.append("representative-center")
        probe["focus_reasons"] = reasons
        probe["focus_visible"] = bool(reasons)

        source_zone_ids = probe["source_zone_ids"]
        assert isinstance(source_zone_ids, list)
        if (
            not reasons
            and probe["invariant_status"] == "PASS"
            and len(source_zone_ids) == 1
        ):
            ordinary_by_zone.setdefault(str(source_zone_ids[0]), []).append(
                probe
            )

    def ordinary_key(probe: dict[str, object]) -> tuple[object, ...]:
        categories = tuple(str(value) for value in probe["categories"])
        category_rank = (
            0
            if "interior-grid" in categories
            else 1
            if any("edge-midpoint" in value for value in categories)
            else 2
        )
        return (
            category_rank,
            categories,
            float(probe["latitude_deg"]),
            float(probe["longitude_deg"]),
        )

    for zone_id in sorted(ordinary_by_zone):
        representatives = sorted(
            ordinary_by_zone[zone_id],
            key=ordinary_key,
        )[:2]
        for probe in representatives:
            probe["focus_visible"] = True
            reasons = probe["focus_reasons"]
            assert isinstance(reasons, list)
            reasons.append("deterministic-zone-sample")


def collect_zone_data(topology: Topology) -> dict[str, Any]:
    """Select probes, invoke both formal APIs, and check simple invariants."""

    if not isinstance(topology, Topology):
        raise TypeError("topology must be a Topology")
    ground_stations = [
        {
            "asn": autonomous_system.asn,
            "ground_station_id": ground_station.ground_station_id,
            "node_id": ground_station.node_id.value,
            "latitude_deg": ground_station.latitude_deg,
            "longitude_deg": ground_station.longitude_deg,
            "elevation_m": ground_station.elevation_m,
            "station_type": ground_station.station_type,
            "operator": ground_station.operator,
        }
        for autonomous_system in topology.autonomous_systems
        for ground_station in autonomous_system.ground_stations
    ]
    try:
        layout = topology.get_zone_layout()
    except RuntimeError:
        return {
            "format_version": 1,
            "tool": "zone",
            "status": "PASS",
            "zone_layout_configured": False,
            "model_type": "unknown",
            "model_is_default": False,
            "allow_overlap": None,
            "zones": [],
            "probes": [],
            "ground_stations": ground_stations,
            "counts": {
                "zones": 0,
                "probes": 0,
                "focus_visible": 0,
                "passed": 0,
                "failed": 0,
                "multi_match": 0,
                "pole": 0,
                "dateline": 0,
                "relationship_candidates": 0,
                "overlap_candidates": 0,
            },
        }

    try:
        model_type = type(getattr(layout, "_membership_model")).__name__
    except Exception:
        model_type = "unknown"
    model_is_default = model_type == "DefaultZoneMembershipModel"
    owned_by_id = {zone.zone_id: zone for zone in layout.zones}
    owned_identities = {id(zone): zone for zone in layout.zones}
    zones = [
        {
            "zone_id": zone.zone_id,
            "priority": zone.priority,
            "geometry": {
                "south_latitude_deg": zone.geometry.south_latitude_deg,
                "north_latitude_deg": zone.geometry.north_latitude_deg,
                "west_longitude_deg": zone.geometry.west_longitude_deg,
                "east_longitude_deg": zone.geometry.east_longitude_deg,
                "wrapped": (
                    zone.geometry.west_longitude_deg
                    > zone.geometry.east_longitude_deg
                ),
                "representative_center": list(
                    zone.geometry.representative_center
                ),
            },
            "display_boundary": _sample_rectangle_boundary(zone.geometry),
        }
        for zone in layout.zones
    ]

    evaluated = []
    for index, probe in enumerate(select_zone_probes(layout.zones), start=1):
        latitude = float(probe["latitude_deg"])
        longitude = float(probe["longitude_deg"])
        matches = ()
        winner = None
        find_error: BaseException | None = None
        resolve_error: BaseException | None = None
        try:
            matches = tuple(topology.find_zones(latitude, longitude))
        except Exception as error:
            find_error = error
        try:
            winner = topology.resolve_zone(latitude, longitude)
        except Exception as error:
            resolve_error = error

        reasons: list[str] = []
        if find_error is not None:
            reasons.append(
                f"Topology.find_zones raised {_error_text(find_error)}"
            )
        if resolve_error is not None:
            reasons.append(
                f"Topology.resolve_zone raised {_error_text(resolve_error)}"
            )
        origins = probe["origins"]
        assert isinstance(origins, list)
        source_ids = sorted(
            {
                source_id
                for origin in origins
                for source_id in origin["source_zone_ids"]
            }
        )
        for source_id in source_ids:
            source = owned_by_id.get(source_id)
            if source is None:
                reasons.append(f"probe source Zone is not layout-owned: {source_id}")
            elif not any(match is source for match in matches):
                reasons.append(
                    f"source Zone is absent from physical matches: {source_id}"
                )
        for match in matches:
            if (
                id(match) not in owned_identities
                or owned_identities[id(match)] is not match
            ):
                reasons.append(
                    "Topology.find_zones returned a Zone outside current ZoneLayout"
                )
        if winner is not None and not any(winner is match for match in matches):
            reasons.append(
                "Topology.resolve_zone winner is not identical to a physical match"
            )
        if winner is None and resolve_error is None:
            reasons.append("Topology.resolve_zone returned no authoritative Zone")

        invariant_status = "PASS" if not reasons else "FAIL"
        resolution_status = "OBSERVED" if winner is not None else "FAIL"
        evaluated.append(
            {
                "probe_id": f"probe-{index:04d}",
                "latitude_deg": latitude,
                "longitude_deg": longitude,
                "origins": origins,
                "source_zone_ids": source_ids,
                "categories": sorted(
                    {str(origin["category"]) for origin in origins}
                ),
                "physical_matches": [
                    {
                        "zone_id": match.zone_id,
                        "priority": match.priority,
                        "representative_center": list(
                            match.geometry.representative_center
                        ),
                    }
                    for match in matches
                ],
                "authoritative_result": (
                    None
                    if winner is None
                    else {
                        "zone_id": winner.zone_id,
                        "priority": winner.priority,
                        "representative_center": list(
                            winner.geometry.representative_center
                        ),
                    }
                ),
                "invariant_status": invariant_status,
                "resolution_status": resolution_status,
                "failure_reasons": reasons,
                "formal_call_chain": (
                    "Topology.find_zones / resolve_zone → ZoneLayout → "
                    "ZoneMembershipModel → Zone.contains → "
                    "LatitudeLongitudeRectangle.contains"
                ),
            }
        )

    _annotate_focus_visibility(evaluated)

    def has_category(probe: dict[str, object], fragment: str) -> bool:
        categories = probe["categories"]
        assert isinstance(categories, list)
        return any(fragment in str(category) for category in categories)

    counts = {
        "zones": len(zones),
        "probes": len(evaluated),
        "focus_visible": sum(
            bool(probe["focus_visible"]) for probe in evaluated
        ),
        "passed": sum(
            probe["invariant_status"] == "PASS" for probe in evaluated
        ),
        "failed": sum(
            probe["invariant_status"] == "FAIL" for probe in evaluated
        ),
        "multi_match": sum(
            len(probe["physical_matches"]) > 1 for probe in evaluated
        ),
        "pole": sum(has_category(probe, "pole") for probe in evaluated),
        "dateline": sum(
            has_category(probe, "dateline") for probe in evaluated
        ),
        "relationship_candidates": sum(
            any(
                origin["origin_kind"]
                == "tool-selected relationship candidate"
                for origin in probe["origins"]
            )
            for probe in evaluated
        ),
        "overlap_candidates": sum(
            has_category(probe, "overlap") for probe in evaluated
        ),
    }
    return {
        "format_version": 1,
        "tool": "zone",
        "status": "PASS" if counts["failed"] == 0 else "FAIL",
        "zone_layout_configured": True,
        "model_type": model_type,
        "model_is_default": model_is_default,
        "allow_overlap": layout.allow_overlap,
        "zones": zones,
        "probes": evaluated,
        "ground_stations": ground_stations,
        "counts": counts,
    }


def _render_page(data: dict[str, Any]) -> str:
    counts = data["counts"]
    assert isinstance(counts, dict)
    status = str(data["status"])
    configured = "已配置" if data["zone_layout_configured"] else "未配置"
    allow_overlap = data["allow_overlap"]
    stats = "".join(
        f'<div class="card stat"><span class="muted">{escape(label)}</span>'
        f"<strong>{counts[key]}</strong></div>"
        for key, label in (
            ("zones", "Zone"),
            ("probes", "Probe"),
            ("passed", "Invariant PASS"),
            ("failed", "Invariant FAIL"),
            ("multi_match", "Multi-match"),
            ("pole", "Pole probes"),
            ("dateline", "Dateline probes"),
            ("overlap_candidates", "Overlap candidates"),
        )
    )
    failures = [
        probe
        for probe in data["probes"]
        if probe["invariant_status"] == "FAIL"
    ]
    multi_matches = [
        probe
        for probe in data["probes"]
        if len(probe["physical_matches"]) > 1
    ]
    notable = (
        f"{len(failures)} 个 FAIL；{len(multi_matches)} 个 multi-match。"
        if failures or multi_matches
        else "没有 invariant FAIL 或 multi-match。"
    )
    default_explanation = (
        """<div class="warning"><strong>DefaultZoneMembershipModel：</strong>
physical matches → maximum priority → nearest representative center →
zone_id tie-break。正式 winner 始终来自 Topology.resolve_zone，工具不重算。</div>"""
        if data["model_is_default"]
        else ""
    )
    body = f"""
<section class="card">
  <span class="status {status}">{status}</span>
  <h2>ZoneLayout {configured}</h2>
  <p>Model：<code>{escape(str(data['model_type']))}</code>；
  allow_overlap：<code>{escape(str(allow_overlap))}</code>。{notable}</p>
  <p class="muted">PASS/FAIL 只表示简单不变量与正式 API 调用状态；
  authoritative resolution 标为 OBSERVED，不是工具独立证明。</p>
  {default_explanation}
</section>
<section><h2>探针摘要</h2><div class="grid stats">{stats}</div></section>
<section class="card">
  <h2>Zone 与自动探针空间视图</h2>
  <div class="controls">
    <label>探针视图
      <select id="view-mode">
        <option value="focus">重点视图</option>
        <option value="all">全部探针</option>
        <option value="anomalies">仅异常</option>
        <option value="multi">仅多重匹配</option>
      </select>
    </label>
    <label>Zone <select id="zone-filter"><option value="">全部</option></select></label>
    <label>Category <select id="category-filter"><option value="">全部</option></select></label>
    <label>Status <select id="status-filter"><option value="">全部</option><option>PASS</option><option>FAIL</option></select></label>
  </div>
  <div class="controls">
    <button id="show-globe" type="button" aria-pressed="true">3D 地球</button>
    <button id="show-map" type="button" aria-pressed="false">2D 经纬度图</button>
    <button id="globe-reset" type="button">重置视角</button>
    <label><input id="zone-toggle" type="checkbox" checked> Zone</label>
    <label><input id="probe-toggle" type="checkbox" checked> probes</label>
    <label><input id="label-toggle" type="checkbox" checked> labels</label>
    <label><input id="gs-toggle" type="checkbox" checked> GroundStation</label>
  </div>
  <p><strong>探针总数：</strong><span id="probe-total">{counts['probes']}</span>；
  <strong>当前显示：</strong><span id="probe-visible">0</span>；
  <strong>当前模式：</strong><span id="mode-label">重点视图</span></p>
  <div id="globe-panel">
    <canvas id="zone-globe" width="1100" height="620" aria-label="Zone 3D 地球视图"></canvas>
  </div>
  <div id="map-panel" hidden>
    <svg id="zone-map" viewBox="0 0 1200 600" role="img" aria-label="Zone 与探针经纬度世界视图"></svg>
  </div>
  <div class="warning"><strong>显示语义：</strong>
  球体采用单位半径；经纬网、Zone、probe 与 GroundStation 只来自已有经纬度数据。
  Zone 的 north/south 边按固定纬度、west/east 边按固定经度采样，不声称是大圆线。
  球面显示不改变正式 geometry、contains、membership 或 resolution，也没有执行
  Satellite propagation。</div>
  <p class="muted">2D 图用于精确检查边界、日期变更线和密集 probe。
  wrapped rectangle 在 2D 图拆为两个片段，在球体上沿 eastward arc 连续显示。
  tool-selected relation candidate 不是正式 overlap 结果。切换视图或探针模式
  只改变这份嵌入数据的浏览器可见性，不重新调用正式 API。</p>
</section>
<section class="grid two">
  <div class="card"><h2>点击详情</h2><pre id="probe-detail" class="detail">点击 Zone、probe 或 GroundStation。</pre></div>
  <div class="card"><h2>正式调用链</h2>
  <p><code>Topology.find_zones / resolve_zone → ZoneLayout →
  ZoneMembershipModel → Zone.contains →
  LatitudeLongitudeRectangle.contains</code></p>
  <p class="muted">physical matches、priority、representative center 与
  authoritative result 均来自正式返回对象；工具不选择 winner。</p></div>
</section>
<section class="card">
  <h2>当前筛选探针详情表</h2>
  <p class="muted">切换到“全部探针”可查看完整记录；表格和两个视图使用同一筛选状态。</p>
  <div style="overflow:auto"><table>
    <thead><tr><th>Probe</th><th>坐标</th><th>Category</th><th>Matches</th><th>Invariant</th><th>Resolution</th></tr></thead>
    <tbody id="probe-table"></tbody>
  </table></div>
</section>
"""
    script = f"""
const DATA={json_for_script(data)};
const map=document.getElementById("zone-map");
const globe=document.getElementById("zone-globe");
const globeContext=globe.getContext("2d");
const viewMode=document.getElementById("view-mode");
const zoneFilter=document.getElementById("zone-filter");
const categoryFilter=document.getElementById("category-filter");
const statusFilter=document.getElementById("status-filter");
const zoneToggle=document.getElementById("zone-toggle");
const probeToggle=document.getElementById("probe-toggle");
const labelToggle=document.getElementById("label-toggle");
const groundStationToggle=document.getElementById("gs-toggle");
const modeLabels={{focus:"重点视图",all:"全部探针",anomalies:"仅异常",multi:"仅多重匹配"}};
const palette=["#5ee5e5","#69a9ff","#ff9f7a","#bca7ff","#7ee787","#ffd166"];
const ns="http:"+"//www.w3.org/2000/svg";
const svg=(name,attrs={{}})=>{{
  const node=document.createElementNS(ns,name);
  Object.entries(attrs).forEach(([key,value])=>node.setAttribute(key,value));
  return node;
}};
const x=longitude=>(longitude+180)/360*1200;
const y=latitude=>(90-latitude)/180*600;
const showDetail=value=>document.getElementById("probe-detail").textContent=JSON.stringify(value,null,2);

for(let lon=-180;lon<=180;lon+=30)
  map.append(svg("line",{{x1:x(lon),y1:0,x2:x(lon),y2:600,stroke:"#294966","stroke-width":1}}));
for(let lat=-90;lat<=90;lat+=30)
  map.append(svg("line",{{x1:0,y1:y(lat),x2:1200,y2:y(lat),stroke:"#294966","stroke-width":1}}));
const zoneLayer=svg("g"),labelLayer=svg("g"),probeLayer=svg("g");
map.append(zoneLayer,labelLayer,probeLayer);
const zoneNodes=new Map(),labelNodes=new Map(),probeNodes=new Map();
DATA.zones.forEach((zone,index)=>{{
  const g=zone.geometry,color=palette[index%palette.length],nodes=[];
  const segments=g.wrapped?[[g.west_longitude_deg,180],[-180,g.east_longitude_deg]]:
    [[g.west_longitude_deg,g.east_longitude_deg]];
  segments.forEach(([west,east])=>{{
    const rect=svg("rect",{{x:x(west),y:y(g.north_latitude_deg),width:Math.max(1,x(east)-x(west)),
      height:Math.max(1,y(g.south_latitude_deg)-y(g.north_latitude_deg)),
      fill:color+"22",stroke:color,"stroke-width":3}});
    rect.dataset.zone=zone.zone_id;rect.style.cursor="pointer";
    rect.addEventListener("click",()=>showDetail(zone));zoneLayer.append(rect);nodes.push(rect);
  }});
  const center=g.representative_center;
  const centerPoint=svg("circle",{{cx:x(center[1]),cy:y(center[0]),r:5,fill:color}});
  centerPoint.dataset.zone=zone.zone_id;centerPoint.style.cursor="pointer";
  centerPoint.addEventListener("click",()=>showDetail(zone));zoneLayer.append(centerPoint);nodes.push(centerPoint);
  zoneNodes.set(zone.zone_id,nodes);
  const label=svg("text",{{x:x(center[1])+7,y:y(g.north_latitude_deg)-8,fill:color,"font-size":15,"font-weight":700}});
  label.textContent=`${{zone.zone_id}} · p${{zone.priority}}`;label.dataset.zone=zone.zone_id;labelLayer.append(label);
  labelNodes.set(zone.zone_id,label);
  const option=document.createElement("option");option.value=zone.zone_id;option.textContent=zone.zone_id;zoneFilter.append(option);
}});
const categories=[...new Set(DATA.probes.flatMap(probe=>probe.categories))].sort();
categories.forEach(category=>{{
  const option=document.createElement("option");option.value=category;option.textContent=category;categoryFilter.append(option);
}});
DATA.probes.forEach(probe=>{{
  const point=svg("circle",{{cx:x(probe.longitude_deg),cy:y(probe.latitude_deg),
    r:probe.invariant_status==="FAIL"?8:5,
    fill:probe.invariant_status==="FAIL"?"#ff7474":"#ffd166",
    stroke:probe.resolution_status==="OBSERVED"?"#5ee5e5":"#07111f","stroke-width":2}});
  point.dataset.probe=probe.probe_id;point.style.cursor="pointer";
  point.addEventListener("click",()=>showDetail(probe));probeLayer.append(point);
  probeNodes.set(probe.probe_id,point);
}});

function modeAllows(probe){{
  if(viewMode.value==="all")return true;
  if(viewMode.value==="anomalies")return probe.invariant_status==="FAIL";
  if(viewMode.value==="multi")return probe.physical_matches.length>1;
  return probe.focus_visible===true;
}}
function filterAllows(probe){{
  return modeAllows(probe)&&
    (!zoneFilter.value||probe.source_zone_ids.includes(zoneFilter.value))&&
    (!categoryFilter.value||probe.categories.includes(categoryFilter.value))&&
    (!statusFilter.value||probe.invariant_status===statusFilter.value);
}}
function visibleProbes(){{
  return probeToggle.checked?DATA.probes.filter(filterAllows):[];
}}
function zoneIsVisible(zone){{
  return zoneToggle.checked&&(!zoneFilter.value||zone.zone_id===zoneFilter.value);
}}
function rebuildTable(probes){{
  const table=document.getElementById("probe-table");table.replaceChildren();
  probes.forEach(probe=>{{
    const row=document.createElement("tr");row.style.cursor="pointer";
    const values=[
      probe.probe_id,
      `${{probe.latitude_deg}}, ${{probe.longitude_deg}}`,
      probe.categories.join(", "),
      probe.physical_matches.map(value=>value.zone_id).join(", "),
      probe.invariant_status,
      probe.resolution_status
    ];
    values.forEach(value=>{{const cell=document.createElement("td");cell.textContent=value;row.append(cell);}});
    row.addEventListener("click",()=>showDetail(probe));table.append(row);
  }});
}}

function initialViewForLocations(locations,fallback){{
  if(!locations.length)return fallback;
  const vector=locations.reduce((sum,value)=>{{
    const latitude=value[0]*Math.PI/180,longitude=value[1]*Math.PI/180;
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
const initialLocations=DATA.zones.length?
  DATA.zones.map(zone=>zone.geometry.representative_center):
  DATA.ground_stations.map(station=>[station.latitude_deg,station.longitude_deg]);
const initialGlobeView=initialViewForLocations(initialLocations,{{yaw:-0.65,pitch:0.28}});
let globeYaw=initialGlobeView.yaw,globePitch=initialGlobeView.pitch,globeDrag=null;
let globeMoved=false,globePointHits=[],globeZonePaths=[];
const spherePoint=(latitude,longitude,radius=1)=>{{
  const lat=latitude*Math.PI/180,lon=longitude*Math.PI/180;
  return [radius*Math.cos(lat)*Math.cos(lon),radius*Math.sin(lat),radius*Math.cos(lat)*Math.sin(lon)];
}};
const globeRotate=([xValue,yValue,zValue])=>{{
  const cy=Math.cos(globeYaw),sy=Math.sin(globeYaw),cp=Math.cos(globePitch),sp=Math.sin(globePitch);
  const x1=xValue*cy+zValue*sy,z1=-xValue*sy+zValue*cy;
  return [x1,yValue*cp-z1*sp,yValue*sp+z1*cp];
}};
const globeProject=point=>{{
  const [xValue,yValue,zValue]=globeRotate(point),scale=238;
  return [globe.width/2+xValue*scale,globe.height/2-yValue*scale,zValue];
}};
const canonicalLongitude=value=>{{
  const result=((value+180)%360+360)%360-180;return result===0?0:result;
}};
const longitudeAt=(geometry,fraction)=>{{
  const span=((geometry.east_longitude_deg-geometry.west_longitude_deg)%360+360)%360;
  return canonicalLongitude(geometry.west_longitude_deg+span*fraction);
}};
function globeLine(points,color,width){{
  globeContext.strokeStyle=color;globeContext.lineWidth=width;
  globeContext.beginPath();let open=false;
  points.forEach(point=>{{
    const projected=globeProject(point);
    if(projected[2]<0){{open=false;return;}}
    if(open)globeContext.lineTo(projected[0],projected[1]);
    else{{globeContext.moveTo(projected[0],projected[1]);open=true;}}
  }});
  globeContext.stroke();
}}
function drawZoneSurface(zone,color){{
  const geometry=zone.geometry,latitudeSteps=10,longitudeSteps=20;
  for(let latitudeIndex=0;latitudeIndex<latitudeSteps;latitudeIndex++){{
    const south=geometry.south_latitude_deg+
      (geometry.north_latitude_deg-geometry.south_latitude_deg)*latitudeIndex/latitudeSteps;
    const north=geometry.south_latitude_deg+
      (geometry.north_latitude_deg-geometry.south_latitude_deg)*(latitudeIndex+1)/latitudeSteps;
    for(let longitudeIndex=0;longitudeIndex<longitudeSteps;longitudeIndex++){{
      const west=longitudeAt(geometry,longitudeIndex/longitudeSteps);
      const east=longitudeAt(geometry,(longitudeIndex+1)/longitudeSteps);
      const projected=[
        globeProject(spherePoint(south,west,1.004)),
        globeProject(spherePoint(south,east,1.004)),
        globeProject(spherePoint(north,east,1.004)),
        globeProject(spherePoint(north,west,1.004))
      ];
      if(projected.reduce((sum,value)=>sum+value[2],0)/4<=0)continue;
      const path=new Path2D();path.moveTo(projected[0][0],projected[0][1]);
      projected.slice(1).forEach(value=>path.lineTo(value[0],value[1]));path.closePath();
      globeContext.fillStyle=color+"28";globeContext.fill(path);
      globeZonePaths.push({{path,zone}});
    }}
  }}
}}
function drawGlobe(){{
  globeContext.clearRect(0,0,globe.width,globe.height);
  const gradient=globeContext.createRadialGradient(globe.width*.43,globe.height*.4,10,
    globe.width/2,globe.height/2,238);
  gradient.addColorStop(0,"#276c92");gradient.addColorStop(1,"#09263e");
  globeContext.fillStyle=gradient;globeContext.beginPath();
  globeContext.arc(globe.width/2,globe.height/2,238,0,Math.PI*2);globeContext.fill();
  for(let latitude=-60;latitude<=60;latitude+=30)
    globeLine(Array.from({{length:73}},(_,index)=>spherePoint(latitude,-180+index*5)),"#79b9d344",1);
  for(let longitude=-150;longitude<180;longitude+=30)
    globeLine(Array.from({{length:37}},(_,index)=>spherePoint(-90+index*5,longitude)),"#79b9d344",1);
  globePointHits=[];globeZonePaths=[];
  if(zoneToggle.checked)DATA.zones.forEach((zone,index)=>{{
    if(!zoneIsVisible(zone))return;
    const color=palette[index%palette.length];
    drawZoneSurface(zone,color);
    globeLine(zone.display_boundary.map(value=>spherePoint(value[0],value[1],1.008)),color,2.4);
    const center=zone.geometry.representative_center;
    const point=globeProject(spherePoint(center[0],center[1],1.015));
    if(point[2]>=0){{
      globeContext.fillStyle=color;globeContext.beginPath();
      globeContext.moveTo(point[0],point[1]-7);globeContext.lineTo(point[0]+7,point[1]);
      globeContext.lineTo(point[0],point[1]+7);globeContext.lineTo(point[0]-7,point[1]);
      globeContext.closePath();globeContext.fill();
      globePointHits.push({{point,value:zone,kind:"zone"}});
      if(labelToggle.checked){{
        globeContext.fillStyle=color;globeContext.font="14px system-ui";
        globeContext.fillText(
          zone.zone_id,
          point[0]+9,
          point[1]-8+(index%4)*18
        );
      }}
    }}
  }});
  if(groundStationToggle.checked)DATA.ground_stations.forEach(station=>{{
    const point=globeProject(spherePoint(station.latitude_deg,station.longitude_deg,1.018));
    if(point[2]<0)return;
    globeContext.fillStyle="#bca7ff";globeContext.strokeStyle="#07111f";globeContext.lineWidth=2;
    globeContext.beginPath();globeContext.arc(point[0],point[1],6,0,Math.PI*2);globeContext.fill();globeContext.stroke();
    globePointHits.push({{point,value:station,kind:"ground-station"}});
  }});
  visibleProbes().forEach(probe=>{{
    const point=globeProject(spherePoint(probe.latitude_deg,probe.longitude_deg,1.025));
    if(point[2]<0)return;
    globeContext.fillStyle=probe.invariant_status==="FAIL"?"#ff7474":"#ffd166";
    globeContext.strokeStyle=probe.resolution_status==="OBSERVED"?"#5ee5e5":"#07111f";
    globeContext.lineWidth=2;globeContext.beginPath();
    globeContext.arc(point[0],point[1],probe.invariant_status==="FAIL"?7:4.5,0,Math.PI*2);
    globeContext.fill();globeContext.stroke();
    globePointHits.push({{point,value:probe,kind:"probe"}});
  }});
  globeContext.fillStyle="#b9d8e8";globeContext.font="14px system-ui";
  globeContext.fillText("单位球面 · 拖动旋转 · 点击查看详情",18,28);
}}

globe.addEventListener("pointerdown",event=>{{
  globeDrag=[event.clientX,event.clientY];globeMoved=false;globe.setPointerCapture(event.pointerId);
}});
globe.addEventListener("pointermove",event=>{{
  if(!globeDrag)return;
  const dx=event.clientX-globeDrag[0],dy=event.clientY-globeDrag[1];
  if(Math.abs(dx)+Math.abs(dy)>2)globeMoved=true;
  globeYaw+=dx*.009;globePitch=Math.max(-1.3,Math.min(1.3,globePitch+dy*.009));
  globeDrag=[event.clientX,event.clientY];drawGlobe();
}});
globe.addEventListener("pointerup",()=>globeDrag=null);
globe.addEventListener("click",event=>{{
  if(globeMoved){{globeMoved=false;return;}}
  const rectangle=globe.getBoundingClientRect();
  const clickX=(event.clientX-rectangle.left)*globe.width/rectangle.width;
  const clickY=(event.clientY-rectangle.top)*globe.height/rectangle.height;
  let best=null,distance=14;
  [...globePointHits].reverse().forEach(item=>{{
    const current=Math.hypot(item.point[0]-clickX,item.point[1]-clickY);
    if(current<distance){{distance=current;best=item.value;}}
  }});
  if(best){{showDetail(best);return;}}
  for(let index=globeZonePaths.length-1;index>=0;index--)
    if(globeContext.isPointInPath(globeZonePaths[index].path,clickX,clickY)){{
      showDetail(globeZonePaths[index].zone);return;
    }}
}});

function applyFilters(){{
  const probes=visibleProbes();
  const visibleIds=new Set(probes.map(probe=>probe.probe_id));
  DATA.zones.forEach(zone=>{{
    const visible=zoneIsVisible(zone);
    (zoneNodes.get(zone.zone_id)||[]).forEach(node=>node.style.display=visible?"":"none");
    const label=labelNodes.get(zone.zone_id);
    if(label)label.style.display=visible&&labelToggle.checked?"":"none";
  }});
  DATA.probes.forEach(probe=>{{
    const node=probeNodes.get(probe.probe_id);
    if(node)node.style.display=visibleIds.has(probe.probe_id)?"":"none";
  }});
  document.getElementById("probe-visible").textContent=String(probes.length);
  document.getElementById("mode-label").textContent=modeLabels[viewMode.value];
  rebuildTable(probes);drawGlobe();
}}
function setSpatialView(mode){{
  const globeActive=mode==="globe";
  document.getElementById("globe-panel").hidden=!globeActive;
  document.getElementById("map-panel").hidden=globeActive;
  document.getElementById("show-globe").setAttribute("aria-pressed",String(globeActive));
  document.getElementById("show-map").setAttribute("aria-pressed",String(!globeActive));
  if(globeActive)drawGlobe();
}}
[viewMode,zoneFilter,categoryFilter,statusFilter,zoneToggle,probeToggle,labelToggle,groundStationToggle]
  .forEach(control=>control.addEventListener("change",applyFilters));
document.getElementById("show-globe").addEventListener("click",()=>setSpatialView("globe"));
document.getElementById("show-map").addEventListener("click",()=>setSpatialView("map"));
document.getElementById("globe-reset").addEventListener("click",()=>{{
  globeYaw=initialGlobeView.yaw;globePitch=initialGlobeView.pitch;drawGlobe();
}});
applyFilters();setSpatialView("globe");
"""
    return standalone_page(
        title="Zone Tool：范围、Membership 与 Resolution",
        eyebrow="Zone observation",
        body=body,
        script=script,
    )


def write_zone_tool(topology: Topology, directory: Path) -> dict[str, object]:
    """Write probes JSON, summary and standalone page."""

    data = collect_zone_data(topology)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "probes.json", data)
    write_text(directory / "index.html", _render_page(data))
    counts = data["counts"]
    assert isinstance(counts, dict)
    write_text(
        directory / "summary.txt",
        "\n".join(
            (
                f"status={data['status']}",
                (
                    "zone_layout_configured="
                    f"{str(data['zone_layout_configured']).lower()}"
                ),
                f"model_type={data['model_type']}",
                f"allow_overlap={data['allow_overlap']}",
                *(f"{key}={value}" for key, value in counts.items()),
                (
                    "resolution=OBSERVED (authoritative result comes from "
                    "Topology.resolve_zone)"
                ),
                "",
            )
        ),
    )
    return {
        "status": data["status"],
        "message": (
            f"{counts['zones']} Zone / {counts['probes']} probes / "
            f"{counts['failed']} FAIL / {counts['multi_match']} multi-match"
        ),
        "counts": counts,
    }
