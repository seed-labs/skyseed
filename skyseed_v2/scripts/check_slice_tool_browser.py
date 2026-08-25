#!/usr/bin/env python3
"""Smoke-test the chunked Slice / Difference Tool with Firefox WebDriver."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _request(
    endpoint: str,
    *,
    method: str = "GET",
    value: object | None = None,
    timeout: float = 30.0,
) -> Any:
    payload = None if value is None else json.dumps(value).encode("utf-8")
    request = Request(
        endpoint,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"WebDriver HTTP {error.code}: {body}") from error
    if isinstance(result, dict) and isinstance(result.get("value"), dict):
        error = result["value"].get("error")
        if error:
            raise RuntimeError(
                f"WebDriver {error}: {result['value'].get('message', '')}"
            )
    return result.get("value") if isinstance(result, dict) else result


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _default_geckodriver() -> str | None:
    return shutil.which("geckodriver")


def _stop_snap_driver_scope(port: int) -> bool:
    for process_directory in Path("/proc").iterdir():
        if not process_directory.name.isdigit():
            continue
        try:
            command = (process_directory / "cmdline").read_bytes().split(b"\0")
            cgroup = (process_directory / "cgroup").read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if (
            not any(b"geckodriver" in value for value in command)
            or str(port).encode("ascii") not in command
        ):
            continue
        scope = next(
            (
                line.rsplit("/", 1)[-1]
                for line in cgroup.splitlines()
                if line.endswith(".scope")
                and "snap.firefox.geckodriver-" in line
            ),
            None,
        )
        if scope is None:
            continue
        completed = subprocess.run(
            ("systemctl", "--user", "stop", scope),
            check=False,
            capture_output=True,
            text=True,
        )
        return completed.returncode == 0
    return False


def _stop_driver(process: subprocess.Popen[bytes], port: int) -> None:
    if process.poll() is None:
        try:
            process.terminate()
        except PermissionError:
            if not _stop_snap_driver_scope(port):
                raise
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except PermissionError:
            if not _stop_snap_driver_scope(port):
                raise
        process.wait(timeout=10)


def _wait_for_driver(base_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status = _request(f"{base_url}/status", timeout=1.0)
        except (OSError, URLError):
            time.sleep(0.1)
            continue
        if isinstance(status, dict) and status.get("ready"):
            return
        time.sleep(0.1)
    raise TimeoutError("geckodriver did not become ready")


def _execute(base_url: str, session_id: str, script: str) -> Any:
    return _request(
        f"{base_url}/session/{session_id}/execute/sync",
        method="POST",
        value={"script": script, "args": []},
    )


def _wait_for_slice(
    base_url: str,
    session_id: str,
    expected_slice_id: int,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        loaded = _execute(
            base_url,
            session_id,
            'return document.getElementById("slice-globe").dataset.loadedSliceId||"";',
        )
        if loaded == str(expected_slice_id):
            return
        time.sleep(0.1)
    raise TimeoutError(f"Slice {expected_slice_id} chunk did not load")


def _capture_screenshot(
    base_url: str,
    session_id: str,
    directory: Path | None,
    filename: str,
) -> str | None:
    if directory is None:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    encoded = _request(
        f"{base_url}/session/{session_id}/screenshot", timeout=30.0
    )
    if not isinstance(encoded, str):
        raise RuntimeError("WebDriver screenshot response is not base64 text")
    path = directory / filename
    path.write_bytes(base64.b64decode(encoded))
    return path.name


def _click_canvas_dataset(
    base_url: str,
    session_id: str,
    prefix: str,
) -> str:
    target = _execute(
        base_url,
        session_id,
        f"""
const canvas=document.getElementById("slice-globe");
canvas.scrollIntoView({{block:"center"}});
const box=canvas.getBoundingClientRect();
const id=canvas.dataset.{prefix}Id||"";
return {{id,x:box.left+Number(canvas.dataset.{prefix}X),y:box.top+Number(canvas.dataset.{prefix}Y)}};
""",
    )
    if not target["id"]:
        raise AssertionError(f"no visible {prefix} hit-test target")
    _request(
        f"{base_url}/session/{session_id}/actions",
        method="POST",
        value={
            "actions": [
                {
                    "type": "pointer",
                    "id": "mouse",
                    "parameters": {"pointerType": "mouse"},
                    "actions": [
                        {
                            "type": "pointerMove",
                            "duration": 0,
                            "origin": "viewport",
                            "x": round(target["x"]),
                            "y": round(target["y"]),
                        },
                        {"type": "pointerDown", "button": 0},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        },
    )
    return str(target["id"])


def _hover_canvas_dataset(
    base_url: str,
    session_id: str,
    prefix: str,
) -> str:
    return str(
        _execute(
            base_url,
            session_id,
            f"""
