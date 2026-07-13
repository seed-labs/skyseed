"""Render an offline globe from one run's frozen canonical inputs."""

from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any

from skyseed_v1.visualization.export_ground_links import (
    EARTH_RADIUS_KM,
    NodePosition,
    _gmst_deg,
    _satellite_lat_lon_deg,
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _canonical_segment_config_dirs(run_dir: Path) -> list[Path]:
    directories = [run_dir / "config"]
    directories.extend(
        sorted(run_dir.glob("replenished_segments/segment_*/config"))
    )
    for directory in directories:
        for name in ("snapshot_preview.json", "visualization_physical_links.json"):
            if not (directory / name).is_file():
                raise ValueError(f"canonical visualization source missing: {directory / name}")
    return directories


def build_run_globe_payload(run_dir: Path) -> dict[str, Any]:
    """Project already-frozen runtime inputs without recomputing topology."""
    run_dir = run_dir.resolve()
    profile_path = run_dir / "effective_profile.json"
    profile = _read_json(profile_path) if profile_path.is_file() else {}
    config_dirs = _canonical_segment_config_dirs(run_dir)
    frames: list[dict[str, Any]] = []
    first_snapshot: dict[str, Any] | None = None
    interval_seconds: float | None = None
    source_artifacts: list[dict[str, str]] = []
    for config_dir in config_dirs:
        snapshot_path = config_dir / "snapshot_preview.json"
        physical_path = config_dir / "visualization_physical_links.json"
        snapshot_payload = _read_json(snapshot_path)
        snapshots = list(snapshot_payload.get("snapshots") or [])
        pipeline = dict(snapshot_payload.get("pipeline") or {})
        frame_offset = int(pipeline.get("visualization_link_frame_offset") or 0)
        if frame_offset < 0:
            raise ValueError(
                f"canonical visualization frame offset is negative: {snapshot_path}"
            )
        physical = _read_json(physical_path)
        physical_frames = list(physical.get("links") or [])
        if len(snapshots) < 2:
            raise ValueError(f"canonical segment has fewer than two snapshots: {snapshot_path}")
        segment_interval = float(str(physical.get("interval") or "").removesuffix("s"))
        interval_seconds = interval_seconds or segment_interval
        if segment_interval != interval_seconds:
            raise ValueError("canonical segment intervals disagree")
        snapshot_start = str(dict(snapshots[0]).get("time_slice", {}).get("absolute_time_utc") or "")
        physical_base_start = datetime.fromtimestamp(
            int(str(physical.get("timestamp"))) / 1000.0,
            tz=datetime.fromisoformat(snapshot_start.replace("Z", "+00:00")).tzinfo,
        )
        physical_effective_start = datetime.fromtimestamp(
            physical_base_start.timestamp() + frame_offset * segment_interval,
            tz=physical_base_start.tzinfo,
        ).isoformat().replace("+00:00", "Z")
        if physical_effective_start != snapshot_start:
            raise ValueError(
                "canonical segment time mismatch: "
                f"snapshot={snapshot_start} physical_links={physical_effective_start} "
                f"frame_offset={frame_offset}"
            )
        frame_count = len(snapshots) - 1
        if len(physical_frames) < frame_offset + frame_count:
            raise ValueError(
                f"physical links do not cover canonical snapshots: {physical_path}"
            )
        first_snapshot = first_snapshot or dict(snapshots[0])
        source_artifacts.append(
            {
                "snapshotPreview": str(snapshot_path),
                "physicalLinks": str(physical_path),
                "physicalFrameOffset": frame_offset,
            }
        )
        for index in range(frame_count):
            snapshot = dict(snapshots[index])
            time_slice = dict(snapshot.get("time_slice") or {})
            timestamp_text = str(time_slice.get("absolute_time_utc") or "")
            timestamp = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
            gmst = _gmst_deg(timestamp)
            nodes = dict(snapshot.get("nodes") or {})
            satellites = []
            for node_id, node in nodes.items():
                if str(dict(node).get("node_type")) != "satellite":
                    continue
                state = dict(dict(node).get("position_state") or {})
                position = NodePosition(
                    float(state["x_km"]), float(state["y_km"]), float(state["z_km"])
                )
                latitude, longitude = _satellite_lat_lon_deg(position, gmst_deg=gmst)
                altitude = math.sqrt(
                    position.x_km**2 + position.y_km**2 + position.z_km**2
                ) - EARTH_RADIUS_KM
                satellites.append(
                    [str(node_id), round(latitude, 4), round(longitude, 4), round(altitude, 2)]
                )
            zones = dict(snapshot.get("zones") or {})
            membership = dict(snapshot.get("ut_zone_membership") or {})
            attachments = []
            for ut_id, zone_id in sorted(membership.items()):
                attachment = dict(dict(nodes.get(ut_id) or {}).get("attachment") or {})
                serving = str(attachment.get("active_serving_satellite_id") or "")
                anchor = str(dict(zones.get(zone_id) or {}).get("active_serving_satellite_id") or "")
                if not serving or serving != anchor:
                    raise ValueError(
                        f"canonical attachment/zone anchor mismatch: {ut_id}/{zone_id}"
                    )
                attachments.append(
                    {"groundStationId": str(ut_id), "satelliteId": serving, "zoneId": str(zone_id)}
                )
            physical_frame = dict(physical_frames[frame_offset + index])
            frames.append(
                {
                    "timestamp": timestamp_text,
                    "satellites": satellites,
                    "satelliteLinks": list(physical_frame.get("satelliteLinks") or []),
                    "gatewayAccess": list(physical_frame.get("groundLinks") or []),
                    "utAttachments": attachments,
                    "zoneAnchors": {
                        zone_id: dict(zone).get("active_serving_satellite_id")
                        for zone_id, zone in zones.items()
                    },
                }
            )
    if first_snapshot is None:
        raise ValueError("canonical visualization has no snapshots")
    nodes = dict(first_snapshot.get("nodes") or {})
    membership = dict(first_snapshot.get("ut_zone_membership") or {})
    uts = []
    gateways = []
    for node_id, node in sorted(nodes.items()):
        item = dict(node)
        fixed = dict(item.get("fixed_coordinates") or {})
        if item.get("node_type") == "user_terminal":
            uts.append(
                {
                    "ut_id": node_id,
                    "zone_id": membership.get(node_id),
                    "latitude_deg": fixed.get("latitude_deg"),
                    "longitude_deg": fixed.get("longitude_deg"),
                    "altitude_km": fixed.get("altitude_km", 0.0),
                }
            )
        elif item.get("node_type") == "gateway":
            gateways.append(
                {
                    "gateway_id": node_id,
                    "latitude_deg": fixed.get("latitude_deg"),
                    "longitude_deg": fixed.get("longitude_deg"),
                    "altitude_km": fixed.get("altitude_km", 0.0),
                }
            )
    zones = []
    for zone_id, zone in sorted(dict(first_snapshot.get("zones") or {}).items()):
        item = dict(zone)
        fixed = dict(item.get("fixed_coordinates") or {})
        zones.append(
            {
                **item,
                "zone_id": zone_id,
                "latitude_deg": fixed.get("latitude_deg"),
                "longitude_deg": fixed.get("longitude_deg"),
                "altitude_km": fixed.get("altitude_km", 0.0),
            }
        )
    hints = dict(first_snapshot.get("planner_hints") or {})
    policy_lines = []
    for ut_id, policy in sorted(dict(hints.get("ut_gateway_policies") or {}).items()):
        for rule in list(dict(policy).get("rules") or []):
            policy_lines.append(
                {
                    "utId": ut_id,
                    "gatewayId": rule.get("gateway_id"),
                    "prefix": rule.get("prefix"),
                    "role": rule.get("rule_role"),
                }
            )
    runner = dict(profile.get("runner_args") or {})
    if not runner.get("planned_shell_id"):
        topology_path = config_dirs[0] / "topology.json"
        if topology_path.is_file():
            topology = _read_json(topology_path)
            orbit_input = dict(topology.get("orbit_input") or {})
            runner["planned_shell_id"] = (
                orbit_input.get("planned_shell_id")
                or orbit_input.get("shell_id")
            )
    return {
        "schema": "skyseed-run-globe-v1",
        "runId": run_dir.name,
        "example": run_dir.parent.parent.name,
        "shell": runner.get("planned_shell_id"),
        "intervalSeconds": interval_seconds,
        "semantics": {
            "source": "frozen runtime inputs from this exact run",
            "satelliteLinks": "physical frozen ISL candidates",
            "gatewayAccess": "physical gateway visibility links",
            "utAttachments": "canonical runtime attachment from TopologySnapshot",
            "policyLines": "logical UT-to-gateway policy; not a physical radio link",
        },
        "sourceArtifacts": source_artifacts,
        "uts": uts,
        "gateways": gateways,
        "zones": zones,
        "policyLines": policy_lines,
        "frames": frames,
    }


def write_demo_globe(run_dir: Path, output_dir: Path | None = None) -> Path:
    destination = output_dir or run_dir / "visualization"
    destination.mkdir(parents=True, exist_ok=True)
    payload = build_run_globe_payload(run_dir)
    data_path = destination / "visualization_data.json"
    data_path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    embedded = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = _HTML.replace("__SKYSEED_DATA__", embedded)
    index = destination / "index.html"
    index.write_text(html, encoding="utf-8")
    return index


_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Skyseed timeline globe</title><style>
html,body{margin:0;height:100%;overflow:hidden;background:#050a14;color:#e8f3ff;font:14px system-ui,sans-serif}canvas{width:100%;height:100%;display:block;cursor:grab}canvas:active{cursor:grabbing}
#panel{position:fixed;left:14px;top:14px;width:310px;padding:14px;background:#071323e8;border:1px solid #294766;border-radius:10px;box-shadow:0 8px 30px #0008}h1{font-size:18px;margin:0 0 6px}.muted{color:#9cb2c9;font-size:12px}.row{display:flex;gap:8px;align-items:center;margin-top:10px}input[type=range]{flex:1}.layers{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-top:10px}.legend{margin-top:9px;font-size:12px;line-height:1.5}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}button{background:#15304b;color:white;border:1px solid #41698e;border-radius:5px;padding:5px 10px}#stats{margin-top:8px;font-variant-numeric:tabular-nums}
</style></head><body><canvas id="globe"></canvas><div id="panel"><h1 id="title"></h1><div class="muted">Drag to rotate · wheel to zoom · offline standalone page</div><div class="row"><button id="play">Play</button><input id="time" type="range" min="0" value="0"><span id="step"></span></div><div id="timestamp"></div><div class="layers">
<label><input data-layer="isl" type="checkbox" checked> satellite links</label><label><input data-layer="gw" type="checkbox" checked> GW access</label><label><input data-layer="ut" type="checkbox" checked> UT attachments</label><label><input data-layer="policy" type="checkbox"> UT→GW policy</label><label><input data-layer="zones" type="checkbox" checked> zones</label><label><input data-layer="labels" type="checkbox" checked> labels</label></div>
<div class="legend"><span class="dot" style="background:#50d5ff"></span>ISL &nbsp; <span class="dot" style="background:#ffad4d"></span>GW visibility<br><span class="dot" style="background:#67f29a"></span>canonical UT attachment &nbsp; <span class="dot" style="background:#ff72db"></span>logical policy</div><div id="stats" class="muted"></div></div>
<script>const D=__SKYSEED_DATA__;const c=document.querySelector('canvas'),x=c.getContext('2d'),slider=document.querySelector('#time'),layers={};document.querySelectorAll('[data-layer]').forEach(e=>layers[e.dataset.layer]=e);slider.max=D.frames.length-1;document.querySelector('#title').textContent=`${D.example} · ${D.shell}`;
let yaw=-.5,pitch=.25,zoom=1,drag=null,playing=false,timer=null;const loc=(o)=>[+o.latitude_deg,+o.longitude_deg,0],satMap=()=>new Map(D.frames[+slider.value].satellites.map(s=>[s[0],s]));
function zoneRing(z){let lat=+z.latitude_deg,lon=+z.longitude_deg,angular=(+z.radius_km||100)/6371,points=[];for(let i=0;i<=48;i++){let b=2*Math.PI*i/48,a=lat*Math.PI/180,l=lon*Math.PI/180,la=Math.asin(Math.sin(a)*Math.cos(angular)+Math.cos(a)*Math.sin(angular)*Math.cos(b)),lo=l+Math.atan2(Math.sin(b)*Math.sin(angular)*Math.cos(a),Math.cos(angular)-Math.sin(a)*Math.sin(la));points.push([la*180/Math.PI,((lo*180/Math.PI+540)%360)-180,0])}return points}
function coordinates(p){return typeof p[0]==='string'?p.slice(1):p}
function project(p){let [lat,lon,alt=0]=coordinates(p).map(Number),a=lat*Math.PI/180,b=lon*Math.PI/180+yaw,r=1+alt/6371;let X=r*Math.cos(a)*Math.sin(b),Y=-r*Math.sin(a),Z=r*Math.cos(a)*Math.cos(b);let yy=Y*Math.cos(pitch)-Z*Math.sin(pitch),zz=Y*Math.sin(pitch)+Z*Math.cos(pitch);let R=Math.min(innerWidth,innerHeight)*.39*zoom;return[innerWidth/2+X*R,innerHeight/2+yy*R,zz]}
function line(a,b,color,width=1,dash=[]){let A=project(a),B=project(b);if(A[2]<0||B[2]<0)return;x.beginPath();x.setLineDash(dash);x.moveTo(A[0],A[1]);x.lineTo(B[0],B[1]);x.strokeStyle=color;x.lineWidth=width;x.stroke();x.setLineDash([])}
function surfaceArc(a,b,color,width=1,dash=[]){let A=coordinates(a),B=coordinates(b),v=q=>{let p=q[0]*Math.PI/180,l=q[1]*Math.PI/180;return[Math.cos(p)*Math.cos(l),Math.cos(p)*Math.sin(l),Math.sin(p)]},u=v(A),w=v(B),d=Math.max(-1,Math.min(1,u[0]*w[0]+u[1]*w[1]+u[2]*w[2])),o=Math.acos(d),last=A;if(o<.0001){line(a,b,color,width,dash);return}for(let i=1;i<=24;i++){let t=i/24,s=Math.sin(o),q=[(Math.sin((1-t)*o)*u[0]+Math.sin(t*o)*w[0])/s,(Math.sin((1-t)*o)*u[1]+Math.sin(t*o)*w[1])/s,(Math.sin((1-t)*o)*u[2]+Math.sin(t*o)*w[2])/s],next=[Math.asin(q[2])*180/Math.PI,Math.atan2(q[1],q[0])*180/Math.PI,0];line(last,next,color,width,dash);last=next}}
function point(p,color,r,label){let q=project(p);if(q[2]<0)return;x.beginPath();x.arc(q[0],q[1],r,0,7);x.fillStyle=color;x.fill();if(label&&layers.labels.checked){x.fillStyle='#eef';x.font='11px system-ui';x.fillText(label,q[0]+r+3,q[1]-2)}}
function render(){let w=innerWidth*devicePixelRatio,h=innerHeight*devicePixelRatio;if(c.width!==w||c.height!==h){c.width=w;c.height=h}x.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);x.clearRect(0,0,innerWidth,innerHeight);let R=Math.min(innerWidth,innerHeight)*.39*zoom,g=x.createRadialGradient(innerWidth*.43,innerHeight*.4,R*.1,innerWidth/2,innerHeight/2,R);g.addColorStop(0,'#245b8b');g.addColorStop(1,'#07172a');x.beginPath();x.arc(innerWidth/2,innerHeight/2,R,0,7);x.fillStyle=g;x.fill();x.strokeStyle='#5182a8';x.stroke();for(let lat=-60;lat<=60;lat+=30)for(let lon=-180;lon<180;lon+=10)line([lat,lon],[lat,lon+10],'#8db4d322');for(let lon=-180;lon<180;lon+=30)for(let lat=-90;lat<90;lat+=10)line([lat,lon],[lat+10,lon],'#8db4d322');let f=D.frames[+slider.value],S=satMap(),U=new Map(D.uts.map(o=>[o.ut_id,o])),G=new Map(D.gateways.map(o=>[o.gateway_id,o]));if(layers.zones.checked)D.zones.forEach(z=>{let pts=zoneRing(z);for(let i=0;i<pts.length-1;i++)line(pts[i],pts[i+1],'#ffe36a',2,[5,4])});if(layers.isl.checked)f.satelliteLinks.forEach(l=>{if(S.has(l.satelliteAId)&&S.has(l.satelliteBId))line(S.get(l.satelliteAId),S.get(l.satelliteBId),'#50d5ff66')});if(layers.gw.checked)f.gatewayAccess.forEach(l=>{if(G.has(l.groundStationId)&&S.has(l.satelliteId))line(loc(G.get(l.groundStationId)),S.get(l.satelliteId),'#ffad4d99',1.5)});if(layers.ut.checked)f.utAttachments.forEach(l=>{if(U.has(l.groundStationId)&&S.has(l.satelliteId))line(loc(U.get(l.groundStationId)),S.get(l.satelliteId),'#67f29acc',2)});if(layers.policy.checked)D.policyLines.forEach(l=>{if(U.has(l.utId)&&G.has(l.gatewayId))surfaceArc(loc(U.get(l.utId)),loc(G.get(l.gatewayId)),'#ff72dbbb',1.3,[5,5])});f.satellites.forEach(s=>point(s,'#c8f4ff',1.6));D.gateways.forEach(o=>point(loc(o),'#ffad4d',5,o.gateway_id));D.uts.forEach(o=>point(loc(o),'#67f29a',4,o.ut_id));document.querySelector('#timestamp').textContent=f.timestamp;document.querySelector('#step').textContent=`${+slider.value+1}/${D.frames.length}`;document.querySelector('#stats').textContent=`${f.satellites.length} satellites · ${f.satelliteLinks.length} ISLs · ${f.gatewayAccess.length} GW links · ${f.utAttachments.length} canonical UT attachments`}
slider.oninput=render;document.querySelectorAll('[data-layer]').forEach(e=>e.onchange=render);document.querySelector('#play').onclick=()=>{playing=!playing;document.querySelector('#play').textContent=playing?'Pause':'Play';clearInterval(timer);if(playing)timer=setInterval(()=>{slider.value=(+slider.value+1)%D.frames.length;render()},Math.max(120,D.intervalSeconds*35))};c.onpointerdown=e=>{drag=[e.clientX,e.clientY,yaw,pitch];c.setPointerCapture(e.pointerId)};c.onpointermove=e=>{if(!drag)return;yaw=drag[2]+(e.clientX-drag[0])*.008;pitch=Math.max(-1.35,Math.min(1.35,drag[3]+(e.clientY-drag[1])*.008));render()};c.onpointerup=()=>drag=null;c.onwheel=e=>{e.preventDefault();zoom=Math.max(.55,Math.min(2.2,zoom*Math.exp(-e.deltaY*.001)));render()};onresize=render;render();</script></body></html>'''
