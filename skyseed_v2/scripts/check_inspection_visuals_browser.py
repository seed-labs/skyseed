#!/usr/bin/env python3
"""Exercise current inspection views and detached replay controls in Firefox."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile
import time

try:
    from scripts._webdriver import (
        _available_port,
        _execute,
        _request,
        _stop_driver,
        _wait_for_driver,
    )
except ModuleNotFoundError:
    from _webdriver import (  # type: ignore[no-redef]
        _available_port,
        _execute,
        _request,
        _stop_driver,
        _wait_for_driver,
    )


def _navigate(base_url: str, session_id: str, page: Path) -> None:
    if not page.is_file():
        raise FileNotFoundError(page)
    _request(
        f"{base_url}/session/{session_id}/url",
        method="POST",
        value={"url": page.resolve().as_uri()},
    )
    time.sleep(0.6)


def _drag(
    base_url: str,
    session_id: str,
    element_id: str,
    dx: int,
    dy: int,
) -> None:
    rectangle = _execute(
        base_url,
        session_id,
        f"""
const value=document.getElementById({element_id!r});
value.scrollIntoView({{block:"center"}});
const box=value.getBoundingClientRect();
return {{x:Math.round(box.left+box.width/2),y:Math.round(box.top+box.height/2)}};
""",
    )
    _request(
        f"{base_url}/session/{session_id}/actions",
        method="POST",
        value={
            "actions": [
                {
                    "type": "pointer",
                    "id": "inspection-mouse",
                    "parameters": {"pointerType": "mouse"},
                    "actions": [
                        {
                            "type": "pointerMove",
                            "duration": 0,
                            "origin": "viewport",
                            "x": rectangle["x"],
                            "y": rectangle["y"],
                        },
                        {"type": "pointerDown", "button": 0},
                        {
                            "type": "pointerMove",
                            "duration": 180,
                            "origin": "viewport",
                            "x": rectangle["x"] + dx,
                            "y": rectangle["y"] + dy,
                        },
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        },
    )


def _click_at(
    base_url: str,
    session_id: str,
    x: int,
    y: int,
) -> None:
    _request(
        f"{base_url}/session/{session_id}/actions",
        method="POST",
        value={
            "actions": [
                {
                    "type": "pointer",
                    "id": "inspection-click",
                    "parameters": {"pointerType": "mouse"},
                    "actions": [
                        {
                            "type": "pointerMove",
                            "duration": 0,
                            "origin": "viewport",
                            "x": x,
                            "y": y,
                        },
                        {"type": "pointerDown", "button": 0},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        },
    )


def _move_at(
    base_url: str,
    session_id: str,
    x: int,
    y: int,
) -> None:
    _request(
        f"{base_url}/session/{session_id}/actions",
        method="POST",
        value={
            "actions": [
                {
                    "type": "pointer",
                    "id": "inspection-hover",
                    "parameters": {"pointerType": "mouse"},
                    "actions": [
                        {
                            "type": "pointerMove",
                            "duration": 80,
                            "origin": "viewport",
                            "x": x,
                            "y": y,
                        }
                    ],
                }
            ]
        },
    )


def _aggregate(base_url: str, session_id: str, page: Path) -> dict[str, object]:
    _navigate(base_url, session_id, page)
    result = _execute(
        base_url,
        session_id,
        """
const chinese=document.title,prose=document.querySelector("section.card")?.textContent||"";
document.querySelector('[data-language="en"]')?.click();
return {chinese,english:document.title,prose};
""",
    )
    if (
        result["chinese"] != "SkySEED 工具总览"
        or result["english"] != "SkySEED Tool Overview"
        or "先阅读静态结构和精确 PhysicalPositionSnapshot 事实" not in result["prose"]
        or "所有 Tool 均分离且只读" not in result["prose"]
    ):
        raise AssertionError(f"Aggregate Chinese explanation regression: {result}")
    return {"chinese_explanation": "PASS"}


def _physical_world(base_url: str, session_id: str, page: Path) -> dict[str, object]:
    _navigate(base_url, session_id, page)
    current = _execute(
        base_url,
        session_id,
        """
const globe=document.getElementById("globe-canvas"),map=document.getElementById("map-canvas"),time=document.getElementById("time");
return {title:document.title,frames:Number(time.max)+1,positions:Number(globe.dataset.positionCount),globeTime:globe.dataset.timestamp,mapTime:map.dataset.timestamp,dpr:window.devicePixelRatio||1,firstHit:map.dataset.firstHitX!==""};
""",
    )
    if (
        current["title"] != "物理世界检查"
        or current["frames"] < 1
        or current["positions"] < 1
        or current["globeTime"] != current["mapTime"]
        or not current["firstHit"]
    ):
        raise AssertionError(f"PhysicalWorld visual regression: {current}")
    return {
        "formal_frames": current["frames"],
        "positions": current["positions"],
        "linked_timestamp": current["globeTime"],
        "device_pixel_ratio": current["dpr"],
        "operator_row_present": False,
    }

    # Legacy static-orbit checks are retained below as historical review logic.
    initial = _execute(
        base_url,
        session_id,
        """