const canvas=document.getElementById("slice-globe"),box=canvas.getBoundingClientRect();
updateHover(box.left+Number(canvas.dataset.{prefix}X),box.top+Number(canvas.dataset.{prefix}Y));
return canvas.dataset.hoverLabelText||"";
""",
        )
    )


def _exercise_page(
    base_url: str,
    session_id: str,
    timeout: float,
    profile: str,
) -> dict[str, object]:
    initial_id = _execute(
        base_url,
        session_id,
        "return Number(window.SKYSEED_SLICE_MANIFEST.slice_manifest.find(value=>value.slice_id===1)?.slice_id||0);",
    )
    _wait_for_slice(base_url, session_id, int(initial_id), timeout)
    initial = _execute(
        base_url,
        session_id,
        r"""
return {
  inventory:window.SKYSEED_SLICE_MANIFEST.inventory,
  manifestHasSlices:Object.prototype.hasOwnProperty.call(window.SKYSEED_SLICE_MANIFEST,"slices"),
  loadedSliceId:Number(document.getElementById("slice-globe").dataset.loadedSliceId),
  loadedRouteEntries:Number(document.getElementById("slice-globe").dataset.loadedRouteEntries),
  chunkObjectCount:Object.keys(window.__SKYSEED_SLICE_CHUNKS__).length,
  nodeDetail:document.getElementById("selected-detail").textContent,
  pathDetail:document.getElementById("path-detail").textContent,
  diagnostics:document.getElementById("diagnostics").textContent,
  sliceContext:document.getElementById("slice-context").textContent,
  sliceContextState:document.getElementById("slice-context").dataset.stateAvailable,
  servedZones:Number(document.getElementById("slice-context").dataset.servedZones),
  unservedZones:Number(document.getElementById("slice-context").dataset.unservedZones),
  coveredGroundStations:Number(document.getElementById("slice-context").dataset.coveredGroundStations),
  routeEntries:Number(document.getElementById("slice-context").dataset.routeEntries),
  physicalContext:document.getElementById("slice-physical-context").textContent,
  modelContext:document.getElementById("model-context")?.textContent||"",
  externalScripts:[...document.scripts].map(value=>value.src).filter(Boolean),
  rangeMax:Number(document.getElementById("slice-range").max),
  earthReferenceRadiusM:Number(document.getElementById("slice-globe").dataset.earthReferenceRadiusM),
  firstSatelliteDisplayRadius:Number(document.getElementById("slice-globe").dataset.firstSatelliteDisplayRadius)
};
""",
    )
    interactions: dict[str, object] = {}
    _execute(
        base_url,
        session_id,
        'document.getElementById("pick-node").click();return true;',
    )
    node_hover = _hover_canvas_dataset(base_url, session_id, "firstNode")
    if not node_hover:
        raise AssertionError("direct node hover label was not rendered")
    selected_node = _click_canvas_dataset(base_url, session_id, "firstNode")
    node_state = _execute(
        base_url,
        session_id,
        r"""
const canvas=document.getElementById("slice-globe");
return {id:canvas.dataset.selectedNodeId,label:canvas.dataset.selectedLabelText,labelKind:canvas.dataset.selectedLabelKind,detail:document.getElementById("selected-detail").textContent};
""",
    )
    if (
        node_state["id"] != selected_node
        or node_state["labelKind"] != "node"
        or not node_state["label"]
        or "degree" not in node_state["detail"]
    ):
        raise AssertionError(f"direct node selection failed: {node_state}")
    interactions["direct_node"] = node_state
    interactions["direct_node_hover"] = node_hover
    _click_canvas_dataset(base_url, session_id, "firstNode")
    node_cleared = _execute(
        base_url,
        session_id,
        r"""
const canvas=document.getElementById("slice-globe"),detail=document.getElementById("selected-detail");
return {node:canvas.dataset.selectedNodeId,kind:canvas.dataset.selectedLabelKind,label:canvas.dataset.selectedLabelText,status:detail.dataset.selectionStatus,text:detail.textContent};
""",
    )
    if node_cleared != {
        "node": "",
        "kind": "",
        "label": "",
        "status": "none",
        "text": "Click an object on Dynamic Earth.",
    }:
        raise AssertionError(f"same-node toggle did not clear selection: {node_cleared}")
    interactions["direct_node_toggle"] = node_cleared

    if profile == "rich":
        connected_node = _click_canvas_dataset(
            base_url, session_id, "connectedNode"
        )
        node_context = _execute(
            base_url,
            session_id,
            r'''
const canvas=document.getElementById("slice-globe");
return {
  id:canvas.dataset.selectedNodeId,
  incident:Number(canvas.dataset.incidentContextConnections),
  endpoints:Number(canvas.dataset.endpointContextNodes)
};
''',
        )
        if (
            node_context["id"] != connected_node
            or node_context["incident"] < 1
            or node_context["endpoints"] != 0
        ):
            raise AssertionError(
                f"selected-node Connection context failed: {node_context}"
            )
        interactions["node_connection_context"] = node_context

        selected_ground_station = _click_canvas_dataset(
            base_url, session_id, "groundStation"
        )
        ground_station_state = _execute(
            base_url,
            session_id,
            r"""
