#!/usr/bin/env python3
"""Mechanically exercise commit-prep inspection pages in headless Firefox."""

from __future__ import annotations

import argparse
import json
from math import atan2, degrees, hypot
from pathlib import Path
import subprocess
import tempfile
import time

try:
    from scripts._webdriver import (
        _available_port,
        _capture_screenshot,
        _execute,
        _request,
        _stop_driver,
        _wait_for_driver,
    )
except ModuleNotFoundError:
    from _webdriver import (  # type: ignore[no-redef]
        _available_port,
        _capture_screenshot,
        _execute,
        _request,
        _stop_driver,
        _wait_for_driver,
    )


def _navigate(base: str, session: str, page: Path) -> None:
    if not page.is_file():
        raise FileNotFoundError(page)
    _request(
        f"{base}/session/{session}/url",
        method="POST",
        value={"url": page.resolve().as_uri()},
    )
    time.sleep(0.6)


def _click_first_canvas_point(
    base: str,
    session: str,
    canvas_id: str,
    *,
    x_dataset: str = "firstHitX",
    y_dataset: str = "firstHitY",
) -> None:
    point = _execute(
        base,
        session,
        f"""
const canvas=document.getElementById({canvas_id!r});canvas.scrollIntoView({{block:"center"}});
const box=canvas.getBoundingClientRect(),rawX=canvas.dataset[{x_dataset!r}],rawY=canvas.dataset[{y_dataset!r}];return {{x:Math.round(box.left+Number(rawX)),y:Math.round(box.top+Number(rawY)),rawX,rawY,left:box.left,top:box.top}};
""",
    )
    if not all(isinstance(point.get(key), int) for key in ("x", "y")):
        raise AssertionError(f"invalid canvas hit point: {canvas_id}: {point}")
    _request(
        f"{base}/session/{session}/actions",
        method="POST",
        value={
            "actions": [
                {
                    "type": "pointer",
                    "id": "commit-prep-click",
                    "parameters": {"pointerType": "mouse"},
                    "actions": [
                        {
                            "type": "pointerMove",
                            "duration": 0,
                            "origin": "viewport",
                            "x": point["x"],
                            "y": point["y"],
                        },
                        {"type": "pointerDown", "button": 0},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        },
    )


def _drag_canvas(
    base: str,
    session: str,
    canvas_id: str,
    *,
    dx: int,
    dy: int,
) -> None:
    point = _execute(
        base,
        session,
        f"""
const canvas=document.getElementById({canvas_id!r});canvas.scrollIntoView({{block:'center'}});const box=canvas.getBoundingClientRect();return {{x:Math.round(box.left+box.width/2),y:Math.round(box.top+box.height/2)}};
""",
    )
    _request(
        f"{base}/session/{session}/actions",
        method="POST",
        value={
            "actions": [
                {
                    "type": "pointer",
                    "id": f"drag-{canvas_id}",
                    "parameters": {"pointerType": "mouse"},
                    "actions": [
                        {
                            "type": "pointerMove",
                            "duration": 0,
                            "origin": "viewport",
                            "x": point["x"],
                            "y": point["y"],
                        },
                        {"type": "pointerDown", "button": 0},
                        {
                            "type": "pointerMove",
                            "duration": 180,
                            "origin": "viewport",
                            "x": point["x"] + dx,
                            "y": point["y"] + dy,
                        },
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        },
    )


def _click_geographic_canvas_point(
    base: str,
    session: str,
    canvas_id: str,
    *,
    latitude_deg: float,
    longitude_deg: float,
) -> None:
    point = _execute(
        base,
        session,
        f"""
const canvas=document.getElementById({canvas_id!r});canvas.scrollIntoView({{block:'center'}});
const box=canvas.getBoundingClientRect();return {{x:Math.round(box.left+({longitude_deg!r}+180)/360*box.width),y:Math.round(box.top+(90-({latitude_deg!r}))/180*box.height)}};
""",
    )
    if not all(isinstance(point.get(key), int) for key in ("x", "y")):
        raise AssertionError(f"invalid geographic point: {canvas_id}: {point}")
    _request(
        f"{base}/session/{session}/actions",
        method="POST",
        value={
            "actions": [
                {
                    "type": "pointer",
                    "id": "commit-prep-geographic-click",
                    "parameters": {"pointerType": "mouse"},
                    "actions": [
                        {
                            "type": "pointerMove",
                            "duration": 0,
                            "origin": "viewport",
                            "x": point["x"],
                            "y": point["y"],
                        },
                        {"type": "pointerDown", "button": 0},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        },
    )


def _physical_world(base: str, session: str, page: Path) -> dict[str, object]:
    _navigate(base, session, page)
    initial = _execute(
        base,
        session,
        """
const globe=document.getElementById("globe-canvas"),map=document.getElementById("map-canvas"),time=document.getElementById("time");
return {title:document.title,frames:Number(time.max)+1,globeTime:globe.dataset.timestamp,mapTime:map.dataset.timestamp,positions:Number(globe.dataset.positionCount),candidates:Number(globe.dataset.candidateCount),firstHit:map.dataset.firstHitX!=="",firstHitX:map.dataset.firstHitX,firstHitY:map.dataset.firstHitY,errors:window.SKYSEED_PAGE_ERRORS||[]};
""",
    )
    if not initial["firstHit"] or any(
        value in {None, "NaN", "undefined", ""}
        for value in (initial["firstHitX"], initial["firstHitY"])
    ):
        raise AssertionError(f"PhysicalWorld frame chunk did not render: {initial=}")
    _click_first_canvas_point(base, session, "map-canvas")
    selected = _execute(
        base,
        session,
        """
const globe=document.getElementById("globe-canvas"),map=document.getElementById("map-canvas"),detail=document.getElementById("detail"),time=document.getElementById("time");
if(Number(time.max)>0){time.value="1";time.dispatchEvent(new Event("input",{bubbles:true}));}
return {detail:detail.dataset.selectedId,globeSelected:globe.dataset.selectedId,mapSelected:map.dataset.selectedId,globeTime:globe.dataset.timestamp,mapTime:map.dataset.timestamp,frame:Number(time.value)};
""",
    )
    time.sleep(0.5)
    _drag_canvas(base, session, "map-canvas", dx=38, dy=21)
    mechanics = _execute(
        base,
        session,
        """
const globe=document.getElementById('globe-canvas'),map=document.getElementById('map-canvas'),beforeGlobe=Number(globe.dataset.cameraZoom),beforeMap=Number(map.dataset.mapZoom),widthRatio=globe.getBoundingClientRect().width/map.getBoundingClientRect().width;
globe.dispatchEvent(new WheelEvent('wheel',{deltaY:-120,clientX:100,clientY:100,bubbles:true,cancelable:true}));map.dispatchEvent(new WheelEvent('wheel',{deltaY:-120,clientX:100,clientY:100,bubbles:true,cancelable:true}));document.querySelector('[data-layer="physical-link"]').click();const relation=document.querySelector('#relations [data-relation]');relation?.click();const linkedRelation=relation?{globe:globe.dataset.selectedRelation,map:map.dataset.selectedRelation,detail:document.getElementById('detail').dataset.selectedRelation,pressed:document.querySelector('#relations [data-relation][aria-pressed="true"]')?.getAttribute('aria-pressed')||''}:null;document.querySelector('[data-layer="zone"]').click();const zones=Number(globe.dataset.zoneBoundaryCount);window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));return {beforeGlobe,afterGlobe:Number(globe.dataset.cameraZoom),beforeMap,afterMap:Number(map.dataset.mapZoom),mapPan:map.dataset.mapPan,widthRatio,linkedRelation,zones,cleared:!map.dataset.selectedId&&!map.dataset.selectedRelation,modelText:document.getElementById('model').textContent.length,errors:window.SKYSEED_PAGE_ERRORS||[]};
""",
    )
    if (
        initial["title"] != "物理世界检查"
        or initial["frames"] < 1
        or initial["positions"] < 1
        or not initial["firstHit"]
        or initial["globeTime"] != initial["mapTime"]
        or not selected["detail"]
        or selected["detail"] != selected["globeSelected"]
        or selected["detail"] != selected["mapSelected"]
        or selected["globeTime"] != selected["mapTime"]
        or mechanics["afterGlobe"] <= mechanics["beforeGlobe"]
        or mechanics["afterMap"] <= mechanics["beforeMap"]
        or mechanics["mapPan"] == "0,0"
        or not 0.85 <= mechanics["widthRatio"] <= 1.15
        or mechanics["zones"] < 1
        or not mechanics["cleared"]
        or (
            mechanics["linkedRelation"] is not None
            and (
                not mechanics["linkedRelation"]["globe"]
                or mechanics["linkedRelation"]["globe"]
                != mechanics["linkedRelation"]["map"]
                or mechanics["linkedRelation"]["globe"]
                != mechanics["linkedRelation"]["detail"]
                or mechanics["linkedRelation"]["pressed"] != "true"
            )
        )
        or mechanics["errors"]
    ):
        raise AssertionError(
            f"PhysicalWorld browser mechanics failed: "
            f"{initial=} {selected=} {mechanics=}"
        )
    _execute(
        base,
        session,
        "const time=document.getElementById('time');time.value='0';time.dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('reset-view').click();document.querySelector('[data-layer=\"physical-link\"]').click();",
    )
    time.sleep(0.5)
    first_chunk = json.loads((page.parent / "data/frame-000.js").read_text(encoding="utf-8").split("=", 1)[1].rstrip(";\n"))
    candidates = first_chunk["candidates"]
    pairs = candidates["ground_station"] or candidates["inter_satellite"]
    if not pairs:
        raise AssertionError("relation reselection fixture requires a physical link candidate")
    endpoint = pairs[0][1]
    _, x, y, z = next(position for position in first_chunk["positions"] if position[0] == endpoint)
    _click_geographic_canvas_point(base, session, "map-canvas",
                                  latitude_deg=degrees(atan2(z, hypot(x, y))),
                                  longitude_deg=degrees(atan2(y, x)))
    reselected = _execute(
        base,
        session,
        """
const globe=document.getElementById('globe-canvas'),map=document.getElementById('map-canvas'),relation=document.querySelector('#relations [data-relation]');relation?.click();const detail=document.getElementById('detail'),pressed=document.querySelector('#relations [data-relation][aria-pressed="true"]');return {globe:globe.dataset.selectedRelation,map:map.dataset.selectedRelation,detail:detail.dataset.selectedRelation,pressed:pressed?.getAttribute('aria-pressed')||'',errors:window.SKYSEED_PAGE_ERRORS||[]};
""",
    )
    if (
        not reselected["globe"]
        or reselected["globe"] != reselected["map"]
        or reselected["globe"] != reselected["detail"]
        or reselected["pressed"] != "true"
        or reselected["errors"]
    ):
        raise AssertionError(
            f"PhysicalWorld relation reselection failed: {reselected=}"
        )
    return {
        **initial,
        "selected": selected["detail"],
        "linked_frame": selected["frame"],
        "mechanics": mechanics,
        "reselected_relation": reselected["globe"],
    }


def _planner(base: str, session: str, page: Path) -> dict[str, object]:
    _navigate(base, session, page)
    initial = _execute(
        base,
        session,
        """
const time=document.getElementById("planner-time"),globe=document.getElementById("planned-globe"),map=document.getElementById("planned-map");
return {title:document.title,frames:Number(time.max)+1,stages:document.querySelectorAll("#stage-strip [data-stage]").length,globeTime:globe.dataset.timestamp,mapTime:map.dataset.timestamp,detail:document.getElementById("stage-detail").textContent.length,firstHit:map.dataset.firstHitX!==""};
""",
    )
    _click_first_canvas_point(base, session, "planned-map")
    selected = _execute(
        base,
        session,
        """
const time=document.getElementById("planner-time"),globe=document.getElementById("planned-globe"),map=document.getElementById("planned-map");
const selected=map.dataset.selectedId;if(Number(time.max)>0){time.value=String(time.max);time.dispatchEvent(new Event("input",{bubbles:true}));}
return {selected,globeSelected:globe.dataset.selectedId,mapSelected:map.dataset.selectedId,globeTime:globe.dataset.timestamp,mapTime:map.dataset.timestamp,stages:document.querySelectorAll("#stage-strip [data-stage]").length,detail:document.getElementById("stage-detail").textContent.length};
""",
    )
    time.sleep(0.5)
    _drag_canvas(base, session, "planned-map", dx=34, dy=19)
    mechanics = _execute(
        base,
        session,
        """
const globe=document.getElementById('planned-globe'),map=document.getElementById('planned-map'),beforeGlobe=Number(globe.dataset.cameraZoom),beforeMap=Number(map.dataset.mapZoom),widthRatio=globe.getBoundingClientRect().width/map.getBoundingClientRect().width;
globe.dispatchEvent(new WheelEvent('wheel',{deltaY:-120,clientX:100,clientY:100,bubbles:true,cancelable:true}));map.dispatchEvent(new WheelEvent('wheel',{deltaY:-120,clientX:100,clientY:100,bubbles:true,cancelable:true}));document.querySelector('[data-stage="gs"]').click();const relation=document.querySelector('#special [data-gs-relation]');relation?.click();const linkedRelation=relation?{globe:globe.dataset.selectedRelation,map:map.dataset.selectedRelation,pressed:document.querySelector('#special [data-gs-relation][aria-pressed="true"]')?.getAttribute('aria-pressed')||''}:null;document.querySelector('[data-gs-step="validation"]').click();const validation=document.getElementById('special').textContent,substeps=document.querySelectorAll('[data-gs-step]').length,peerValidation=!!document.querySelector('[data-stage="gs-validation"]');document.querySelector('[data-stage="zone"]').click();const zones=Number(globe.dataset.zoneBoundaryCount);window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));return {beforeGlobe,afterGlobe:Number(globe.dataset.cameraZoom),beforeMap,afterMap:Number(map.dataset.mapZoom),mapPan:map.dataset.mapPan,widthRatio,groups:document.querySelectorAll('.stage-option small').length,linkedRelation,validation,substeps,peerValidation,zones,cleared:!map.dataset.selectedId&&!map.dataset.selectedRelation,errors:window.SKYSEED_PAGE_ERRORS||[]};
""",
    )
    if (
        initial["title"] not in {"Planner Semantic Inspection", "规划器语义检查"}
        or initial["frames"] < 1
        or initial["stages"] != 9
        or initial["globeTime"] != initial["mapTime"]
        or initial["detail"] < 20
        or not initial["firstHit"]
        or not selected["selected"]
        or selected["stages"] != 9
        or selected["detail"] < 20
        or selected["globeTime"] != selected["mapTime"]
        or mechanics["afterGlobe"] <= mechanics["beforeGlobe"]
        or mechanics["afterMap"] <= mechanics["beforeMap"]
        or mechanics["mapPan"] == "0,0"
        or not 0.85 <= mechanics["widthRatio"] <= 1.15
        or mechanics["groups"] != 9
        or mechanics["substeps"] != 2
        or mechanics["peerValidation"]
        or "PASS" not in mechanics["validation"]
        or not mechanics["cleared"]
        or (
            mechanics["linkedRelation"] is not None
            and (
                not mechanics["linkedRelation"]["globe"]
                or mechanics["linkedRelation"]["globe"]
                != mechanics["linkedRelation"]["map"]
                or mechanics["linkedRelation"]["pressed"] != "true"
            )
        )
        or mechanics["errors"]
    ):
        raise AssertionError(
            f"Planner browser mechanics failed: "
            f"{initial=} {selected=} {mechanics=}"
        )
    return {**initial, "selected": selected["selected"], "mechanics": mechanics}


def _compiler(base: str, session: str, page: Path) -> dict[str, object]:
    _navigate(base, session, page)
    result = _execute(
        base,
        session,
        """
const select=document.getElementById("realization"),global=document.getElementById("global-boundary");
if(select.options.length>1){select.value="1";select.dispatchEvent(new Event("change",{bubbles:true}));}
return {title:document.title,realizations:select.options.length,charts:document.querySelectorAll(".chart canvas").length,boundaries:Number(global.max)+1,timestamp:document.getElementById("compiler-timestamp").textContent,realizationDetail:document.getElementById("realization-detail").textContent.length,boundaryDetail:document.getElementById("boundary-workload").textContent.length,secondaryBoundary:!!document.getElementById("compiler-boundary"),sharedBoundary:select.dataset.globalBoundary,workloadBoundary:document.getElementById("boundary-workload").dataset.globalBoundary};
""",
    )
    dimensions = _execute(
        base,
        session,
        """
const timeline=document.getElementById('compiler-timeline'),charts=[...document.querySelectorAll('.chart canvas')],global=document.getElementById('global-boundary');global.value=global.max;global.dispatchEvent(new Event('input',{bubbles:true}));return {timelineBoundaries:Number(timeline.dataset.boundaryCount),timelineRealizations:Number(timeline.dataset.realizationCount),xAxes:charts.map(value=>value.dataset.xAxis),yAxes:charts.map(value=>value.dataset.yAxis),focusedRealizations:charts.map(value=>value.dataset.focusedRealization),globalTimestamp:document.getElementById('global-time').textContent,errors:window.SKYSEED_PAGE_ERRORS||[]};
""",
    )
    linked_cursor = _execute(
        base,
        session,
        """
const timeline=document.getElementById('compiler-timeline'),global=document.getElementById('global-boundary'),before=Number(global.value),box=timeline.getBoundingClientRect();timeline.dispatchEvent(new MouseEvent('click',{clientX:box.left+box.width*.22,clientY:box.top+100,bubbles:true}));const afterTimeline=Number(global.value),chart=document.getElementById('timing-chart'),chartBox=chart.getBoundingClientRect();chart.dispatchEvent(new MouseEvent('click',{clientX:chartBox.left+chartBox.width*.72,clientY:chartBox.top+80,bubbles:true}));return {before,afterTimeline,afterChart:Number(global.value),realization:Number(document.getElementById('realization').value),timestamp:document.getElementById('global-time').textContent};
""",
    )
    if (
        result["title"] not in {"BPF Compiler Inspection", "BPF 编译器检查"}
        or result["realizations"] < 1
        or result["charts"] != 4
        or result["boundaries"] < 1
        or not result["timestamp"]
        or result["realizationDetail"] < 20
        or result["boundaryDetail"] < 20
        or result["secondaryBoundary"]
        or result["sharedBoundary"] != result["workloadBoundary"]
        or dimensions["timelineBoundaries"] < result["realizations"]
        or dimensions["timelineRealizations"] != result["realizations"]
        or dimensions["xAxes"] != ["realization index"] * 4
        or dimensions["yAxes"] != [
            "intent count",
            "operation count",
            "count",
            "seconds",
        ]
        or not dimensions["globalTimestamp"]
        or len(set(dimensions["focusedRealizations"])) != 1
        or linked_cursor["afterTimeline"] == linked_cursor["before"]
        or linked_cursor["afterChart"] == linked_cursor["afterTimeline"]
        or not linked_cursor["timestamp"]
        or dimensions["errors"]
    ):
        raise AssertionError(
            f"Compiler browser mechanics failed: {result=} {dimensions=}"
        )
    return result | {"dimensions": dimensions, "linked_cursor": linked_cursor}


def _observation(base: str, session: str, page: Path) -> dict[str, object]:
    _navigate(base, session, page)
    probe: dict[str, object] = {}
    for _ in range(20):
        probe = _execute(
            base,
            session,
            """
const canvas=document.getElementById('execution-timeline');return {x:canvas?.dataset.firstSpanX||'',y:canvas?.dataset.firstSpanY||'',errors:window.SKYSEED_PAGE_ERRORS||[]};
""",
        )
        if probe["x"] and probe["y"]:
            break
        time.sleep(0.1)
    if not probe.get("x") or not probe.get("y"):
        raise AssertionError(
            f"Observation timeline did not expose a selectable span: {probe}"
        )
    _click_first_canvas_point(
        base,
        session,
        "execution-timeline",
        x_dataset="firstSpanX",
        y_dataset="firstSpanY",
    )
    zoom = _execute(
        base,
        session,
        """
const canvas=document.getElementById('execution-timeline'),before=Number(canvas.dataset.viewEnd)-Number(canvas.dataset.viewStart),box=canvas.getBoundingClientRect();canvas.dispatchEvent(new WheelEvent('wheel',{deltaY:-180,clientX:box.left+box.width*.65,clientY:box.top+80,bubbles:true,cancelable:true}));return {before,after:Number(canvas.dataset.viewEnd)-Number(canvas.dataset.viewStart),start:Number(canvas.dataset.viewStart),selected:canvas.dataset.selectedSequence};
""",
    )
    _drag_canvas(base, session, "execution-timeline", dx=45, dy=0)
    result = _execute(
        base,
        session,
        """
const canvas=document.getElementById('execution-timeline'),outer=document.getElementById('outer-chart'),advanced=[...document.querySelectorAll('details.card')];return {title:document.title,components:Number(canvas.dataset.componentCount),lanes:Number(canvas.dataset.laneCount),expanded:Number(canvas.dataset.expandedComponentCount),spans:Number(canvas.dataset.spanCount),focus:document.getElementById('timeline-focus').textContent,stats:document.getElementById('execution-stats').textContent,rawStats:document.getElementById('raw-span-stats').textContent,viewStart:Number(canvas.dataset.viewStart),outerSpans:Number(outer.dataset.phaseCount||0),outerAggregates:Number(outer.dataset.aggregateCount||0),advancedCollapsed:advanced.every(value=>!value.open),errors:window.SKYSEED_PAGE_ERRORS||[]};
""",
    )
    if (
        not result["title"]
        or result["components"] < 1
        or result["spans"] < 1
        or result["expanded"] != 1
        or "Selected exact span" not in result["focus"]
        or "cross-component overlap ratio" not in result["stats"]
        or "raw span overlap including nested rollups" not in result["rawStats"]
        or "recorded pipeline gaps" not in result["stats"]
        or zoom["after"] >= zoom["before"]
        or not zoom["selected"]
        or result["viewStart"] == zoom["start"]
        or not result["advancedCollapsed"]
        or result["errors"]
    ):
        raise AssertionError(
            f"Observation browser mechanics failed: {result=} {zoom=}"
        )
    return result | {"zoom": zoom}


def _generic(base: str, session: str, page: Path, phrase: str) -> dict[str, object]:
    _navigate(base, session, page)
    result = _execute(
        base,
        session,
        f"return {{title:document.title,text:document.body.textContent.includes({phrase!r}),errors:document.querySelectorAll('.status.FAILED').length}};",
    )
    if not result["title"] or not result["text"] or result["errors"]:
        raise AssertionError(f"Generic Tool browser mechanics failed: {page}: {result}")
    return result


def _s3_review(
    base: str,
    session: str,
    root: Path,
    screenshot_directory: Path | None,
) -> dict[str, object]:
    """Exercise the full maintained Starlink inspection at its real scale."""

    screenshots: dict[str, str | None] = {}
    planner_root = root / "planner"
    planner_data = json.loads(
        (planner_root / "planner-inspection.json").read_text(encoding="utf-8")
    )
    first_chunk_path = planner_root / planner_data["frame_index"][0]["chunk"]
    first_chunk_text = first_chunk_path.read_text(encoding="utf-8")
    first_frame = json.loads(first_chunk_text.split("=", 1)[1].rstrip(";\n"))
    parameter_frame_index = next(
        index
        for index, item in enumerate(planner_data["frame_index"])
        if '"parameter_changes":[{' in (
            planner_root / item["chunk"]
        ).read_text(encoding="utf-8")
    )
    parameter_text = (planner_root / planner_data["frame_index"][parameter_frame_index]["chunk"]).read_text(encoding="utf-8")
    parameter_frame = json.loads(parameter_text.split("=", 1)[1].rstrip(";\n"))
    changed_connection = parameter_frame["transition"]["parameter_changes"][0]["connection"]
    # Relative-threshold policy need not update the first displayed Satellite.
    # Select an endpoint of an actual retained change through the visible map.
    parameter_node = changed_connection[2]
    _, x, y, z = next(position for position in parameter_frame["positions"] if position[0] == parameter_node)
    parameter_latitude, parameter_longitude = degrees(atan2(z, hypot(x, y))), degrees(atan2(y, x))
    zone_id = str(first_frame["selected"]["zone_service"][0][0])
    zone_record = next(
        value
        for value in planner_data["spatial_index"]["zones"]
        if value["zone_id"] == zone_id
    )
    zone_center = zone_record["geometry"]["representative_center"]
    first_route_shard = int(planner_data["route_index"][0]["shard"])
    route_shard_text = (
        planner_root / f"data/routes-{first_route_shard:02d}.js"
    ).read_text(encoding="utf-8")
    route_shard = json.loads(route_shard_text.split("]=", 1)[1].rstrip(";\n"))
    route_record = next(
        value
        for value in planner_data["route_index"]
        if int(value["shard"]) == first_route_shard
        and route_shard[value["key"]]["timeline"][-1][1] is not None
    )
    route_label = str(route_record["label"])
    _navigate(base, session, root / "physical-world/index.html")
    physical_world = _execute(
        base,
        session,
        """
document.querySelector('.workspace').scrollIntoView({block:'start'});
const stats=[...document.querySelectorAll('.stats .stat strong')].map(v=>Number(v.textContent));
return {orbits:stats[0],satellites:stats[1],groundStations:stats[2],zones:stats[3],frames:stats[4],positions:Number(document.getElementById('globe-canvas').dataset.positionCount),candidateEdges:Number(document.getElementById('globe-canvas').dataset.candidateCount),errors:window.SKYSEED_PAGE_ERRORS||[],domNodes:document.getElementsByTagName('*').length,navigationMs:performance.getEntriesByType('navigation')[0]?.duration||0};
""",
    )
    screenshots["physical-world"] = _capture_screenshot(
        base, session, screenshot_directory, "s3-physical-world.png"
    )

    _navigate(base, session, root / "planner/index.html")
    planner_inventory = _execute(
        base,
        session,
        """
const stats=[...document.querySelectorAll('.stats .stat strong')].map(v=>Number(v.textContent));
return {plans:stats[0],transitions:stats[1],frames:Number(document.getElementById('planner-time').max)+1,stages:document.querySelectorAll('#stage-strip [data-stage]').length,domNodes:document.getElementsByTagName('*').length};
""",
    )
    planner_stages: dict[str, object] = {}
    for stage, filename in (
        ("isl", "s3-planner-isl.png"),
        ("gs", "s3-planner-ground-station.png"),
        ("zone", "s3-planner-zone.png"),
    ):
        stage_result = _execute(
            base,
            session,
            f"""
document.querySelector("[data-stage={stage}]").click();
document.querySelector('.workspace').scrollIntoView({{block:'start'}});
const map=document.getElementById('planned-map');
return {{stage:map.dataset.stage,selected:map.dataset.selectedId||'',focusedCandidates:Number(map.dataset.focusedCandidateCount||0),visibleEdges:Number(map.dataset.visibleEdgeCount||0),detail:document.getElementById('stage-detail').textContent.length,domNodes:document.getElementsByTagName('*').length}};
""",
        )
        if stage == "gs":
            _click_first_canvas_point(base, session, "planned-map")
            stage_result = _execute(
                base,
                session,
                """const m=document.getElementById('planned-map');return {stage:m.dataset.stage,selected:m.dataset.selectedId||'',focusedCandidates:Number(m.dataset.focusedCandidateCount||0),visibleEdges:Number(m.dataset.visibleEdgeCount||0),detail:document.getElementById('special').textContent.length,domNodes:document.getElementsByTagName('*').length};""",
            )
        elif stage == "zone":
            _click_geographic_canvas_point(
                base,
                session,
                "planned-map",
                latitude_deg=float(zone_center[0]),
                longitude_deg=float(zone_center[1]),
            )
            stage_result = _execute(
                base,
                session,
                """const m=document.getElementById('planned-map');return {stage:m.dataset.stage,selected:m.dataset.selectedId||'',focusedCandidates:Number(m.dataset.focusedCandidateCount||0),visibleEdges:Number(m.dataset.visibleEdgeCount||0),detail:document.getElementById('focus-detail').textContent.length,domNodes:document.getElementsByTagName('*').length};""",
            )
        screenshots[f"planner-{stage}"] = _capture_screenshot(
            base, session, screenshot_directory, filename
        )
        planner_stages[stage] = stage_result

    _execute(
        base,
        session,
        "document.querySelector('[data-stage=\"isl\"]').click();return true;",
    )
    _click_first_canvas_point(
        base,
        session,
        "planned-map",
        x_dataset="firstSatelliteHitX",
        y_dataset="firstSatelliteHitY",
    )
    isl_semantics = _execute(
        base,
        session,
        """
const map=document.getElementById('planned-map');
const islButton=document.querySelector('#special [data-isl-relation]');islButton?.click();
return {selected:map.dataset.selectedId,candidates:Number(map.dataset.focusedCandidateCount||0),edges:Number(map.dataset.visibleEdgeCount||0),relation:map.dataset.selectedRelation,detail:document.getElementById('focus-detail').textContent,candidateDetail:document.getElementById('special').textContent};
""",
    )
    _execute(
        base,
        session,
        f"""
const slider=document.getElementById('planner-time');slider.value={parameter_frame_index!r};slider.dispatchEvent(new Event('input',{{bubbles:true}}));return true;
""",
    )
    time.sleep(0.8)
    _execute(base, session, "document.getElementById('clear-focus').click();document.getElementById('reset-view').click();document.querySelector('[data-stage=\"parameter\"]').click();return true;")
    _click_geographic_canvas_point(base, session, "planned-map",
                                  latitude_deg=parameter_latitude, longitude_deg=parameter_longitude)
    later_semantics = _execute(
        base,
        session,
        """
const map=document.getElementById('planned-map');
document.querySelector('[data-stage="parameter"]').click();
const parameterButton=document.querySelector('#special [data-parameter]');parameterButton?.click();
const parameter={available:!!parameterButton,relation:map.dataset.selectedRelation,detail:document.getElementById('focus-detail').textContent,edges:Number(map.dataset.visibleEdgeCount||0)};
document.querySelector('[data-stage="coordination"]').click();
const coordination={relationCleared:!map.dataset.selectedRelation,detail:document.getElementById('special').textContent};
document.querySelector('[data-stage="publication"]').click();
const publication={detail:document.getElementById('special').textContent};
return {parameter,coordination,publication,errors:window.SKYSEED_PAGE_ERRORS||[]};
""",
    )
    planner_semantics = {"isl": isl_semantics, **later_semantics}

    _execute(
        base,
        session,
        """const slider=document.getElementById('planner-time');slider.value='0';slider.dispatchEvent(new Event('input',{bubbles:true}));return true;""",
    )
    time.sleep(0.8)
    route = _execute(
        base,
        session,
        """
document.querySelector('[data-stage="route"]').click();
const demands=[...document.querySelectorAll('#special [data-route-demand]')];
return {requested:'',source:document.getElementById('focus-detail').textContent,usedPrimary:demands.length>0,demandCount:demands.length,advancedClosed:!document.querySelector('#special details')?.open};
""",
    )
    for demand_index in range(min(30, int(route["demandCount"]))):
        selected_route = _execute(
            base,
            session,
            f"""
const demand=[...document.querySelectorAll('#special [data-route-demand]')][{demand_index}];demand?.click();return {{requested:demand?.textContent||''}};
""",
        )
        time.sleep(0.3)
        selected_route |= _execute(
            base,
            session,
            """
document.querySelector('.workspace').scrollIntoView({block:'start'});
const text=document.getElementById('route-outcome').textContent,map=document.getElementById('planned-map'),globe=document.getElementById('planned-globe');return {loaded:text.includes('selected demand'),outcome:text.includes('unreachable')?'unreachable':text.includes('reachable')?'reachable':'',visibleEdges:Number(map.dataset.visibleEdgeCount||0),detail:text.length,mapRoute:map.dataset.selectedRoute,globeRoute:globe.dataset.selectedRoute,mapRouteEdges:Number(map.dataset.routeEdgeCount||0),globeRouteEdges:Number(globe.dataset.routeEdgeCount||0),domNodes:document.getElementsByTagName('*').length};
""",
        )
        route |= selected_route
        if selected_route["outcome"] in {"reachable", "unreachable"}:
            break
    screenshots["planner-route"] = _capture_screenshot(
        base, session, screenshot_directory, "s3-planner-focused-route.png"
    )

    _navigate(base, session, root / "bpf-compiler/index.html")
    compiler = _execute(
        base,
        session,
        """
document.querySelector('.dashboard').scrollIntoView({block:'start'});
const stats=[...document.querySelectorAll('.stats .stat strong')].map(v=>Number(v.textContent));
return {realizations:document.getElementById('realization').options.length,plans:stats[0],activationBoundaries:stats[1],charts:document.querySelectorAll('.chart canvas').length,tableRows:document.querySelectorAll('table tbody tr').length,pre:document.querySelectorAll('pre').length,domNodes:document.getElementsByTagName('*').length,navigationMs:performance.getEntriesByType('navigation')[0]?.duration||0};
""",
    )
    screenshots["compiler"] = _capture_screenshot(
        base, session, screenshot_directory, "s3-compiler-dashboard.png"
    )

    if physical_world != physical_world | {
        "satellites": 720,
        "groundStations": 355,
        "zones": 732,
        "frames": 121,
        "positions": 1075,
        "errors": [],
    }:
        raise AssertionError(f"S3 PhysicalWorld scale regression: {physical_world}")
    if planner_inventory != planner_inventory | {
        "transitions": 121,
        "plans": 4,
        "frames": 121,
        "stages": 9,
    }:
        raise AssertionError(f"S3 Planner scale regression: {planner_inventory}")
    if any(value["stage"] != key for key, value in planner_stages.items()):
        raise AssertionError(f"S3 Planner stage regression: {planner_stages}")
    if (
        not planner_stages["gs"]["selected"]
        or planner_stages["zone"]["focusedCandidates"] < 1
        or not route["loaded"]
        or route["outcome"] not in {"reachable", "unreachable"}
        or not route["usedPrimary"]
        or not route["advancedClosed"]
        or not route["mapRoute"]
        or route["mapRoute"] != route["globeRoute"]
        or route["mapRouteEdges"] != route["globeRouteEdges"]
        or route["detail"] < 20
        or not planner_semantics["isl"]["selected"]
        or planner_semantics["isl"]["candidates"] < 1
        or planner_semantics["isl"]["relation"] != "physical ISL candidate"
        or "cyclic K3" not in planner_semantics["isl"]["candidateDetail"]
        or not planner_semantics["parameter"]["available"]
        or planner_semantics["parameter"]["relation"] != "parameter change"
        or not all(
            label in planner_semantics["parameter"]["detail"]
            for label in ("bandwidth", "delay", "packet loss")
        )
        or not planner_semantics["coordination"]["relationCleared"]
        or "semantic events" not in planner_semantics["coordination"]["detail"]
        or "Plan ID" not in planner_semantics["publication"]["detail"]
        or planner_semantics["errors"]
    ):
        raise AssertionError(
            f"S3 Planner focused interaction regression: "
            f"{planner_stages=} {planner_semantics=} {route=}"
        )
    if compiler != compiler | {
        "realizations": 4,
        "plans": 4,
        "activationBoundaries": 121,
        "charts": 4,
        "tableRows": 0,
        "pre": 0,
    }:
        raise AssertionError(f"S3 Compiler dashboard regression: {compiler}")
    return {
        "physical-world": physical_world,
        "planner": planner_inventory | {"stage_results": planner_stages},
        "planner_semantics": planner_semantics,
        "route": route,
        "compiler": compiler,
        "screenshots": screenshots,
    }


def run_commit_prep_browser_gate(
    inspection_root: Path,
    *,
    geckodriver: str = "geckodriver",
    screenshot_directory: Path | None = None,
    timeout: float = 45.0,
    s3_expectations: bool = False,
) -> dict[str, object]:
    """Open and exercise every relevant page present in one inspection root."""

    root = Path(inspection_root).resolve()
    port = _available_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            (geckodriver, "--port", str(port)), stdout=log, stderr=subprocess.STDOUT
        )
        session = None
        try:
            _wait_for_driver(base, timeout)
            created = _request(
                f"{base}/session",
                method="POST",
                value={
                    "capabilities": {
                        "alwaysMatch": {
                            "browserName": "firefox",
                            "moz:firefoxOptions": {"args": ["-headless"]},
                        }
                    }
                },
                timeout=timeout,
            )
            session = created["sessionId"]
            _request(
                f"{base}/session/{session}/window/rect",
                method="POST",
                value={"width": 1600, "height": 1200},
            )
            observation = _observation(
                base, session, root / "observation/index.html"
            )
            if (
                (root / "orchestration-timing.json").is_file()
                and observation["outerSpans"] < 1
            ):
                raise AssertionError(
                    "Observation omitted measured orchestration spans: "
                    f"{observation}"
                )
            result: dict[str, object] = {
                "status": "PASS",
                "browser": created["capabilities"]["browserName"],
                "browser_version": created["capabilities"]["browserVersion"],
                "physical-world": _physical_world(
                    base, session, root / "physical-world/index.html"
                ),
                "planner": _planner(base, session, root / "planner/index.html"),
                "bpf-compiler": _compiler(
                    base, session, root / "bpf-compiler/index.html"
                ),
                "zone": _generic(base, session, root / "zone/index.html", "Zone"),
                "observation": observation,
            }
            for optional, phrase in (
                ("runner", "Runner lifecycle"),
                ("traffic", "Traffic transactions"),
            ):
                page = root / optional / "index.html"
                if page.is_file():
                    result[optional] = _generic(base, session, page, phrase)
            if s3_expectations:
                result["s3"] = _s3_review(
                    base, session, root, screenshot_directory
                )
            result["screenshot"] = _capture_screenshot(
                base,
                session,
                screenshot_directory,
                "commit-prep-inspection.png",
            )
            return result
        finally:
            if session is not None:
                try:
                    _request(
                        f"{base}/session/{session}",
                        method="DELETE",
                        timeout=timeout,
                    )
                except OSError:
                    pass
            _stop_driver(process, port)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inspection_root", type=Path)
    parser.add_argument("--geckodriver", default="geckodriver")
    parser.add_argument("--screenshot-directory", type=Path)
    parser.add_argument(
        "--s3-expectations",
        action="store_true",
        help="exercise exact maintained Starlink scale and focused views",
    )
    arguments = parser.parse_args()
    print(
        run_commit_prep_browser_gate(
            arguments.inspection_root,
            geckodriver=arguments.geckodriver,
            screenshot_directory=arguments.screenshot_directory,
            s3_expectations=arguments.s3_expectations,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_commit_prep_browser_gate"]