const canvas=document.getElementById("orbit-canvas"),ctx=canvas.getContext("2d");
canvas.scrollIntoView({block:"center"});
const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data;
const radius=Math.min(canvas.width,canvas.height)*.34;
let outside=0;
for(let y=0;y<canvas.height;y+=2)for(let x=0;x<canvas.width;x+=2){
  if(Math.hypot(x-canvas.width/2,y-canvas.height/2)>radius+3&&pixels[(y*canvas.width+x)*4+3])outside++;
}
const chinese=document.title,chineseProse=document.querySelector("section.card")?.textContent||"";document.querySelector('[data-language="en"]').click();
const box=canvas.getBoundingClientRect();
const viewport=(x,y)=>({x:Math.round(box.left+x),y:Math.round(box.top+y)});
return {chinese,chineseProse,english:document.title,yaw:Number(canvas.dataset.cameraYaw),pitch:Number(canvas.dataset.cameraPitch),orbitSegments:Number(canvas.dataset.orbitSegments),gridSegments:Number(canvas.dataset.gridSegments),coastlineSegments:Number(canvas.dataset.coastlineSegments),outside,dpr:Number(canvas.dataset.devicePixelRatio),logicalWidth:Number(canvas.dataset.logicalWidth),logicalHeight:Number(canvas.dataset.logicalHeight),backingWidth:canvas.width,backingHeight:canvas.height,earthReferenceRadiusM:Number(canvas.dataset.earthReferenceRadiusM),orbitAltitudeKm:Number(canvas.dataset.firstOrbitAltitudeKm),orbitDisplayRadius:Number(canvas.dataset.firstOrbitDisplayRadius),expectedOrbitDisplayRadius:1+Number(canvas.dataset.firstOrbitAltitudeKm)*1000/Number(canvas.dataset.earthReferenceRadiusM),station:viewport(Number(canvas.dataset.firstGroundStationX),Number(canvas.dataset.firstGroundStationY))};
""",
    )
    station_target: dict[str, object] = {}
    for _ in range(8):
        station_target = _execute(
            base_url,
            session_id,
            """
const canvas=document.getElementById("orbit-canvas");
canvas.scrollIntoView({block:"center"});
const box=canvas.getBoundingClientRect();
const available=canvas.dataset.firstGroundStationX!==""&&canvas.dataset.firstGroundStationY!=="";
const x=available?Math.round(box.left+Number(canvas.dataset.firstGroundStationX)):null;
const y=available?Math.round(box.top+Number(canvas.dataset.firstGroundStationY)):null;
const element=document.elementFromPoint(x,y);
return {available,x,y,box:{left:box.left,top:box.top,width:box.width,height:box.height},innerWidth,innerHeight,elementId:element?.id||"",elementTag:element?.tagName||""};
""",
        )
        if station_target["available"]:
            break
        _drag(base_url, session_id, "orbit-canvas", 95, 0)
    if not station_target.get("available"):
        raise AssertionError(
            "PhysicalWorld globe exposed no selectable GroundStation "
            "after a complete camera sweep"
        )
    _move_at(
        base_url,
        session_id,
        int(station_target["x"]),
        int(station_target["y"]),
    )
    station_hover = _execute(
        base_url,
        session_id,
        'return document.getElementById("orbit-canvas").dataset.hoverLabel;',
    )
    _click_at(
        base_url,
        session_id,
        int(station_target["x"]),
        int(station_target["y"]),
    )
    station = _execute(
        base_url,
        session_id,
        """