const canvas=document.getElementById("slice-globe");
return {id:canvas.dataset.selectedNodeId,label:canvas.dataset.selectedLabelText,labelKind:canvas.dataset.selectedLabelKind,detail:document.getElementById("selected-detail").textContent};
""",
        )
        if (
            ground_station_state["id"] != selected_ground_station
            or ground_station_state["labelKind"] != "node"
            or not ground_station_state["label"].startswith("GroundStation ")
            or "connected Satellites" not in ground_station_state["detail"]
        ):
            raise AssertionError(
                f"GroundStation selection failed: {ground_station_state}"
            )
        _click_canvas_dataset(base_url, session_id, "groundStation")
        ground_station_cleared = _execute(
            base_url,
            session_id,
            'const c=document.getElementById("slice-globe");return {id:c.dataset.selectedNodeId,label:c.dataset.selectedLabelText,kind:c.dataset.selectedLabelKind};',
        )
        if ground_station_cleared != {"id": "", "label": "", "kind": ""}:
            raise AssertionError(
                "same-GroundStation toggle did not clear selection: "
                f"{ground_station_cleared}"
            )
        interactions["ground_station_toggle"] = {
            "selected": ground_station_state,
            "cleared": ground_station_cleared,
        }

        _execute(
            base_url,
            session_id,
            'document.getElementById("pick-connection").click();return true;',
        )
        connection_hover = _hover_canvas_dataset(
            base_url, session_id, "firstConnection"
        )
        if not connection_hover:
            raise AssertionError("direct Connection hover label was not rendered")
        selected_connection = _click_canvas_dataset(
            base_url, session_id, "firstConnection"
        )
        connection_state = _execute(
            base_url,
            session_id,
            r"""
const canvas=document.getElementById("slice-globe");
return {id:canvas.dataset.selectedConnectionRef,label:canvas.dataset.selectedLabelText,labelKind:canvas.dataset.selectedLabelKind,status:document.getElementById("selected-detail").dataset.selectionStatus,incident:Number(canvas.dataset.incidentContextConnections),endpoints:Number(canvas.dataset.endpointContextNodes),detail:document.getElementById("selected-detail").textContent};
""",
        )
        if (
            not connection_state["id"]
            or connection_state["labelKind"] != "connection"
            or not connection_state["label"]
            or connection_state["status"] != "present"
            or connection_state["incident"] != 0
            or connection_state["endpoints"] != 2
            or "bandwidth" not in connection_state["detail"]
        ):
            raise AssertionError(
                "direct Connection selection failed: "
                f"published_target={selected_connection!r}, {connection_state}"
            )
        interactions["direct_connection"] = connection_state
        interactions["direct_connection_hover"] = connection_hover
        if (
            _click_canvas_dataset(base_url, session_id, "selectedConnection")
            != connection_state["id"]
        ):
            raise AssertionError("selected Connection hit-test identity changed")
        connection_cleared = _execute(
            base_url,
            session_id,
            r"""
const canvas=document.getElementById("slice-globe"),detail=document.getElementById("selected-detail");
return {id:canvas.dataset.selectedConnectionRef,label:canvas.dataset.selectedLabelText,kind:canvas.dataset.selectedLabelKind,status:detail.dataset.selectionStatus,incident:Number(canvas.dataset.incidentContextConnections),endpoints:Number(canvas.dataset.endpointContextNodes)};
""",
        )
        if connection_cleared != {
            "id": "",
            "label": "",
            "kind": "",
            "status": "none",
            "incident": 0,
            "endpoints": 0,
        }:
            raise AssertionError(
                "same-Connection toggle did not clear selection: "
                f"{connection_cleared}"
            )
        interactions["direct_connection_toggle"] = connection_cleared

        _execute(
            base_url,
            session_id,
            'document.getElementById("pick-zone").click();return true;',
        )
        zone_hover = _hover_canvas_dataset(base_url, session_id, "serviceZone")
        if not zone_hover.startswith("Zone "):
            raise AssertionError(f"direct Zone hover label failed: {zone_hover!r}")
        selected_zone = _click_canvas_dataset(base_url, session_id, "serviceZone")
        zone_state = _execute(
            base_url,
            session_id,
            r"""
