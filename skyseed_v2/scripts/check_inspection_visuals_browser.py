#!/usr/bin/env python3
"""Exercise the four manually reviewed inspection visual fixes in Firefox."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile
import time

try:
    from scripts.check_slice_tool_browser import (
        _available_port,
        _execute,
        _request,
        _stop_driver,
        _wait_for_driver,
    )
except ModuleNotFoundError:
    from check_slice_tool_browser import (  # type: ignore[no-redef]
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
    time.sleep(0.15)


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
        result["chinese"] != "Skyseed v2 工具总览"
        or result["english"] != "Skyseed v2 Tool Overview"
        or "阅读顺序从正式结构与采样 Slice evidence 开始" not in result["prose"]
        or "所有 Tool 均分离且只读" not in result["prose"]
    ):
        raise AssertionError(f"Aggregate Chinese explanation regression: {result}")
    return {"chinese_explanation": "PASS"}


def _topology(base_url: str, session_id: str, page: Path) -> dict[str, object]:
    _navigate(base_url, session_id, page)
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
    _move_at(
        base_url,
        session_id,
        int(initial["station"]["x"]),
        int(initial["station"]["y"]),
    )
    station_hover = _execute(
        base_url,
        session_id,
        'return document.getElementById("orbit-canvas").dataset.hoverLabel;',
    )
    _click_at(
        base_url,
        session_id,
        int(initial["station"]["x"]),
        int(initial["station"]["y"]),
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
        int(initial["station"]["x"]),
        int(initial["station"]["y"]),
    )
    station_cleared = _execute(
        base_url,
        session_id,
        'const c=document.getElementById("orbit-canvas"),d=document.getElementById("selected-object");return {kind:d.dataset.selectedKind,id:d.dataset.selectedId,label:c.dataset.selectedLabel};',
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
        initial["chinese"] != "拓扑检查"
        or initial["english"] != "Topology Inspection"
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
            "Topology visual regression: "
            f"{initial=} {station_hover=} {station=} {station_cleared=} "
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
    _move_at(
        base_url,
        session_id,
        int(map_selection["globeTarget"]["x"]),
        int(map_selection["globeTarget"]["y"]),
    )
    globe_hover = _execute(
        base_url,
        session_id,
        'return document.getElementById("zone-globe").dataset.hoverLabel;',
    )
    _click_at(
        base_url,
        session_id,
        int(map_selection["globeTarget"]["x"]),
        int(map_selection["globeTarget"]["y"]),
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
            f"{initial=} {map_selection=} {map_cleared=} {globe_hover=} "
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
    _navigate(base_url, session_id, page)
    identity = _execute(
        base_url,
        session_id,
        """
const canvas=document.getElementById("globe");
const chinese=document.title,chineseProse=document.querySelector("section.card")?.textContent||"";document.querySelector('[data-language="en"]').click();
return {chinese,english:document.title};
""",
    )
    def camera() -> dict[str, float]:
        return _execute(
            base_url,
            session_id,
            """const c=document.getElementById("globe");return {lat:Number(c.dataset.cameraLatitude),lon:Number(c.dataset.cameraLongitude)};""",
        )

    _drag(base_url, session_id, "globe", 0, -400)
    first = camera()
    _drag(base_url, session_id, "globe", 0, -300)
    farther = camera()
    _drag(base_url, session_id, "globe", 0, 20)
    reversed_value = camera()
    _drag(base_url, session_id, "globe", 80, 0)
    horizontal = camera()
    result = {
        **identity,
        "first": first,
        "farther": farther,
        "reversed": reversed_value,
        "horizontal": horizontal,
        "maximum": 89 * 3.141592653589793 / 180,
    }
    if (
        result["chinese"] != "Skyseed 流量 / 路径回放"
        or result["english"] != "Skyseed Traffic / Path Replay"
        or abs(result["first"]["lat"] - result["maximum"]) > 1e-9
        or result["farther"] != result["first"]
        or not result["reversed"]["lat"] < result["farther"]["lat"]
        or result["reversed"]["lon"] != result["farther"]["lon"]
        or result["horizontal"]["lon"] == result["reversed"]["lon"]
    ):
        raise AssertionError(f"Traffic camera regression: {result}")
    return result


def _planner(base_url: str, session_id: str, page: Path) -> dict[str, object]:
    _navigate(base_url, session_id, page)
    result = _execute(
        base_url,
        session_id,
        """