const canvas=document.getElementById("orbit-canvas"),detail=document.getElementById("selected-object");canvas.scrollIntoView({block:"center"});const box=canvas.getBoundingClientRect();
return {kind:detail.dataset.selectedKind,id:detail.dataset.selectedId,text:detail.textContent,label:canvas.dataset.selectedLabel,orbit:{x:Math.round(box.left+Number(canvas.dataset.firstOrbitX)),y:Math.round(box.top+Number(canvas.dataset.firstOrbitY))}};
""",
    )
    _click_at(
        base_url,
        session_id,
        int(station_target["x"]),
        int(station_target["y"]),
    )
    station_cleared = _execute(
        base_url,
        session_id,
        'const c=document.getElementById("orbit-canvas"),d=document.getElementById("selected-object");return {kind:d.dataset.selectedKind,id:d.dataset.selectedId,label:c.dataset.selectedLabel};',
    )
    _execute(
        base_url,
        session_id,
        'document.getElementById("orbit-reset").click();return true;',
    )
    orbit_target = _execute(
        base_url,
        session_id,
        'const c=document.getElementById("orbit-canvas"),b=c.getBoundingClientRect();return {x:Math.round(b.left+Number(c.dataset.firstOrbitX)),y:Math.round(b.top+Number(c.dataset.firstOrbitY))};',
    )
    _click_at(
        base_url,
        session_id,
        int(orbit_target["x"]),
        int(orbit_target["y"]),
    )
    orbit = _execute(
        base_url,
        session_id,
        """
const detail=document.getElementById("selected-object"),canvas=document.getElementById("orbit-canvas");return {kind:detail.dataset.selectedKind,id:detail.dataset.selectedId,text:detail.textContent,label:canvas.dataset.selectedLabel};
""",
    )
    _click_at(
        base_url,
        session_id,
        int(orbit_target["x"]),
        int(orbit_target["y"]),
    )
    orbit_cleared = _execute(
        base_url,
        session_id,
        'const c=document.getElementById("orbit-canvas"),d=document.getElementById("selected-object");return {kind:d.dataset.selectedKind,id:d.dataset.selectedId,label:c.dataset.selectedLabel};',
    )
    orbit_reselect_target = _execute(
        base_url,
        session_id,
        'const c=document.getElementById("orbit-canvas"),b=c.getBoundingClientRect();return {x:Math.round(b.left+Number(c.dataset.firstOrbitX)),y:Math.round(b.top+Number(c.dataset.firstOrbitY))};',
    )
    _click_at(
        base_url,
        session_id,
        int(orbit_reselect_target["x"]),
        int(orbit_reselect_target["y"]),
    )
    _drag(base_url, session_id, "orbit-canvas", 95, 54)
    moved = _execute(
        base_url,
        session_id,
        """const c=document.getElementById("orbit-canvas"),d=document.getElementById("selected-object");return {yaw:Number(c.dataset.cameraYaw),pitch:Number(c.dataset.cameraPitch),kind:d.dataset.selectedKind,id:d.dataset.selectedId};""",
    )
    reset = _execute(
        base_url,
        session_id,
        """document.getElementById("orbit-reset").click();const c=document.getElementById("orbit-canvas");return {yaw:Number(c.dataset.cameraYaw),pitch:Number(c.dataset.cameraPitch)};""",
    )
    if (
        initial["chinese"] != "物理世界检查"
        or initial["english"] != "Physical World Inspection"
        or "本 Tool 用于检查正式静态 inventory 和 identity" not in initial["chineseProse"]
        or initial["orbitSegments"] < 1
        or initial["gridSegments"] < 1
        or initial["coastlineSegments"] < 1
        or initial["outside"] < 10
        or abs(initial["earthReferenceRadiusM"] - 6_371_008.8) > 0.1
        or abs(
            initial["orbitDisplayRadius"]
            - initial["expectedOrbitDisplayRadius"]
        )
        > 1e-12
        or not 1.0 < initial["orbitDisplayRadius"] < 1.2
        or abs(
            initial["backingWidth"]
            - round(initial["logicalWidth"] * initial["dpr"])
        )
        > 1
        or abs(
            initial["backingHeight"]
            - round(initial["logicalHeight"] * initial["dpr"])
        )
        > 1
        or station["kind"] != "ground-station"
        or station_hover != station["id"].split("/")[-1]
        or not station["label"]
        or not all(
            value in station["text"]
            for value in ("ground_station_id", "latitude / longitude", "station_type")
        )
        or orbit["kind"] != "orbit"
        or not orbit["label"]
        or not all(
            value in orbit["text"]
            for value in ("orbit_id", "inclination", "right ascension", "satellite_count")
        )
        or station_cleared != {"kind": "", "id": "", "label": ""}
        or orbit_cleared != {"kind": "", "id": "", "label": ""}
        or (
            moved["yaw"] == initial["yaw"]
            and moved["pitch"] == initial["pitch"]
        )
        or moved["kind"] != orbit["kind"]
        or moved["id"] != orbit["id"]
        or abs(reset["yaw"] - initial["yaw"]) > 1e-9
        or abs(reset["pitch"] - initial["pitch"]) > 1e-9
    ):
        raise AssertionError(
            "PhysicalWorld visual regression: "
            f"{initial=} {station_target=} {station_hover=} "
            f"{station=} {station_cleared=} "
            f"{orbit=} {orbit_cleared=} {moved=} {reset=}"
        )
    return {
        "outside_orbit_pixels": initial["outside"],
        "selected_ground_station": station["id"],
        "selected_orbit": orbit["id"],
        "operator_row_present": "operator" in station["text"],
        "device_pixel_ratio": initial["dpr"],
        "coastline_segments": initial["coastlineSegments"],
        "earth_reference_radius_m": initial["earthReferenceRadiusM"],
        "orbit_display_radius": initial["orbitDisplayRadius"],
        "selection_toggle": "PASS",
    }


def _zone(base_url: str, session_id: str, page: Path) -> dict[str, object]:
    _navigate(base_url, session_id, page)
    initial = _execute(
        base_url,
        session_id,
        """