const canvas=document.getElementById("slice-globe");
const detail=document.getElementById("selected-detail").textContent;
return {id:canvas.dataset.selectedZoneId,label:canvas.dataset.selectedLabelText,labelKind:canvas.dataset.selectedLabelKind,service:canvas.dataset.selectedZoneServiceSatellite,tethers:Number(canvas.dataset.selectedZoneTethers),otherZoneCount:Number(canvas.dataset.selectedZoneOtherServiceZones),otherZones:detail.includes("other Zones served by that Satellite"),elevation:detail.includes("current representative-point elevation"),surface:detail.includes("approximate surface separation"),slant:detail.includes("slant distance"),representativeScope:detail.includes("representative point only"),detail};
""",
        )
        if (
            zone_state["id"] != selected_zone
            or zone_state["labelKind"] != "zone"
            or zone_state["label"] != f"Zone {selected_zone}"
            or not zone_state["service"]
            or zone_state["tethers"] < 1
            or not zone_state["otherZones"]
            or zone_state["otherZoneCount"] < 1
            or not zone_state["elevation"]
            or not zone_state["surface"]
            or not zone_state["slant"]
            or not zone_state["representativeScope"]
        ):
            raise AssertionError(f"Zone service selection failed: {zone_state}")
        interactions["zone_service"] = zone_state
        interactions["zone_hover"] = zone_hover

        route_state = _execute(
            base_url,
            session_id,
            r"""
const canvas=document.getElementById("slice-globe"),gateway=document.getElementById("gateway-route-gateway"),direction=document.getElementById("gateway-route-direction");
const beforeGateway={zone:canvas.dataset.routeZoneId,gateway:canvas.dataset.routeGatewayId,status:canvas.dataset.routeStatus,nodes:Number(canvas.dataset.routePathNodes)};
gateway.value=[...gateway.options].map(option=>option.value).find(Boolean)||"";
gateway.dispatchEvent(new Event("change",{bubbles:true}));
const zoneToGateway={zone:canvas.dataset.routeZoneId,gateway:canvas.dataset.routeGatewayId,gatewayChoices:Number(canvas.dataset.routeGatewayChoices),direction:canvas.dataset.routeDirection,source:document.getElementById("route-source").value,target:document.getElementById("route-target").value,status:canvas.dataset.routeStatus,nodes:Number(canvas.dataset.routePathNodes),detail:document.getElementById("path-detail").textContent};
direction.value="gateway-to-zone";direction.dispatchEvent(new Event("change",{bubbles:true}));
const gatewayToZone={zone:canvas.dataset.routeZoneId,gateway:canvas.dataset.routeGatewayId,gatewayChoices:Number(canvas.dataset.routeGatewayChoices),direction:canvas.dataset.routeDirection,source:document.getElementById("route-source").value,target:document.getElementById("route-target").value,status:canvas.dataset.routeStatus,nodes:Number(canvas.dataset.routePathNodes),detail:document.getElementById("path-detail").textContent};
return {beforeGateway,optionValues:[...gateway.options].map(option=>option.value).filter(Boolean),zoneToGateway,gatewayToZone};
""",
        )
        zone_to_gateway = route_state["zoneToGateway"]
        gateway_to_zone = route_state["gatewayToZone"]
        if (
            route_state["beforeGateway"]
            != {
                "zone": selected_zone,
                "gateway": "",
                "status": "none",
                "nodes": 0,
            }
            or zone_to_gateway["zone"] != selected_zone
            or zone_to_gateway["gatewayChoices"] < 1
            or len(route_state["optionValues"])
            != zone_to_gateway["gatewayChoices"]
            or zone_to_gateway["gateway"] not in route_state["optionValues"]
            or zone_to_gateway["direction"] != "zone-to-gateway"
            or zone_to_gateway["source"] != zone_state["service"]
            or zone_to_gateway["target"]
            != f'G|{zone_to_gateway["gateway"]}'
            or zone_to_gateway["status"] != "present"
            or zone_to_gateway["nodes"] < 1
            or gateway_to_zone["direction"] != "gateway-to-zone"
            or gateway_to_zone["source"] != zone_to_gateway["gateway"]
            or gateway_to_zone["target"] != f"Z|{selected_zone}"
            or gateway_to_zone["status"] != "present"
            or gateway_to_zone["nodes"] < 1
            or "Gateway ingress Satellite" not in gateway_to_zone["detail"]
            or "formal path composition" not in gateway_to_zone["detail"]
            or "direct Gateway/terminal formal access"
            not in gateway_to_zone["detail"]
        ):
            raise AssertionError(
                f"Zone/Gateway Route workflow failed: {route_state}"
            )
        interactions["visual_route"] = route_state

        _click_canvas_dataset(base_url, session_id, "serviceZone")
        zone_cleared = _execute(
            base_url,
            session_id,
            r"""