return (async()=>{
const waitFor=async(predicate,label)=>{for(let attempt=0;attempt<100;attempt++){const value=predicate();if(value)return value;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(`Timed out waiting for ${label}`);};
const chinese=document.title,chineseProse=document.querySelector("section.card")?.textContent||"";document.querySelector('[data-language="en"]').click();
const parameter=document.getElementById("parameter-overview").textContent;
document.querySelector('[data-view="overview"]').click();
const playback=document.getElementById("playback-scrubber"),speed=document.getElementById("playback-speed"),globe=document.getElementById("planner-globe");
const startPlayback=Number(playback.value),navigationEntries=performance.getEntriesByType("navigation").length,locationBefore=location.href,cameraBefore=[globe.dataset.cameraYaw,globe.dataset.cameraPitch];
speed.value="4";window.scrollTo(0,document.getElementById("spatial-context").offsetTop+120);await new Promise(resolve=>setTimeout(resolve,50));const scrollBefore=window.scrollY;
document.getElementById("playback-toggle").click();
await waitFor(()=>Number(playback.value)>=Math.min(Number(playback.max),startPlayback+2),"Planner playback progression");
document.getElementById("playback-toggle").click();await new Promise(resolve=>setTimeout(resolve,100));
const playbackEvidence={start:startPlayback,end:Number(playback.value),maximum:Number(playback.max),scrollBefore,scrollAfter:window.scrollY,activeView:document.querySelector(".tab.active")?.dataset.view,state:document.body.dataset.plannerPlayback,navigationEntriesBefore:navigationEntries,navigationEntriesAfter:performance.getEntriesByType("navigation").length,locationStable:location.href===locationBefore,cameraBefore,cameraAfter:[globe.dataset.cameraYaw,globe.dataset.cameraPitch]};
document.querySelector('[data-view="forwarding"]').click();
const route=document.getElementById("route-select");
await waitFor(()=>route.options.length,"Forwarding catalog");
const scenario=window.SKYSEED_PLANNER_MANIFEST.configuration.scenario_identity;
const canonical=[...route.options].find(value=>value.textContent.includes("O1-S1")&&value.textContent.includes("GS-Marsala"))||route.options[0];
route.value=canonical.value;route.dispatchEvent(new Event("change",{bubbles:true}));
const event=await waitFor(()=>document.getElementById("route-event"),"route events"),first=event.options[0].value;
event.value=first;event.dispatchEvent(new Event("change",{bubbles:true}));
await waitFor(()=>document.querySelector("[data-selected-precedence]"),"first precedence detail");
const firstContext=document.getElementById("route-precedence").textContent;
const firstSelected=document.querySelector("[data-selected-precedence]").dataset.selectedPrecedence;
const remove=[...event.options].find(value=>value.textContent.includes("REMOVED"));
if(remove){event.value=remove.value;event.dispatchEvent(new Event("change",{bubbles:true}));await waitFor(()=>document.querySelector("[data-selected-precedence]")?.dataset.selectedPrecedence===remove.value,"removed precedence detail");}
const removeContext=document.getElementById("route-precedence").textContent;
const removeSelected=document.querySelector("[data-selected-precedence]").dataset.selectedPrecedence;
const zoneRoute=[...route.options].find(value=>value.textContent.includes("Z:"));
route.value=zoneRoute.value;route.dispatchEvent(new Event("change",{bubbles:true}));
await waitFor(()=>document.getElementById("route-event"),"Zone route events");document.querySelector('[data-side="after"]').click();
await waitFor(()=>document.getElementById("spatial-detail").textContent.includes("Current service/path-terminal satellite"),"Zone spatial detail");
const zoneDetail=document.getElementById("spatial-detail").textContent;
const groundRoute=[...route.options].find(value=>!value.textContent.includes("Z:"));
route.value=groundRoute.value;route.dispatchEvent(new Event("change",{bubbles:true}));
await waitFor(()=>document.getElementById("spatial-detail").textContent.includes("Semantic target: GroundStation"),"GroundStation spatial detail");
const groundDetail=document.getElementById("spatial-detail").textContent;
return {scenario,chinese,chineseProse,english:document.title,parameter,playbackEvidence,firstContext,removeContext,firstSelected,removeSelected,hadRemove:Boolean(remove),zoneDetail,groundDetail};
})()
""",
    )
    common_failure = (
        result["chinese"] != "Planner 检查工具"
        or result["english"] != "Planner Inspection Tool"
        or "当前 Planner 组合" not in result["chineseProse"]
        or "Planning window 决定每次发布消费" not in result["chineseProse"]
        or "Publication batching" not in result["parameter"]
        or not result["firstSelected"]
        or "Semantic target" not in result["zoneDetail"]
        or "Current service/path-terminal satellite" not in result["zoneDetail"]
        or "Semantic target: GroundStation" not in result["groundDetail"]
        or "no Zone footprint is expected" not in result["groundDetail"]
        or result["playbackEvidence"]["end"]
        < min(
            result["playbackEvidence"]["start"] + 2,
            result["playbackEvidence"]["maximum"],
        )
        or abs(
            result["playbackEvidence"]["scrollAfter"]
            - result["playbackEvidence"]["scrollBefore"]
        )
        > 3
        or result["playbackEvidence"]["activeView"] != "overview"
        or result["playbackEvidence"]["state"] != "paused"
        or not result["playbackEvidence"]["locationStable"]
        or result["playbackEvidence"]["navigationEntriesAfter"]
        != result["playbackEvidence"]["navigationEntriesBefore"]
        or result["playbackEvidence"]["cameraAfter"]
        != result["playbackEvidence"]["cameraBefore"]
    )
    mini_failure = result["scenario"] == "mini_satellite_internet" and (
        not all(
            value in result["parameter"]
            for value in ("2530", "1590", "940", "Parameter planning")
        )
        or not result["hadRemove"]
        or result["firstSelected"] == result["removeSelected"]
        or result["firstContext"] == result["removeContext"]
        or "BEFORE next hop" not in result["removeContext"]
    )
    if common_failure or mini_failure:
        raise AssertionError(f"Planner presentation regression: {result}")
    return {
        "scenario": result["scenario"],
        "selected_first": result["firstSelected"],
        "selected_remove": result["removeSelected"] if result["hadRemove"] else None,
        "zone_spatial": "PASS",
        "ground_station_spatial": "PASS",
        "playback_scroll_navigation_camera": result["playbackEvidence"],
    }


def _observation(base_url: str, session_id: str, page: Path) -> dict[str, object]:
    _navigate(base_url, session_id, page)
    initial = _execute(
        base_url,
        session_id,
        """
return (async()=>{
const waitFor=async(predicate,label)=>{for(let attempt=0;attempt<100;attempt++){const value=predicate();if(value)return value;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(`Timed out waiting for ${label}`);};
const chinese=document.title,chineseProse=document.querySelector("section.card")?.textContent||"";document.querySelector('[data-language="en"]').click();
const point=await waitFor(()=>document.querySelector(".timeline-point"),"Observation timeline point");
point.scrollIntoView({block:"center"});const box=point.getBoundingClientRect();point.dispatchEvent(new MouseEvent("click",{bubbles:true}));
return {chinese,chineseProse,english:document.title,x:Math.round(box.left+box.width/2),y:Math.round(box.top+box.height/2)};
})()
""",
    )
    _click_at(base_url, session_id, initial["x"], initial["y"])
    result = _execute(
        base_url,
        session_id,
        """
return (async()=>{
const waitFor=async(predicate,label)=>{for(let attempt=0;attempt<100;attempt++){const value=predicate();if(value)return value;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(`Timed out waiting for ${label}`);};
await waitFor(()=>!document.getElementById("selected-label").textContent.startsWith("Select a chart"),"selected observation detail");
const annotation=document.getElementById("persistent-annotation");if(!annotation)throw new Error(`Missing persistent annotation after selection: ${document.getElementById("selected-label").textContent}`);
const annotationText=annotation.textContent,selectedLabel=document.getElementById("selected-label").textContent;
document.getElementById("clear-annotation").click();
const cleared=!document.getElementById("persistent-annotation");
document.getElementById("mode-unit").click();
const component=await waitFor(()=>document.getElementById("unit-component"),"unit component selector");
component.value="planner";component.dispatchEvent(new Event("change",{bubbles:true}));
const heatCell=await waitFor(()=>document.querySelector("#unit-heatmap [data-heatmap-sequence]"),"Planner heatmap cell");
heatCell.click();
const unitSummary=document.getElementById("unit-summary").textContent,heatmapNote=document.getElementById("heatmap-note").textContent,unitCount=document.getElementById("unit-selector").options.length;
return {annotationText,selectedLabel,cleared,unitSummary,heatmapNote,unitCount,heatmapRows:document.querySelectorAll("#unit-heatmap .heatmap-row").length};
})()
""",
    )
    result.update({"chinese": initial["chinese"], "chineseProse": initial["chineseProse"], "english": initial["english"]})
    if (
        result["chinese"] != "Skyseed v2 系统 Observation"
        or result["english"] != "Skyseed v2 System Observation"
        or "当前 Observation 策略" not in result["chineseProse"]
        or "只读取已持久化记录" not in result["chineseProse"]
        or not result["annotationText"]
        or not result["selectedLabel"]
        or not result["cleared"]
        or result["unitCount"] < 2
        or result["heatmapRows"] < 2
        or "shared planner leaf scale" not in result["heatmapNote"]
        or "publication" not in result["unitSummary"].lower()
    ):
        raise AssertionError(f"Observation presentation regression: {result}")
    return {
        "persistent_point_annotation": "PASS",
        "planner_publication_units": result["unitCount"],
        "heatmap_rows": result["heatmapRows"],
        "clear_annotation": "PASS",
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
    tool_names = ("topology", "zone") if static_only else tuple(
        name
        for name in ("topology", "zone", "planner", "observation", "traffic")
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
                "topology": _topology(base_url, session_id, pages["topology"]),
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
                if "observation" in pages:
                    result["observation"] = _observation(
                        base_url, session_id, pages["observation"]
                    )
                if "traffic" in pages:
                    result["traffic"] = _traffic(
                        base_url, session_id, pages["traffic"]
                    )
            elif (
                result["topology"]["device_pixel_ratio"] < 1.5
                or result["zone"]["device_pixel_ratio"] < 1.5
                or result["topology"]["operator_row_present"]
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
        help="check only Topology and Zone pages",
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