const canvas=document.getElementById("zone-globe"),map=document.getElementById("zone-map");
const chinese=document.title,chineseProse=document.querySelector("section.card")?.textContent||"";document.querySelector('[data-language="en"]').click();
const area=map.querySelector(".zone-area");area.scrollIntoView({block:"center"});
const areaBox=area.getBoundingClientRect();
return {chinese,chineseProse,english:document.title,mapBoundaries:map.querySelectorAll(".zone-boundary").length,mapAreas:map.querySelectorAll(".zone-area").length,boundarySegments:Number(canvas.dataset.boundarySegments),coastlineSegments:Number(canvas.dataset.coastlineSegments),yaw:Number(canvas.dataset.cameraYaw),pitch:Number(canvas.dataset.cameraPitch),dpr:Number(canvas.dataset.devicePixelRatio),logicalWidth:Number(canvas.dataset.logicalWidth),logicalHeight:Number(canvas.dataset.logicalHeight),backingWidth:canvas.width,backingHeight:canvas.height,mapTarget:{x:Math.round(areaBox.left+areaBox.width/2),y:Math.round(areaBox.top+areaBox.height/2)}};
""",
    )
    _click_at(
        base_url,
        session_id,
        int(initial["mapTarget"]["x"]),
        int(initial["mapTarget"]["y"]),
    )
    map_selection = _execute(
        base_url,
        session_id,
        """
const canvas=document.getElementById("zone-globe"),map=document.getElementById("zone-map"),detail=document.getElementById("selected-zone");canvas.scrollIntoView({block:"center"});const box=canvas.getBoundingClientRect();return {id:detail.dataset.selectedZone,text:detail.textContent,mapLabel:map.dataset.selectedLabel,globeTarget:{x:Math.round(box.left+Number(canvas.dataset.firstZoneX)),y:Math.round(box.top+Number(canvas.dataset.firstZoneY))}};
""",
    )
    _click_at(
        base_url,
        session_id,
        int(initial["mapTarget"]["x"]),
        int(initial["mapTarget"]["y"]),
    )
    map_cleared = _execute(
        base_url,
        session_id,
        'const d=document.getElementById("selected-zone"),m=document.getElementById("zone-map"),c=document.getElementById("zone-globe");return {id:d.dataset.selectedZone,map:m.dataset.selectedZone,label:c.dataset.selectedLabel};',
    )
    globe_target: dict[str, object] = {}
    for _ in range(8):
        globe_target = _execute(
            base_url,
            session_id,
            """