const canvas=document.getElementById("slice-globe"),detail=document.getElementById("selected-detail");
return {id:canvas.dataset.selectedZoneId,label:canvas.dataset.selectedLabelText,kind:canvas.dataset.selectedLabelKind,tethers:Number(canvas.dataset.selectedZoneTethers),routeZone:canvas.dataset.routeZoneId,routeGateway:canvas.dataset.routeGatewayId,routeNodes:Number(canvas.dataset.routePathNodes),status:detail.dataset.selectionStatus};
""",
        )
        if zone_cleared != {
            "id": "",
            "label": "",
            "kind": "",
            "tethers": 0,
            "routeZone": "",
            "routeGateway": "",
            "routeNodes": 0,
            "status": "none",
        }:
            raise AssertionError(
                f"same-Zone toggle did not clear service/Route context: {zone_cleared}"
            )
        interactions["zone_toggle"] = zone_cleared
        if _click_canvas_dataset(base_url, session_id, "serviceZone") != selected_zone:
            raise AssertionError("reselecting the service Zone changed identity")
        _execute(
            base_url,
            session_id,
            r'''
const gateway=document.getElementById("gateway-route-gateway");
gateway.value=[...gateway.options].map(option=>option.value).find(Boolean)||"";
gateway.dispatchEvent(new Event("change",{bubbles:true}));
return true;
''',
        )

        _execute(
            base_url,
            session_id,
            'document.getElementById("pick-node").click();return true;',
        )
        selected_satellite = _click_canvas_dataset(
            base_url, session_id, "serviceSatellite"
        )
        _execute(
            base_url,
            session_id,
            'document.getElementById("toggle-served-zones").click();return true;',
        )
        served_state = _execute(
            base_url,
            session_id,
            'const c=document.getElementById("slice-globe");return {satellite:c.dataset.selectedNodeId,label:c.dataset.selectedLabelText,labelKind:c.dataset.selectedLabelKind,count:Number(c.dataset.satelliteServedZones)};',
        )
        if (
            served_state["satellite"] != selected_satellite
            or served_state["labelKind"] != "node"
            or not served_state["label"].startswith("Satellite ")
            or served_state["count"] < 1
        ):
            raise AssertionError(
                f"Satellite served-Zone highlighting failed: {served_state}"
            )
        interactions["satellite_served_zones"] = served_state
        _click_canvas_dataset(base_url, session_id, "serviceSatellite")
        satellite_cleared = _execute(
            base_url,
            session_id,
            r"""
const canvas=document.getElementById("slice-globe"),detail=document.getElementById("selected-detail");
return {id:canvas.dataset.selectedNodeId,label:canvas.dataset.selectedLabelText,kind:canvas.dataset.selectedLabelKind,served:Number(canvas.dataset.satelliteServedZones),routeZone:canvas.dataset.routeZoneId,routeGateway:canvas.dataset.routeGatewayId,routeNodes:Number(canvas.dataset.routePathNodes),status:detail.dataset.selectionStatus};
""",
        )
        if (
            satellite_cleared["id"]
            or satellite_cleared["label"]
            or satellite_cleared["kind"]
            or satellite_cleared["served"] != 0
            or satellite_cleared["status"] != "none"
            or satellite_cleared["routeZone"] != selected_zone
            or not satellite_cleared["routeGateway"]
            or satellite_cleared["routeNodes"] < 1
        ):
            raise AssertionError(
                "same-Satellite toggle did not clear object state while "
                f"preserving independent Route context: {satellite_cleared}"
            )
        if (
            _click_canvas_dataset(base_url, session_id, "serviceSatellite")
            != selected_satellite
        ):
            raise AssertionError("reselecting the service Satellite changed identity")
        _execute(
            base_url,
            session_id,
            'document.getElementById("toggle-served-zones").click();return true;',
        )
        interactions["satellite_toggle"] = satellite_cleared
    last_index = int(initial["rangeMax"])
    stale_completion_guard = None
    if last_index > 1:
        race = _execute(
            base_url,
            session_id,
            f"""
const originalAppend=document.head.append.bind(document.head);
let delayedChunk=true;
document.head.append=(...nodes)=>{{
  const node=nodes[0];
  if(delayedChunk&&node?.tagName==="SCRIPT"){{
    delayedChunk=false;
    setTimeout(()=>originalAppend(...nodes),350);
    return;
  }}
  originalAppend(...nodes);
}};
const olderId=Number(window.SKYSEED_SLICE_MANIFEST.slice_manifest[0].slice_id);
const intendedId=Number(window.SKYSEED_SLICE_MANIFEST.slice_manifest[{last_index}].slice_id);
loadSlice(0);
loadSlice({last_index});
return {{olderId,intendedId}};
""",
        )
        _wait_for_slice(
            base_url,
            session_id,
            int(race["intendedId"]),
            timeout,
        )
        time.sleep(0.6)
        stale_completion_guard = _execute(
            base_url,
            session_id,
            r"""
return {
  loadedSliceId:Number(document.getElementById("slice-globe").dataset.loadedSliceId),
  chunkIds:Object.keys(window.__SKYSEED_SLICE_CHUNKS__).map(Number).sort((a,b)=>a-b)
};
""",
        )
        if (
            stale_completion_guard["loadedSliceId"]
            != int(race["intendedId"])
            or stale_completion_guard["chunkIds"]
            != [int(race["intendedId"])]
        ):
            raise AssertionError(
                "stale Slice chunk completion replaced the intended "
                f"selection: {race}, {stale_completion_guard}"
            )
    elif last_index > 0:
        expected = _execute(
            base_url,
            session_id,
            f"return Number(window.SKYSEED_SLICE_MANIFEST.slice_manifest[{last_index}].slice_id);",
        )
        _execute(
            base_url,
            session_id,
            f'document.getElementById("slice-range").value="{last_index}";'
            'document.getElementById("slice-range").dispatchEvent('
            'new Event("input",{bubbles:true}));return true;',
        )
        _wait_for_slice(base_url, session_id, int(expected), timeout)
    final = _execute(
        base_url,
        session_id,
        r"""
const difference=document.getElementById("show-difference");difference.checked=true;difference.dispatchEvent(new Event("change",{bubbles:true}));
return {
  loadedSliceId:Number(document.getElementById("slice-globe").dataset.loadedSliceId),
  chunkObjectCount:Object.keys(window.__SKYSEED_SLICE_CHUNKS__).length,
  differenceCounts:document.getElementById("difference-counts").textContent,
  differenceContext:document.getElementById("difference-context").textContent,
  differenceMode:document.getElementById("slice-globe").dataset.differenceMode,
  differenceAddedDrawn:Number(document.getElementById("slice-globe").dataset.differenceAddedDrawn),
  differenceRemovedDrawn:Number(document.getElementById("slice-globe").dataset.differenceRemovedDrawn),
  differenceParameterDrawn:Number(document.getElementById("slice-globe").dataset.differenceParameterDrawn),
  differenceZonesDrawn:Number(document.getElementById("slice-globe").dataset.differenceZonesDrawn),
  selectedNodeId:document.getElementById("slice-globe").dataset.selectedNodeId,
  selectedConnectionRef:document.getElementById("slice-globe").dataset.selectedConnectionRef,
  selectedZoneId:document.getElementById("slice-globe").dataset.selectedZoneId,
  routeZoneId:document.getElementById("slice-globe").dataset.routeZoneId,
  routeGatewayId:document.getElementById("slice-globe").dataset.routeGatewayId,
  routeDirection:document.getElementById("slice-globe").dataset.routeDirection,
  routeGatewayChoices:Number(document.getElementById("slice-globe").dataset.routeGatewayChoices),
  routeStatus:document.getElementById("slice-globe").dataset.routeStatus,
  sliceContextState:document.getElementById("slice-context").dataset.stateAvailable,
  servedZones:Number(document.getElementById("slice-context").dataset.servedZones),
  routeEntries:Number(document.getElementById("slice-context").dataset.routeEntries),
  physicalContext:document.getElementById("slice-physical-context").textContent,
  status:document.querySelector(".status").textContent,
  canvasWidth:document.getElementById("slice-globe").width
};
""",
    )
    if (
        initial["manifestHasSlices"]
        or initial["chunkObjectCount"] != 1
        or final["chunkObjectCount"] != 1
        or initial["loadedSliceId"] != initial_id
        or final["status"] != "PASS"
        or final["canvasWidth"] <= 0
        or any(not value.startswith("file:") for value in initial["externalScripts"])
        or abs(initial["earthReferenceRadiusM"] - 6_371_008.8) > 0.1
        or not 1.0 < initial["firstSatelliteDisplayRadius"] < 1.2
        or initial["sliceContextState"] != "true"
        or initial["unservedZones"] < 0
        or initial["coveredGroundStations"] < 0
        or initial["routeEntries"] != initial["loadedRouteEntries"]
        or "elevation p5" not in initial["physicalContext"]
        or "surface separation p50" not in initial["physicalContext"]
        or "ECEF slant p50" not in initial["physicalContext"]
        or final["sliceContextState"] != "true"
        or "ECEF slant p50" not in final["physicalContext"]
        or (
            profile == "rich"
            and (
                not 1.087 < initial["firstSatelliteDisplayRadius"] < 1.089
                or initial["servedZones"] < 1
                or final["servedZones"] < 1
                or final["routeEntries"] < 1
                or "348" not in initial["modelContext"]
                or "6 Orbits × 58 Satellites" not in initial["modelContext"]
                or "25 °" not in initial["modelContext"]
                or "≈ 970 km" not in initial["modelContext"]
                or "GatewayHopCountRouteModel" not in initial["modelContext"]
                or "6000 km" not in initial["modelContext"]
            )
        )
    ):
        raise AssertionError(
            f"chunked Slice Tool browser state is invalid: {initial}, {final}"
        )
    if profile == "rich":
        if (
            final["differenceMode"] != "true"
            or final["differenceAddedDrawn"] < 1
            or final["differenceRemovedDrawn"] < 1
            or final["differenceParameterDrawn"] < 1
            or final["differenceZonesDrawn"] < 1
            or final["selectedNodeId"]
            != interactions["satellite_served_zones"]["satellite"]
            or final["selectedConnectionRef"]
            or final["selectedZoneId"]
            or final["routeZoneId"]
            != interactions["visual_route"]["gatewayToZone"]["zone"]
            or final["routeGatewayId"]
            != interactions["visual_route"]["gatewayToZone"]["gateway"]
            or final["routeDirection"] != "gateway-to-zone"
            or final["routeGatewayChoices"] < 1
            or final["routeStatus"]
            not in {"present", "absent", "target-unserved"}
        ):
            raise AssertionError(
                f"timeline continuity or visual Difference failed: {final}"
            )
        affected_route = _execute(
            base_url,
            session_id,
            r"""