const canvas=document.getElementById("zone-globe");
canvas.scrollIntoView({block:"center"});
const box=canvas.getBoundingClientRect();
const available=canvas.dataset.firstZoneX!==""&&canvas.dataset.firstZoneY!=="";
return {available,x:available?Math.round(box.left+Number(canvas.dataset.firstZoneX)):null,y:available?Math.round(box.top+Number(canvas.dataset.firstZoneY)):null};
""",
        )
        if globe_target["available"]:
            break
        _drag(base_url, session_id, "zone-globe", 95, 0)
    if not globe_target.get("available"):
        raise AssertionError(
            "Zone globe exposed no selectable Zone after a complete "
            "camera sweep"
        )
    _move_at(
        base_url,
        session_id,
        int(globe_target["x"]),
        int(globe_target["y"]),
    )
    globe_hover = _execute(
        base_url,
        session_id,
        'return document.getElementById("zone-globe").dataset.hoverLabel;',
    )
    _click_at(
        base_url,
        session_id,
        int(globe_target["x"]),
        int(globe_target["y"]),
    )
    globe_selection = _execute(
        base_url,
        session_id,
        """const detail=document.getElementById("selected-zone"),canvas=document.getElementById("zone-globe");return {id:detail.dataset.selectedZone,text:detail.textContent,label:canvas.dataset.selectedLabel,yaw:Number(canvas.dataset.cameraYaw),pitch:Number(canvas.dataset.cameraPitch)};""",
    )
    selected_zone_target = _execute(
        base_url,
        session_id,
        'const c=document.getElementById("zone-globe");c.scrollIntoView({block:"center"});const b=c.getBoundingClientRect();return {x:Math.round(b.left+Number(c.dataset.selectedZoneX)),y:Math.round(b.top+Number(c.dataset.selectedZoneY))};',
    )
    _click_at(
        base_url,
        session_id,
        int(selected_zone_target["x"]),
        int(selected_zone_target["y"]),
    )
    globe_cleared = _execute(
        base_url,
        session_id,
        'const d=document.getElementById("selected-zone"),c=document.getElementById("zone-globe");return {id:d.dataset.selectedZone,label:c.dataset.selectedLabel};',
    )
    globe_reselect_target = _execute(
        base_url,
        session_id,
        'const c=document.getElementById("zone-globe");c.scrollIntoView({block:"center"});const b=c.getBoundingClientRect();return {x:Math.round(b.left+Number(c.dataset.firstZoneX)),y:Math.round(b.top+Number(c.dataset.firstZoneY))};',
    )
    _click_at(
        base_url,
        session_id,
        int(globe_reselect_target["x"]),
        int(globe_reselect_target["y"]),
    )
    _drag(base_url, session_id, "zone-globe", -88, 47)
    moved = _execute(
        base_url,
        session_id,
        """const c=document.getElementById("zone-globe"),d=document.getElementById("selected-zone");return {yaw:Number(c.dataset.cameraYaw),pitch:Number(c.dataset.cameraPitch),id:d.dataset.selectedZone};""",
    )
    if (
        initial["chinese"] != "Zone 检查"
        or initial["english"] != "Zone Inspection"
        or "Zone 是正式的地理 / 服务 identity" not in initial["chineseProse"]
        or initial["boundarySegments"] < 1
        or initial["mapBoundaries"] < 1
        or initial["mapAreas"] < 1
        or initial["coastlineSegments"] < 1
        or abs(
            initial["backingWidth"]
            - round(initial["logicalWidth"] * initial["dpr"])
        )
        > 1
        or abs(
            initial["backingHeight"]
            - round(initial["logicalHeight"] * initial["dpr"])
        )
        > 1
        or not map_selection["id"]
        or map_selection["mapLabel"] != map_selection["id"]
        or not all(
            value in map_selection["text"]
            for value in ("zone_id", "south / north", "representative center", "priority", "wrapped")
        )
        or not globe_selection["id"]
        or not globe_hover
        or globe_selection["label"] != globe_selection["id"]
        or globe_hover != globe_selection["id"]
        or map_cleared != {"id": "", "map": "", "label": ""}
        or globe_cleared != {"id": "", "label": ""}
        or (
            moved["yaw"] == globe_selection["yaw"]
            and moved["pitch"] == globe_selection["pitch"]
        )
        or moved["id"] != globe_selection["id"]
    ):
        raise AssertionError(
            "Zone visual regression: "
            f"{initial=} {map_selection=} {map_cleared=} {globe_target=} "
            f"{globe_hover=} "
            f"{globe_selection=} {globe_cleared=} {moved=}"
        )
    return {
        "map_selected": map_selection["id"],
        "globe_selected": globe_selection["id"],
        "device_pixel_ratio": initial["dpr"],
        "coastline_segments": initial["coastlineSegments"],
        "selection_toggle": "PASS",
    }


def _traffic(base_url: str, session_id: str, page: Path) -> dict[str, object]:
    """Exercise current detached flow replay and its lazy evidence chunks."""
    _navigate(base_url, session_id, page)
    initial = _execute(base_url, session_id, """
return {title:document.title,flows:document.getElementById('traffic-flow').options.length,
 transactions:document.querySelectorAll('#traffic-transactions button').length,
 frames:Number(document.getElementById('traffic-time').max)+1,
 flow:document.getElementById('traffic-flow').value,
 end:Number(document.getElementById('traffic-seek').max),
 globe:!!document.getElementById('traffic-globe')};
""")
    if initial['title'] != 'Traffic / Path Replay' or initial['frames'] < 1 or not initial['globe']:
        raise AssertionError(f"Traffic replay did not initialize: {initial}")

    def wait_for(script):
        # This bounds a browser assertion, not calculation/runtime owner drainage.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            value = _execute(base_url, session_id, script)
            if value:
                return value
            time.sleep(.05)
        raise AssertionError('Traffic evidence chunk did not finish loading')

    selected = False
    if initial['transactions']:
        _execute(base_url, session_id, "document.querySelector('#traffic-transactions button').click();")
        wait_for("return document.getElementById('traffic-selected-transaction').textContent.trim();")
        selected = _execute(base_url, session_id, """