const canvas=document.getElementById("slice-globe"),source=canvas.dataset.affectedRouteSource,target=canvas.dataset.affectedRouteTarget;
if(!source||!target)return null;
setRouteSource(source);setRouteTarget(target);
return {source,target,status:canvas.dataset.routeStatus,changedSegments:Number(canvas.dataset.routeChangedSegments),context:document.getElementById("difference-context").textContent};
""",
        )
        if (
            not affected_route
            or affected_route["status"] != "present"
            or affected_route["changedSegments"] < 1
            or "patches:" not in affected_route["context"]
        ):
            raise AssertionError(
                f"contextual Route Difference failed: {affected_route}"
            )
        interactions["contextual_route_difference"] = affected_route
        clear_state = _execute(
            base_url,
            session_id,
            r'''
document.getElementById("clear-inspection").click();
const canvas=document.getElementById("slice-globe");
return {
  node:canvas.dataset.selectedNodeId,
  connection:canvas.dataset.selectedConnectionRef,
  zone:canvas.dataset.selectedZoneId,
  routeZone:canvas.dataset.routeZoneId,
  routeGateway:canvas.dataset.routeGatewayId,
  routeNodes:Number(canvas.dataset.routePathNodes),
  routeStatus:canvas.dataset.routeStatus,
  differenceMode:canvas.dataset.differenceMode,
  incident:Number(canvas.dataset.incidentContextConnections),
  endpoints:Number(canvas.dataset.endpointContextNodes),
  selectionStatus:document.getElementById("selected-detail").dataset.selectionStatus,
  differenceContext:document.getElementById("difference-context").textContent
};
''',
        )
        if (
            clear_state["node"]
            or clear_state["connection"]
            or clear_state["zone"]
            or clear_state["routeZone"]
            or clear_state["routeGateway"]
            or clear_state["routeNodes"] != 0
            or clear_state["routeStatus"] != "none"
            or clear_state["differenceMode"] != "false"
            or clear_state["incident"] != 0
            or clear_state["endpoints"] != 0
            or clear_state["selectionStatus"] != "none"
            or "Select a Connection, Zone, or Route"
            not in clear_state["differenceContext"]
        ):
            raise AssertionError(
                f"Clear inspection did not restore neutral state: {clear_state}"
            )
        interactions["clear_inspection"] = clear_state
        disappearance = _execute(
            base_url,
            session_id,
            r"""
const ref=document.getElementById("slice-globe").dataset.firstRemovedConnectionRef;
if(!ref)return null;
showConnection(ref);
return {id:ref,status:document.getElementById("selected-detail").dataset.selectionStatus,endpoints:Number(document.getElementById("slice-globe").dataset.endpointContextNodes),detail:document.getElementById("selected-detail").textContent};
""",
        )
        if (
            not disappearance
            or disappearance["status"] != "absent"
            or disappearance["endpoints"] != 0
            or "removed" not in disappearance["detail"]
        ):
            raise AssertionError(
                f"Connection disappearance was not explicit: {disappearance}"
            )
        previous_id = _execute(
            base_url,
            session_id,
            f"return Number(window.SKYSEED_SLICE_MANIFEST.slice_manifest[{last_index - 1}].slice_id);",
        )
        _execute(
            base_url,
            session_id,
            f"loadSlice({last_index - 1});return true;",
        )
        _wait_for_slice(base_url, session_id, int(previous_id), timeout)
        before_removal = _execute(
            base_url,
            session_id,
            r"""