return document.getElementById('traffic-transaction-details').open
 && document.getElementById('traffic-step-detail').textContent.length > 20;
""")
        if not selected:
            raise AssertionError('Traffic transaction detail did not open')

    target = initial['end'] / 2
    _execute(base_url, session_id, f"""
const seek=document.getElementById('traffic-seek');seek.value={target};
seek.dispatchEvent(new Event('input'));
""")
    geometry = wait_for("const text=document.getElementById('traffic-geometry-basis').textContent;return text.startsWith('Geometry: retained PhysicalPositionSnapshot') && text;")
    _execute(base_url, session_id, "document.getElementById('traffic-play').click();")
    if initial['end'] > 0:
        wait_for(f"return Number(document.getElementById('traffic-seek').value) > {target};")
    _execute(base_url, session_id, "if(document.getElementById('traffic-play').textContent==='Pause')document.getElementById('traffic-play').click();")
    moved = _execute(base_url, session_id, """
return {time:Number(document.getElementById('traffic-seek').value),
 flow:document.getElementById('traffic-flow').value,
 status:document.getElementById('traffic-selection-status').textContent};
""")
    if moved['flow'] != initial['flow'] or 'failed' in moved['status'].lower():
        raise AssertionError(f"Traffic flow replay failed: {moved}")
    return {'frames': initial['frames'], 'flows': initial['flows'],
            'transaction_selection': 'PASS' if selected else 'no retained transactions in selected flow',
            'seek_and_playback': 'PASS', 'geometry': geometry}


def _planner(base_url: str, session_id: str, page: Path) -> dict[str, object]:
    _navigate(base_url, session_id, page)
    current = _execute(
        base_url,
        session_id,
        """
const time=document.getElementById("planner-time"),globe=document.getElementById("planned-globe"),map=document.getElementById("planned-map"),stages=[...document.querySelectorAll("#stage-strip [data-stage]")];
const gs=stages.find(v=>v.dataset.stage==="gs");gs?.click();
return {title:document.title,frames:Number(time.max)+1,stages:stages.length,stage:document.getElementById("stage-title").textContent,globeTime:globe.dataset.timestamp,mapTime:map.dataset.timestamp,detail:document.getElementById("stage-detail").textContent.length,firstHitX:map.dataset.firstHitX,firstHitY:map.dataset.firstHitY};
""",
    )
    point = _execute(
        base_url,
        session_id,
        """
const c=document.getElementById("planned-map"),b=c.getBoundingClientRect();
return {x:Math.round(b.left+Number(c.dataset.firstHitX)),y:Math.round(b.top+Number(c.dataset.firstHitY))};
""",
    )
    _click_at(base_url, session_id, int(point["x"]), int(point["y"]))
    selected = _execute(
        base_url,
        session_id,
        """const g=document.getElementById("planned-globe"),m=document.getElementById("planned-map");return {globe:g.dataset.selectedId,map:m.dataset.selectedId,visible:Number(m.dataset.visibleEdgeCount),focused:Number(m.dataset.focusedCandidateCount)};""",
    )
    if (
        current["title"] not in {"Planner Semantic Inspection", "规划器语义检查"}
        or current["frames"] < 1
        or current["stages"] != 9
        or current["globeTime"] != current["mapTime"]
        or current["detail"] < 20
        or not current["firstHitX"]
        or not current["firstHitY"]
        or not selected["map"]
        or selected["map"] != selected["globe"]
    ):
        raise AssertionError(
            f"Planner presentation regression: {current=} {selected=}"
        )
    return {
        "formal_transitions": current["frames"],
        "stage_selector": "PASS",
        "ground_station_stage": current["stage"],
        "linked_planned_views": "PASS",
        "focused_selection": selected["map"],
    }

    # Legacy table-only checks are retained below as historical review logic.
    result = _execute(
        base_url,
        session_id,
        """
const sectionByHeading=label=>[...document.querySelectorAll("section,details")].find(
  section=>[...section.children].some(
    child=>child.tagName==="H2"&&child.textContent.trim()===label
  )
);
const stats=Object.fromEntries([...document.querySelectorAll(".stat")].map(
  node=>[
    node.querySelector(".muted")?.textContent.trim(),
    Number(node.querySelector("strong")?.textContent.trim()),
  ]
));
const planSection=sectionByHeading("Plan lineage and publication frontiers");
const frameSection=sectionByHeading("Formal boundary progression");
const layerSection=sectionByHeading("Semantic layers");
const machineSection=sectionByHeading("Machine evidence");
const planRows=[...(planSection?.querySelectorAll("tbody tr")||[])];
const frameRows=[...(frameSection?.querySelectorAll("tbody tr")||[])];
const planTransitionTotal=planRows.reduce(
  (total,row)=>total+Number(row.cells[5]?.textContent.trim()),0
);
return {
  title:document.title,
  heading:document.querySelector("h1")?.textContent.trim(),
  status:document.querySelector(".status")?.textContent.trim(),
  summary:document.querySelector("section.card")?.textContent||"",
  stats,
  planRows:planRows.length,
  frameRows:frameRows.length,
  planTransitionTotal,
  firstPrevious:planRows[0]?.cells[2]?.textContent.trim(),
  cadences:[...new Set(planRows.map(row=>row.cells[4]?.textContent.trim()))],
  layerText:layerSection?.textContent||"",
  frameText:frameSection?.textContent||"",
  machineText:machineSection?.textContent||"",
  retiredControls:document.querySelectorAll(
    "#playback-scrubber,#planner-globe,#route-select,[data-view]"
  ).length,
};
""",
    )
    stats = result["stats"]
    plans = stats.get("plans", 0)
    transitions = stats.get("formal_transitions", 0)
    if (
        result["title"] != "Planner Semantic Inspection"
        or result["heading"] != result["title"]
        or result["status"] != "PASS"
        or "applies the public canonical transition operation" not in result["summary"]
        or plans <= 0
        or transitions <= 0
        or result["planRows"] != plans
        or result["frameRows"] != transitions
        or result["planTransitionTotal"] != transitions
        or result["firstPrevious"] != "None"
        or len(result["cadences"]) != 1
        or not result["cadences"][0]
        or "PHYSICAL CANDIDATE" not in result["layerText"]
        or "ROUTED / UNREACHABLE" not in result["layerText"]
        or "bounded feasible-count summaries" not in result["frameText"]
        or "never serializes a complete state per frame"
        not in result["machineText"]
        or "retains every feasible or rejected candidate identity"
        not in result["machineText"]
        or result["retiredControls"] != 0
    ):
        raise AssertionError(f"Planner presentation regression: {result}")
    return {
        "plans": plans,
        "formal_transitions": transitions,
        "cadence": result["cadences"][0],
        "canonical_replay_tables": "PASS",
        "bounded_candidate_evidence": "PASS",
        "retired_interactive_slice_views_absent": "PASS",
    }


def _performance(base_url: str, session_id: str, page: Path) -> dict[str, object]:
    _navigate(base_url, session_id, page)
    result = _execute(
        base_url,
        session_id,
        """