const canvas=document.getElementById("slice-globe");
return {id:canvas.dataset.selectedConnectionRef,status:document.getElementById("selected-detail").dataset.selectionStatus,endpoints:Number(canvas.dataset.endpointContextNodes)};
""",
        )
        _execute(
            base_url,
            session_id,
            f"loadSlice({last_index});return true;",
        )
        _wait_for_slice(base_url, session_id, int(final["loadedSliceId"]), timeout)
        after_removal = _execute(
            base_url,
            session_id,
            r"""
const canvas=document.getElementById("slice-globe");
return {id:canvas.dataset.selectedConnectionRef,status:document.getElementById("selected-detail").dataset.selectionStatus,endpoints:Number(canvas.dataset.endpointContextNodes)};
""",
        )
        if (
            before_removal["id"] != disappearance["id"]
            or before_removal["status"] != "present"
            or after_removal
            != {
                "id": disappearance["id"],
                "status": "absent",
                "endpoints": 0,
            }
        ):
            raise AssertionError(
                "Connection identity did not persist across disappearance: "
                f"{before_removal}, {after_removal}"
            )
        interactions["connection_disappearance"] = {
            **disappearance,
            "before": before_removal,
            "after": after_removal,
        }
    return {
        "initial": initial,
        "final": final,
        "interactions": interactions,
        "stale_completion_guard": stale_completion_guard,
    }


def run_browser_smoke(
    page: Path,
    *,
    geckodriver: str,
    timeout: float = 30.0,
    screenshot_directory: Path | None = None,
    profile: str = "rich",
) -> dict[str, object]:
    """Exercise lazy chunks, selection, path and Difference UI in Firefox."""

    if profile not in {"rich", "portability"}:
        raise ValueError(f"unsupported browser profile: {profile}")
    resolved_page = page.resolve()
    if not resolved_page.is_file():
        raise FileNotFoundError(f"Slice / Difference page not found: {resolved_page}")
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
            session = _request(
                f"{base_url}/session",
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
            if not isinstance(session, dict) or not isinstance(
                session.get("sessionId"), str
            ):
                raise RuntimeError("WebDriver did not return a session ID")
            session_id = session["sessionId"]
            _request(
                f"{base_url}/session/{session_id}/window/rect",
                method="POST",
                value={"width": 1600, "height": 1500},
                timeout=timeout,
            )
            _request(
                f"{base_url}/session/{session_id}/url",
                method="POST",
                value={"url": resolved_page.as_uri()},
                timeout=timeout,
            )
            exercised = _exercise_page(base_url, session_id, timeout, profile)
            screenshot = _capture_screenshot(
                base_url,
                session_id,
                screenshot_directory,
                f"{profile}-chunked-slice-tool.png",
            )
            return {
                "status": "PASS",
                "profile": profile,
                "inventory": exercised["initial"]["inventory"],
                "initial_slice_id": exercised["initial"]["loadedSliceId"],
                "final_slice_id": exercised["final"]["loadedSliceId"],
                "loaded_route_entries": exercised["initial"][
                    "loadedRouteEntries"
                ],
                "earth_reference_radius_m": exercised["initial"][
                    "earthReferenceRadiusM"
                ],
                "first_satellite_display_radius": exercised["initial"][
                    "firstSatelliteDisplayRadius"
                ],
                "chunk_object_count": exercised["final"]["chunkObjectCount"],
                "stale_completion_guard": exercised[
                    "stale_completion_guard"
                ],
                "interactions": exercised["interactions"],
                "screenshot": screenshot,
                "browser": session["capabilities"]["browserName"],
                "browser_version": session["capabilities"]["browserVersion"],
                "geckodriver_version": session["capabilities"][
                    "moz:geckodriverVersion"
                ],
            }
        finally:
            if session_id is not None:
                try:
                    _request(
                        f"{base_url}/session/{session_id}",
                        method="DELETE",
                        timeout=5.0,
                    )
                except Exception:
                    pass
            _stop_driver(process, port)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page", type=Path)
    parser.add_argument("--geckodriver", default=_default_geckodriver())
    parser.add_argument("--profile", choices=("rich", "portability"), default="rich")
    parser.add_argument("--screenshots", type=Path)
    arguments = parser.parse_args()
    if not arguments.geckodriver:
        raise SystemExit("geckodriver not found")
    result = run_browser_smoke(
        arguments.page,
        geckodriver=arguments.geckodriver,
        screenshot_directory=arguments.screenshots,
        profile=arguments.profile,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