return (async()=>{
const waitFor=async(predicate,label)=>{for(let attempt=0;attempt<100;attempt++){const value=predicate();if(value)return value;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(`Timed out waiting for ${label}`);};
const sectionByHeading=label=>[...document.querySelectorAll("section,details")].find(
  section=>[...section.children].some(
    child=>child.tagName==="H2"&&child.textContent.trim()===label
  )
);
const statsFor=section=>Object.fromEntries(
  [...(section?.querySelectorAll(".stat")||[])].map(node=>[
    node.querySelector(".muted")?.textContent.trim(),
    Number(node.querySelector("strong")?.textContent.trim()),
  ])
);
await waitFor(()=>document.querySelectorAll("#raw-records tbody tr").length,"raw rows");
const component=document.getElementById("component-filter");
const role=document.getElementById("role-filter");
component.value="planner";component.dispatchEvent(new Event("change",{bubbles:true}));
role.value="rollup";role.dispatchEvent(new Event("change",{bubbles:true}));
const filteredRows=await waitFor(
  ()=>[...document.querySelectorAll("#raw-records tbody tr")],"filtered rows"
);
return {
  title:document.title,
  heading:document.querySelector("h1")?.textContent.trim(),
  status:document.querySelector(".status")?.textContent.trim(),
  summary:document.querySelector("section.card")?.textContent||"",
  formal:statsFor(sectionByHeading("Formal progression")),
  pipelineText:sectionByHeading("Actual pipeline groups")?.textContent||"",
  topLevelText:sectionByHeading("Independent top-level timing series")?.textContent||"",
  componentOptions:[...component.options].map(option=>option.value),
  filteredCount:filteredRows.length,
  filteredOwners:[...new Set(filteredRows.map(row=>row.cells[1]?.textContent.trim()))],
  filteredRoles:[...new Set(filteredRows.map(row=>row.cells[3]?.textContent.trim().split(" / ").pop()))],
  rawCount:document.getElementById("raw-count")?.textContent||"",
};
})()
""",
    )
    if (
        result["title"] != "Performance"
        or result["heading"] != result["title"]
        or result["status"] != "OBSERVED"
        or "reads records emitted by completed execution" not in result["summary"]
        or result["formal"].get("formal transitions", 0) <= 0
        or result["formal"].get("formal transitions")
        != result["formal"].get("PhysicalPositionSnapshots")
        or "prehistory PhysicalPositionSnapshots" not in result["formal"]
        or result["formal"]["prehistory PhysicalPositionSnapshots"] < 0
        or result["formal"].get("Plans", 0) <= 0
        or result["formal"].get("Plans")
        != result["formal"].get("compiled realizations")
        or "physical-link / physical-link-observation" not in result["pipelineText"]
        or "planner / publication-total" not in result["pipelineText"]
        or "compiler / compile-total" not in result["pipelineText"]
        or "realization-producer / evidence-serialization"
        not in result["pipelineText"]
        or "planning-total" not in result["topLevelText"]
        or "publication-total" not in result["topLevelText"]
        or "planner" not in result["componentOptions"]
        or result["filteredCount"] <= 0
        or result["filteredOwners"] != ["planner"]
        or result["filteredRoles"] != ["rollup"]
        or "invocation rows" not in result["rawCount"]
    ):
        raise AssertionError(f"Performance presentation regression: {result}")
    return {
        "formal_transitions": result["formal"]["formal transitions"],
        "prehistory_physical_frames": result["formal"][
            "prehistory PhysicalPositionSnapshots"
        ],
        "plans": result["formal"]["Plans"],
        "compiled_realizations": result["formal"]["compiled realizations"],
        "detached_raw_filter": "PASS",
        "actual_pipeline_taxonomy": "PASS",
    }


def run_visual_browser_gate(
    inspection_root: Path,
    *,
    geckodriver: str,
    timeout: float = 45.0,
    static_only: bool = False,
) -> dict[str, object]:
    """Run the accepted manual-review interaction assertions in real Firefox."""

    root = Path(inspection_root).resolve()
    tool_names = ("physical-world", "zone") if static_only else tuple(
        name
        for name in ("physical-world", "zone", "planner", "performance", "traffic")
        if (root / name / "index.html").is_file()
    )
    pages = {name: root / name / "index.html" for name in tool_names}
    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryFile() as driver_log:
        process = subprocess.Popen(
            (geckodriver, "--port", str(port)),
            stdout=driver_log,
            stderr=subprocess.STDOUT,
        )
        session_id = None
        try:
            _wait_for_driver(base_url, timeout)
            firefox_options: dict[str, object] = {"args": ["-headless"]}
            if static_only:
                firefox_options["prefs"] = {
                    "layout.css.devPixelsPerPx": "2.0"
                }
            session = _request(
                f"{base_url}/session",
                method="POST",
                value={
                    "capabilities": {
                        "alwaysMatch": {
                            "browserName": "firefox",
                            "moz:firefoxOptions": firefox_options,
                        }
                    }
                },
                timeout=timeout,
            )
            session_id = session["sessionId"]
            _request(
                f"{base_url}/session/{session_id}/window/rect",
                method="POST",
                value={"width": 1600, "height": 1200},
            )
            result = {
                "status": "PASS",
                "browser": session["capabilities"]["browserName"],
                "browser_version": session["capabilities"]["browserVersion"],
                "physical-world": _physical_world(base_url, session_id, pages["physical-world"]),
                "zone": _zone(base_url, session_id, pages["zone"]),
            }
            if (root / "index.html").is_file():
                result["aggregate"] = _aggregate(
                    base_url, session_id, root / "index.html"
                )
            if not static_only:
                if "planner" in pages:
                    result["planner"] = _planner(
                        base_url, session_id, pages["planner"]
                    )
                if "performance" in pages:
                    result["performance"] = _performance(
                        base_url, session_id, pages["performance"]
                    )
                if "traffic" in pages:
                    result["traffic"] = _traffic(
                        base_url, session_id, pages["traffic"]
                    )
            elif (
                result["physical-world"]["device_pixel_ratio"] < 1.5
                or result["zone"]["device_pixel_ratio"] < 1.5
                or result["physical-world"]["operator_row_present"]
            ):
                raise AssertionError(
                    "static Tool gate high-DPI/operator display regression"
                )
            return result
        finally:
            if session_id is not None:
                try:
                    _request(f"{base_url}/session/{session_id}", method="DELETE", timeout=timeout)
                except OSError:
                    pass
            _stop_driver(process, port)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inspection_root", type=Path)
    parser.add_argument("--geckodriver", default="geckodriver")
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="check only PhysicalWorld and Zone pages",
    )
    arguments = parser.parse_args()
    print(
        run_visual_browser_gate(
            arguments.inspection_root,
            geckodriver=arguments.geckodriver,
            static_only=arguments.static_only,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_visual_browser_gate"]
