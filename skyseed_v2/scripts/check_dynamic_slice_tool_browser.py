#!/usr/bin/env python3
"""Smoke-test a generated Dynamic Slice Tool with Firefox WebDriver."""

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
        raise RuntimeError(
            f"WebDriver HTTP {error.code}: {body}"
        ) from error
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
    """Stop only the snap scope whose command owns this smoke-test port."""

    proc_root = Path("/proc")
    for process_directory in proc_root.iterdir():
        if not process_directory.name.isdigit():
            continue
        try:
            command = (
                process_directory / "cmdline"
            ).read_bytes().split(b"\0")
            cgroup = (
                process_directory / "cgroup"
            ).read_text(encoding="utf-8")
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
        f"{base_url}/session/{session_id}/screenshot",
        timeout=30.0,
    )
    if not isinstance(encoded, str):
        raise RuntimeError("WebDriver screenshot response is not base64 text")
    path = directory / filename
    path.write_bytes(base64.b64decode(encoded))
    return path.name


def _run_portability_profile(
    base_url: str,
    session_id: str,
    screenshot_directory: Path | None,
) -> dict[str, object]:
    result = _execute(
        base_url,
        session_id,
        """
setSlice(1);
const orbit=document.getElementById("orbit-select");
const options=[...orbit.options].filter(value=>value.value!=="*");
const refs=options.map(value=>value.value);
const labels=options.map(value=>value.textContent);
const firstRef=refs[0],secondRef=refs[1];
orbit.value=firstRef;orbit.dispatchEvent(new Event("change",{bubbles:true}));
const firstSatellites=DATA.topology.nodes.filter(
  node=>node.kind==="satellite"&&node.orbit_ref===firstRef
).map(node=>node.node_id);
const secondSatellites=DATA.topology.nodes.filter(
  node=>node.kind==="satellite"&&node.orbit_ref===secondRef
).map(node=>node.node_id);
document.querySelector('[data-view="isl"]').click();
const islLayers=document.getElementById("isl-globe").dataset.layers;
const islDetail=document.getElementById("isl-detail").textContent;
document.querySelector('[data-view="service"]').click();
const serviceDetail=document.getElementById("service-detail").textContent;
document.querySelector('[data-view="routing"]').click();
const routeDetail=document.getElementById("route-detail").textContent;
setTimelineIndex(1);
document.querySelector('[data-view="difference"]').click();
const differenceCounts={};
for(const category of ["connection-structure","parameters","zone","route"]){
  const control=document.getElementById("difference-category");
  control.value=category;control.dispatchEvent(new Event("change",{bubbles:true}));
  differenceCounts[category]=document.querySelectorAll(".difference-item").length;
}
const differenceDetail=document.getElementById("difference-detail").textContent;
const differenceLayers=document.getElementById("difference-globe").dataset.layers;
const differenceSummary=document.getElementById("difference-summary").textContent;
const fullContext=document.getElementById("difference-satellite-layer").checked;
document.getElementById("shared-timeline").scrollIntoView({block:"start"});
return {
  inventory:DATA.inventory,
  refs,labels,firstSatellites,secondSatellites,
  selectedOrbit:orbit.value,
  synchronizedOrbit:document.getElementById("isl-orbit-filter").value,
  colors:[orbitColor(firstRef),orbitColor(secondRef)],
  sliceMarkers:document.querySelectorAll(".timeline-marker.slice").length,
  differenceMarkers:document.querySelectorAll(".timeline-marker.difference").length,
  label:document.getElementById("slice-label").textContent,
  islLayers,islDetail,serviceDetail,routeDetail,differenceCounts,differenceDetail,
  differenceLayers,differenceSummary,fullContext,
  comparisonSliceId:comparisonSlice().slice_id,
  baseSliceId:displayBaseSlice().slice_id
};
""",
    )
    inventory = result["inventory"]
    if (
        len(result["refs"]) != 2
        or len(set(result["refs"])) != 2
        or not all("AS" in label and "shared-orbit" in label for label in result["labels"])
        or result["selectedOrbit"] != result["refs"][0]
        or result["synchronizedOrbit"] != result["refs"][0]
        or len(result["firstSatellites"]) != 1
        or len(result["secondSatellites"]) != 1
        or result["firstSatellites"] == result["secondSatellites"]
        or result["colors"][0] == result["colors"][1]
        or inventory["nodes"] != 2
        or inventory["orbits"] != 2
        or inventory["zones"] != 0
        or inventory["paths"] != 0
        or result["sliceMarkers"] != inventory["slices"]
        or result["differenceMarkers"] != inventory["slices"] - 1
        or "ecef-orbit-guides" in result["islLayers"]
        or "None for this Slice/filter." not in result["islDetail"]
        or "None for this Slice/filter." not in result["serviceDetail"]
        or "No formal path" not in result["routeDetail"]
        or any(result["differenceCounts"].values())
        or "No discrete network-state change." not in result["differenceDetail"]
        or "No discrete network-state change" not in result["differenceSummary"]
        or "comparison-geometry-current-fallback"
        not in result["differenceLayers"]
        or "no-discrete-network-state-change"
        not in result["differenceLayers"]
        or result["fullContext"]
        or result["baseSliceId"] != 0
        or result["comparisonSliceId"] != 1
        or any(
            value in result["differenceLayers"]
            for value in (
                "position-motion-vectors",
                "ecef-orbit-guides-current",
                "position-comparison-endpoints",
            )
        )
        or not result["label"].startswith("Δ")
    ):
        raise AssertionError(f"portability profile is invalid: {result}")
    screenshot = _capture_screenshot(
        base_url,
        session_id,
        screenshot_directory,
        "portability-duplicate-orbit-empty-state.png",
    )
    return {
        "status": "PASS",
        "profile": "portability",
        "orbit_refs": result["refs"],
        "inventory": inventory,
        "timeline_label": result["label"],
        "screenshot": screenshot,
    }


def run_browser_smoke(
    page: Path,
    *,
    geckodriver: str,
    timeout: float = 30.0,
    screenshot_directory: Path | None = None,
    profile: str = "rich",
) -> dict[str, object]:
    """Exercise shared-context presets and transitions in real Firefox."""

    resolved_page = page.resolve()
    if not resolved_page.is_file():
        raise FileNotFoundError(f"Dynamic Slice page not found: {resolved_page}")
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

            if profile == "portability":
                result = _run_portability_profile(
                    base_url,
                    session_id,
                    screenshot_directory,
                )
                result.update(
                    {
                        "browser": session["capabilities"]["browserName"],
                        "browser_version": session["capabilities"][
                            "browserVersion"
                        ],
                        "geckodriver_version": session["capabilities"][
                            "moz:geckodriverVersion"
                        ],
                    }
                )
                return result
            if profile != "rich":
                raise ValueError(f"unsupported browser profile: {profile}")

            initial = _execute(
                base_url,
                session_id,
                """
const canvas=document.getElementById("topology-globe");
return {
  title:document.title,
  label:document.getElementById("slice-label").textContent,
  timestamp:document.getElementById("timestamp-label").textContent,
  active:document.querySelector('.view-tab[aria-selected="true"]').dataset.view,
  layers:canvas.dataset.layers,
  sliceMarkers:document.querySelectorAll('.timeline-marker.slice').length,
  differenceMarkers:document.querySelectorAll('.timeline-marker.difference').length,
  currentMarkers:document.querySelectorAll('.timeline-marker[aria-current="true"]').length,
  syncCamera:document.getElementById("sync-camera").checked,
  structured:document.querySelectorAll(".structured").length,
  rawAudit:document.querySelector(".audit summary").textContent
};
""",
            )
            if (
                initial["title"] != "Dynamic Slice Inspection Tool"
                or initial["label"] != "Slice 0"
                or "synthetic baseline" not in initial["timestamp"]
                or initial["active"] != "topology"
                or "ecef-orbit-guides" in initial["layers"]
                or initial["differenceMarkers"] != initial["sliceMarkers"] - 1
                or initial["currentMarkers"] != 1
                or not initial["syncCamera"]
                or initial["structured"] != 5
                or "Raw JSON / Audit View" not in initial["rawAudit"]
            ):
                raise AssertionError(f"initial shared timeline is invalid: {initial}")

            topology = _execute(
                base_url,
                session_id,
                """
setSlice(1);
const orbit=document.getElementById("orbit-select");
orbit.value=orbit.options[1].value;
orbit.dispatchEvent(new Event("change",{bubbles:true}));
const formalOrbit=orbitsByRef[orbit.value];
const guideData=displayBaseSlice().orbit_guides.find(
  value=>value.orbit_ref===formalOrbit.orbit_ref
);
const guide=orbitGuide(guideData);
const norm=value=>Math.hypot(...value);
const guideRadius=norm(guide[0]);
const satelliteRadii=formalOrbit.satellite_ids.map(id=>norm(nodePoint(id)));
const normal=guideData.plane_normal_ecef_unit;
const dot=(a,b)=>a.x*b.x+a.y*b.y+a.z*b.z;
const unit=value=>{
  const magnitude=Math.hypot(value.x,value.y,value.z);
  return {x:value.x/magnitude,y:value.y/magnitude,z:value.z/magnitude};
};
const maximumResidual=Math.max(...formalOrbit.satellite_ids.map(id=>
  Math.abs(dot(unit(Object.fromEntries(
    displayBaseSlice().positions.map(value=>[value.node_id,value])
  )[id].ecef_m),normal))
));
const nextGuide=DATA.slices[2].orbit_guides.find(
  value=>value.orbit_ref===formalOrbit.orbit_ref
);
const normalChange=1-Math.abs(dot(normal,nextGuide.plane_normal_ecef_unit));
document.getElementById("shared-timeline").scrollIntoView({block:"start"});
return {
  label:document.getElementById("slice-label").textContent,
  orbit:orbit.value,
  orbitId:formalOrbit.orbit_id,
  syncedOrbit:document.getElementById("isl-orbit-filter").value,
  layers:document.getElementById("topology-globe").dataset.layers,
  detail:document.getElementById("orbit-detail").textContent,
  guideRadius,
  expectedRadius:guideData.mean_radius_m/WGS84_DISPLAY_RADIUS_M,
  meanSatelliteRadius:satelliteRadii.reduce((a,b)=>a+b,0)/satelliteRadii.length,
  maximumResidual,
  recordedResidual:guideData.max_unit_plane_residual,
  normalChange,
  guideSource:guideData.source
};
""",
            )
            if (
                topology["label"] != "Slice 1"
                or topology["orbit"] != topology["syncedOrbit"]
                or "ecef-orbit-guides-previous" not in topology["layers"]
                or any(
                    value in topology["layers"]
                    for value in ("isl", "route", "difference", "zone-service")
                )
                or topology["orbitId"] not in topology["detail"]
                or topology["guideRadius"] <= 1
                or abs(topology["guideRadius"] - topology["expectedRadius"]) > 1e-12
                or abs(
                    topology["guideRadius"] - topology["meanSatelliteRadius"]
                ) > 1e-10
                or topology["maximumResidual"] > 1e-12
                or topology["recordedResidual"] > 1e-12
                or topology["normalChange"] <= 1e-8
                or topology["guideSource"] != "formal-ecef-positions"
            ):
                raise AssertionError(f"Topology/orbit geometry is invalid: {topology}")
            screenshots = [
                _capture_screenshot(
                    base_url,
                    session_id,
                    screenshot_directory,
                    "topology-orbit-ecef-aligned.png",
                )
            ]
            topology_transition = _execute(
                base_url,
                session_id,
                """
const canvasHash=id=>{
  const canvas=document.getElementById(id);
  const data=canvas.getContext("2d").getImageData(0,0,canvas.width,canvas.height).data;
  let hash=2166136261;
  for(let offset=0;offset<data.length;offset+=97){
    hash^=data[offset];hash=Math.imul(hash,16777619);
  }
  return hash>>>0;
};
setTimelineIndex(2);
const before=canvasHash("topology-globe");
setTimelineIndex(3);
const transition={
  hash:canvasHash("topology-globe"),
  layers:document.getElementById("topology-globe").dataset.layers,
  detail:document.getElementById("orbit-detail").textContent
};
setTimelineIndex(4);
const after={
  hash:canvasHash("topology-globe"),
  layers:document.getElementById("topology-globe").dataset.layers
};
setSlice(1);
return {before,transition,after};
""",
            )
            if (
                len(
                    {
                        topology_transition["before"],
                        topology_transition["transition"]["hash"],
                        topology_transition["after"]["hash"],
                    }
                )
                != 3
                or "transition-previous-base"
                not in topology_transition["transition"]["layers"]
                or "ecef-orbit-guides-previous"
                not in topology_transition["transition"]["layers"]
                or "ecef-orbit-guides-current"
                not in topology_transition["transition"]["layers"]
                or "position-motion-vectors"
                not in topology_transition["transition"]["layers"]
                or "position-comparison-endpoints"
                not in topology_transition["transition"]["layers"]
                or "No intermediate Position exists"
                not in topology_transition["transition"]["detail"]
                or "transition-previous-base"
                in topology_transition["after"]["layers"]
            ):
                raise AssertionError(
                    f"Topology previous/transition/after rendering is invalid: "
                    f"{topology_transition}"
                )
            _execute(
                base_url,
                session_id,
                """
setTimelineIndex(3);
document.querySelector('[data-view="topology"]').click();
document.getElementById("shared-timeline").scrollIntoView({block:"start"});
return timelineLabel();
""",
            )
            screenshots.append(
                _capture_screenshot(
                    base_url,
                    session_id,
                    screenshot_directory,
                    "topology-position-transition.png",
                )
            )
            _execute(base_url, session_id, "setSlice(1);return timelineLabel();")

            isl = _execute(
                base_url,
                session_id,
                """
document.querySelector('[data-view="isl"]').click();
const orbit=document.getElementById("isl-orbit-filter");
orbit.value="*";orbit.dispatchEvent(new Event("change",{bubbles:true}));
const node=document.getElementById("isl-node-filter");
const cross=displayBaseSlice().connections.filter(
  value=>value.display_kind==="cross-orbit-isl"
);
const sideSets=new Map();
cross.forEach(value=>{
  const id=value.connection_id;
  [[id.endpoint_a,value.cross_plane_sides?.endpoint_a],
   [id.endpoint_b,value.cross_plane_sides?.endpoint_b]].forEach(([nodeId,side])=>{
    if(!sideSets.has(nodeId))sideSets.set(nodeId,new Set());
    sideSets.get(nodeId).add(side);
  });
});
const candidate=[...sideSets].find(([,sides])=>
  sides.has("predecessor-orbit")&&sides.has("successor-orbit")
);
if(!candidate)throw new Error("no Satellite has both adjacent-Orbit sides");
node.value=candidate[0];
node.dispatchEvent(new Event("change",{bubbles:true}));
const camera={yaw:sharedCamera.yaw,pitch:sharedCamera.pitch};
document.getElementById("shared-timeline").scrollIntoView({block:"start"});
return {
  layers:document.getElementById("isl-globe").dataset.layers,
  node:node.value,
  detail:document.getElementById("isl-detail").textContent,
  camera,
  orbit:document.getElementById("isl-orbit-filter").value
};
""",
            )
            if (
                "stable-ecef-orbit-guides" not in isl["layers"]
                or "stable-satellite-context" not in isl["layers"]
                or "same-orbit-isl" not in isl["layers"]
                or "cross-orbit-isl" not in isl["layers"]
                or isl["node"] not in isl["detail"]
                or "predecessor-orbit" not in isl["detail"]
                or "successor-orbit" not in isl["detail"]
                or any(
                    value in isl["layers"]
                    for value in ("ground-station-access", "zone-service", "route", "difference")
                )
            ):
                raise AssertionError(f"ISL preset is invalid: {isl}")
            screenshots.append(
                _capture_screenshot(
                    base_url,
                    session_id,
                    screenshot_directory,
                    "isl-orbit-context.png",
                )
            )
            screenshots.append(
                _capture_screenshot(
                    base_url,
                    session_id,
                    screenshot_directory,
                    "isl-predecessor-successor-cross-plane.png",
                )
            )

            service = _execute(
                base_url,
                session_id,
                """
document.querySelector('[data-view="service"]').click();
document.getElementById("shared-timeline").scrollIntoView({block:"start"});
return {
  layers:document.getElementById("service-globe").dataset.layers,
  detail:document.getElementById("service-detail").textContent,
  camera:{yaw:sharedCamera.yaw,pitch:sharedCamera.pitch},
  selectedNode:document.getElementById("isl-node-filter").value
};
""",
            )
            if (
                "stable-ecef-orbit-guides" not in service["layers"]
                or "stable-satellite-context" not in service["layers"]
                or "ground-station-access" not in service["layers"]
                or "zone-service" not in service["layers"]
                or "zones" not in service["layers"]
                or "GroundStation access" not in service["detail"]
                or "Zone service" not in service["detail"]
                or service["camera"] != isl["camera"]
                or service["selectedNode"] != isl["node"]
                or any(
                    value in service["layers"]
                    for value in ("same-orbit-isl", "cross-orbit-isl", "route", "difference")
                )
            ):
                raise AssertionError(f"Ground/Zone preset is invalid: {service}")
            screenshots.append(
                _capture_screenshot(
                    base_url,
                    session_id,
                    screenshot_directory,
                    "ground-zone-constellation-context.png",
                )
            )
            outside_service = _execute(
                base_url,
                session_id,
                """
const record=DATA.slices.slice(1).find(slice=>
  slice.zone_service_assignments.some(
    value=>value.display_explanation?.subpoint_inside_zone===false
  )
);
if(!record)throw new Error("no outside-subpoint formal assignment");
const assignment=record.zone_service_assignments.find(
  value=>value.display_explanation?.subpoint_inside_zone===false
);
setSlice(record.slice_id);
document.querySelector('[data-view="service"]').click();
document.getElementById("shared-timeline").scrollIntoView({block:"start"});
return {
  sliceId:record.slice_id,
  zoneId:assignment.zone_id,
  satelliteId:assignment.service_satellite_id,
  elevation:assignment.display_explanation.representative_point_elevation_deg,
  inside:assignment.display_explanation.subpoint_inside_zone,
  detail:document.getElementById("service-detail").textContent
};
""",
            )
            if (
                outside_service["inside"]
                or outside_service["elevation"] < 10.0
                or outside_service["zoneId"] not in outside_service["detail"]
                or outside_service["satelliteId"]
                not in outside_service["detail"]
                or "subpoint containment" not in outside_service["detail"]
            ):
                raise AssertionError(
                    "Zone-service explanation is invalid: "
                    f"{outside_service}"
                )
            screenshots.append(
                _capture_screenshot(
                    base_url,
                    session_id,
                    screenshot_directory,
                    "zone-service-subpoint-outside.png",
                )
            )
            screenshots.append(
                _capture_screenshot(
                    base_url,
                    session_id,
                    screenshot_directory,
                    "ground-station-zone-separation.png",
                )
            )

            routing_g = _execute(
                base_url,
                session_id,
                """
document.querySelector('[data-view="routing"]').click();
const path=displayBaseSlice().paths.find(value=>value.target.kind==="G");
if(!path)throw new Error("no formal G path");
const source=document.getElementById("route-source");
source.value=path.source;source.dispatchEvent(new Event("change",{bubbles:true}));
const target=document.getElementById("route-target");
target.value=path.path_id;target.dispatchEvent(new Event("change",{bubbles:true}));
document.querySelector('[data-view="topology"]').click();
document.querySelector('[data-view="routing"]').click();
document.getElementById("shared-timeline").scrollIntoView({block:"start"});
return {
  source:source.value,target:target.value,
  layers:document.getElementById("routing-globe").dataset.layers,
  detail:document.getElementById("route-detail").textContent
};
""",
            )
            if (
                "selected-path" not in routing_g["layers"]
                or "g-target" not in routing_g["layers"]
                or "stable-ecef-orbit-guides" not in routing_g["layers"]
                or "stable-satellite-context" not in routing_g["layers"]
                or routing_g["source"] not in routing_g["detail"]
                or "Formal resolved path" not in routing_g["detail"]
                or any(
                    value in routing_g["layers"]
                    for value in ("all-connection", "zone-service", "difference")
                )
            ):
                raise AssertionError(f"G routing preset is invalid: {routing_g}")
            screenshots.append(
                _capture_screenshot(
                    base_url,
                    session_id,
                    screenshot_directory,
                    "route-g-target.png",
                )
            )

            routing_z = _execute(
                base_url,
                session_id,
                """
const path=displayBaseSlice().paths.find(value=>value.target.kind==="Z");
if(!path)throw new Error("no formal Z path");
const source=document.getElementById("route-source");
source.value=path.source;source.dispatchEvent(new Event("change",{bubbles:true}));
const target=document.getElementById("route-target");
target.value=path.path_id;target.dispatchEvent(new Event("change",{bubbles:true}));
document.getElementById("shared-timeline").scrollIntoView({block:"start"});
return {
  source:source.value,target:target.value,
  layers:document.getElementById("routing-globe").dataset.layers,
  detail:document.getElementById("route-detail").textContent
};
""",
            )
            if (
                "selected-path" not in routing_z["layers"]
                or "target-zone" not in routing_z["layers"]
                or "zone-service-satellite" not in routing_z["layers"]
                or "service Satellite" not in routing_z["detail"]
            ):
                raise AssertionError(f"Z routing preset is invalid: {routing_z}")
            screenshots.append(
                _capture_screenshot(
                    base_url,
                    session_id,
                    screenshot_directory,
                    "route-z-target-zone-service.png",
                )
            )

            connection_transition = _execute(
                base_url,
                session_id,
                """
const index=DATA.slices.findIndex((slice,index)=>index>0&&(
  slice.difference_from_previous.added_connections.length||
  slice.difference_from_previous.removed_connection_ids.length
));
setTimelineIndex(index*2-1);
document.querySelector('[data-view="difference"]').click();
const category=document.getElementById("difference-category");
category.value="connection-structure";
category.dispatchEvent(new Event("change",{bubbles:true}));
const connectionItemIndex=differenceItems().findIndex(item=>
  item.value.connection_id.kind==="inter-satellite"
);
document.querySelectorAll(".difference-item")[
  connectionItemIndex<0?0:connectionItemIndex
]?.click();
const focusedLayers=document.getElementById("difference-globe").dataset.layers;
const fullContext=document.getElementById("difference-satellite-layer");
const fullContextDefault=fullContext.checked;
fullContext.checked=true;
fullContext.dispatchEvent(new Event("change",{bubbles:true}));
const fullContextLayers=document.getElementById("difference-globe").dataset.layers;
fullContext.checked=false;
fullContext.dispatchEvent(new Event("change",{bubbles:true}));
document.getElementById("shared-timeline").scrollIntoView({block:"start"});
return {
  index,label:document.getElementById("slice-label").textContent,
  layers:document.getElementById("difference-globe").dataset.layers,
  detail:document.getElementById("difference-detail").textContent,
  items:document.querySelectorAll(".difference-item").length,
  focusedLayers,fullContextLayers,fullContextDefault,
  comparisonSliceId:comparisonSlice().slice_id,
  baseSliceId:displayBaseSlice().slice_id
};
""",
            )
            if (
                not connection_transition["label"].startswith("Δ")
                or "difference-connection-structure"
                not in connection_transition["layers"]
                or connection_transition["items"] < 1
                or "previous" not in connection_transition["detail"]
                or "current" not in connection_transition["detail"]
                or connection_transition["baseSliceId"] != 0
                or connection_transition["comparisonSliceId"] != 1
                or "comparison-geometry-current-fallback"
                not in connection_transition["focusedLayers"]
                or "focused-selected-item"
                not in connection_transition["focusedLayers"]
                or "focused-relevant-orbits"
                not in connection_transition["focusedLayers"]
                or connection_transition["fullContextDefault"]
                or "full-constellation-stable-context"
                not in connection_transition["fullContextLayers"]
                or any(
                    value in connection_transition["fullContextLayers"]
                    for value in (
                        "position-motion-vectors",
                        "ecef-orbit-guides-current",
                        "position-comparison-endpoints",
                    )
                )
            ):
                raise AssertionError(
                    "Connection transition is invalid: "
                    f"{connection_transition}"
                )
            screenshots.append(
                _capture_screenshot(
                    base_url,
                    session_id,
                    screenshot_directory,
                    "difference-connection-focused-item.png",
                )
            )

            parameter_transition = _execute(
                base_url,
                session_id,
                """
const index=DATA.slices.findIndex((slice,index)=>index>0&&
  slice.difference_from_previous.connection_parameter_patches.length);
setTimelineIndex(index*2-1);
const category=document.getElementById("difference-category");
category.value="parameters";category.dispatchEvent(new Event("change",{bubbles:true}));
document.querySelector(".difference-item")?.click();
return {
  label:document.getElementById("slice-label").textContent,
  layers:document.getElementById("difference-globe").dataset.layers,
  detail:document.getElementById("difference-detail").textContent
};
""",
            )
            if (
                not parameter_transition["label"].startswith("Δ")
                or "difference-parameters" not in parameter_transition["layers"]
                or "Directional parameter transition"
                not in parameter_transition["detail"]
                or "previous" not in parameter_transition["detail"]
                or "current" not in parameter_transition["detail"]
            ):
                raise AssertionError(
                    f"Parameter transition is invalid: {parameter_transition}"
                )

            zone_transition = _execute(
                base_url,
                session_id,
                """
const index=DATA.slices.findIndex((slice,index)=>index>0&&
  slice.difference_from_previous.zone_service_assignment_patches.some(
    patch=>patch.operation==="changed"
  ));
setTimelineIndex(index*2-1);
const category=document.getElementById("difference-category");
category.value="zone";category.dispatchEvent(new Event("change",{bubbles:true}));
const patchIndex=displayDifference().zone_service_assignment_patches.findIndex(
  patch=>patch.operation==="changed"
);
document.querySelectorAll(".difference-item")[patchIndex].click();
document.getElementById("shared-timeline").scrollIntoView({block:"start"});
return {
  index,label:document.getElementById("slice-label").textContent,
  layers:document.getElementById("difference-globe").dataset.layers,
  detail:document.getElementById("difference-detail").textContent
};
""",
            )
            if (
                not zone_transition["label"].startswith("Δ")
                or "difference-zone" not in zone_transition["layers"]
                or "Zone service transition" not in zone_transition["detail"]
                or "previous" not in zone_transition["detail"]
                or "current" not in zone_transition["detail"]
            ):
                raise AssertionError(
                    f"Zone transition is invalid: {zone_transition}"
                )
            screenshots.append(
                _capture_screenshot(
                    base_url,
                    session_id,
                    screenshot_directory,
                    "difference-zone-handover-focused-item.png",
                )
            )

            route_transition = _execute(
                base_url,
                session_id,
                """
const index=DATA.slices.findIndex((slice,index)=>index>0&&
  slice.difference_from_previous.route_patches.some(
    patch=>patch.operation==="changed"
  ));
setTimelineIndex(index*2-1);
const category=document.getElementById("difference-category");
category.value="route";category.dispatchEvent(new Event("change",{bubbles:true}));
const patchIndex=displayDifference().route_patches.findIndex(
  patch=>patch.operation==="changed"
);
document.querySelectorAll(".difference-item")[patchIndex].click();
document.getElementById("shared-timeline").scrollIntoView({block:"start"});
return {
  index,label:document.getElementById("slice-label").textContent,
  layers:document.getElementById("difference-globe").dataset.layers,
  detail:document.getElementById("difference-detail").textContent
};
""",
            )
            if (
                not route_transition["label"].startswith("Δ")
                or "difference-route" not in route_transition["layers"]
                or "Route next-hop transition" not in route_transition["detail"]
                or "formal resolved path" not in route_transition["detail"]
            ):
                raise AssertionError(
                    f"Route transition is invalid: {route_transition}"
                )
            screenshots.append(
                _capture_screenshot(
                    base_url,
                    session_id,
                    screenshot_directory,
                    "difference-route-change.png",
                )
            )

            isl_transition = _execute(
                base_url,
                session_id,
                """
const index=DATA.slices.findIndex((slice,index)=>index>0&&
  slice.difference_from_previous.added_connections.some(
    value=>value.connection_id.kind==="inter-satellite"
  )&&slice.difference_from_previous.removed_connection_ids.some(
    value=>value.connection_id.kind==="inter-satellite"
  )
);
if(index<1)throw new Error("no ISL add/remove transition");
const canvasHash=id=>{
  const canvas=document.getElementById(id);
  const data=canvas.getContext("2d").getImageData(0,0,canvas.width,canvas.height).data;
  let hash=2166136261;
  for(let offset=0;offset<data.length;offset+=97){
    hash^=data[offset];hash=Math.imul(hash,16777619);
  }
  return hash>>>0;
};
document.querySelector('[data-view="isl"]').click();
const orbitFilter=document.getElementById("isl-orbit-filter");
orbitFilter.value="*";
orbitFilter.dispatchEvent(new Event("change",{bubbles:true}));
const nodeFilter=document.getElementById("isl-node-filter");
nodeFilter.value="*";
nodeFilter.dispatchEvent(new Event("change",{bubbles:true}));
setTimelineIndex(index*2-2);
const before={label:timelineLabel(),hash:canvasHash("isl-globe"),
  layers:document.getElementById("isl-globe").dataset.layers};
setTimelineIndex(index*2-1);
const transition={label:timelineLabel(),hash:canvasHash("isl-globe"),
  layers:document.getElementById("isl-globe").dataset.layers};
setTimelineIndex(index*2);
const after={label:timelineLabel(),hash:canvasHash("isl-globe"),
  layers:document.getElementById("isl-globe").dataset.layers};
return {index,before,transition,after};
""",
            )
            if (
                not isl_transition["before"]["label"].startswith("Slice ")
                or not isl_transition["transition"]["label"].startswith("Δ")
                or not isl_transition["after"]["label"].startswith("Slice ")
                or len(
                    {
                        isl_transition["before"]["hash"],
                        isl_transition["transition"]["hash"],
                        isl_transition["after"]["hash"],
                    }
                )
                != 3
                or "transition-previous-network-base"
                not in isl_transition["transition"]["layers"]
                or "isl-added" not in isl_transition["transition"]["layers"]
                or "isl-removed" not in isl_transition["transition"]["layers"]
                or "stable-ecef-orbit-guides"
                not in isl_transition["transition"]["layers"]
                or any(
                    value in isl_transition["transition"]["layers"]
                    for value in (
                        "position-motion-vectors",
                        "ecef-orbit-guides-current",
                        "position-comparison-endpoints",
                    )
                )
                or "transition-previous-network-base"
                in isl_transition["after"]["layers"]
            ):
                raise AssertionError(
                    f"ISL previous/transition/after rendering is invalid: "
                    f"{isl_transition}"
                )
            isl_index = int(isl_transition["index"])
            for timeline_value, filename in (
                (isl_index * 2 - 2, "slice-n-isl.png"),
                (isl_index * 2 - 1, "delta-n-to-n1-isl-add-remove.png"),
                (isl_index * 2, "slice-n1-isl.png"),
            ):
                _execute(
                    base_url,
                    session_id,
                    f"""
setTimelineIndex({timeline_value});
document.getElementById("shared-timeline").scrollIntoView({{block:"start"}});
return document.getElementById("slice-label").textContent;
""",
                )
                screenshots.append(
                    _capture_screenshot(
                        base_url,
                        session_id,
                        screenshot_directory,
                        filename,
                    )
                )

            service_transition = _execute(
                base_url,
                session_id,
                """
const index=DATA.slices.findIndex((slice,index)=>index>0&&
  slice.difference_from_previous.zone_service_assignment_patches.some(
    patch=>patch.operation==="changed"
  )
);
setTimelineIndex(index*2-1);
document.querySelector('[data-view="service"]').click();
const transitionLayers=document.getElementById("service-globe").dataset.layers;
setTimelineIndex(index*2);
const afterLayers=document.getElementById("service-globe").dataset.layers;
setTimelineIndex(index*2-1);
document.getElementById("shared-timeline").scrollIntoView({block:"start"});
return {
  index,
  label:timelineLabel(),
  transitionLayers,
  afterLayers,
  detail:document.getElementById("service-detail").textContent
};
""",
            )
            if (
                not service_transition["label"].startswith("Δ")
                or "zone-service-transition"
                not in service_transition["transitionLayers"]
                or "transition-previous-network-base"
                not in service_transition["transitionLayers"]
                or "stable-ecef-orbit-guides"
                not in service_transition["transitionLayers"]
                or any(
                    value in service_transition["transitionLayers"]
                    for value in (
                        "position-motion-vectors",
                        "ecef-orbit-guides-current",
                        "position-comparison-endpoints",
                    )
                )
                or "zone-service-transition"
                in service_transition["afterLayers"]
                or "Previous access and service relations"
                not in service_transition["detail"]
            ):
                raise AssertionError(
                    f"Service transition overlay is invalid: "
                    f"{service_transition}"
                )
            screenshots.append(
                _capture_screenshot(
                    base_url,
                    session_id,
                    screenshot_directory,
                    "service-zone-handover-focused-transition.png",
                )
            )

            routing_transition = _execute(
                base_url,
                session_id,
                """
const index=DATA.slices.findIndex((slice,index)=>index>0&&
  slice.difference_from_previous.route_patches.some(
    patch=>patch.operation==="changed"
  )
);
setTimelineIndex(index*2-1);
document.querySelector('[data-view="routing"]').click();
const patch=displayDifference().route_patches.find(value=>value.operation==="changed");
const source=document.getElementById("route-source");
source.value=patch.source;source.dispatchEvent(new Event("change",{bubbles:true}));
const target=document.getElementById("route-target");
target.value=patch.route_ref;target.dispatchEvent(new Event("change",{bubbles:true}));
document.getElementById("shared-timeline").scrollIntoView({block:"start"});
return {
  index,
  label:timelineLabel(),
  layers:document.getElementById("routing-globe").dataset.layers,
  detail:document.getElementById("route-detail").textContent,
  previousPath:displayPreviousSlice().paths.find(
    value=>value.path_id===patch.route_ref
  )?.nodes??null,
  currentPath:displayCurrentSlice().paths.find(
    value=>value.path_id===patch.route_ref
  )?.nodes??null
};
""",
            )
            if (
                not routing_transition["label"].startswith("Δ")
                or "previous-formal-path" not in routing_transition["layers"]
                or "current-formal-path" not in routing_transition["layers"]
                or "stable-ecef-orbit-guides"
                not in routing_transition["layers"]
                or any(
                    value in routing_transition["layers"]
                    for value in (
                        "position-motion-vectors",
                        "ecef-orbit-guides-current",
                        "position-comparison-endpoints",
                    )
                )
                or "Previous formal resolved path"
                not in routing_transition["detail"]
                or routing_transition["previousPath"] is None
                or routing_transition["currentPath"] is None
            ):
                raise AssertionError(
                    f"Routing task transition is invalid: "
                    f"{routing_transition}"
                )
            screenshots.append(
                _capture_screenshot(
                    base_url,
                    session_id,
                    screenshot_directory,
                    "routing-focused-old-new-formal-path.png",
                )
            )

            _execute(
                base_url,
                session_id,
                """
setTimelineIndex(0);
document.querySelector('[data-view="topology"]').click();
window.__playbackLabels=[document.getElementById("slice-label").textContent];
window.__playbackObserver=new MutationObserver(()=>
  window.__playbackLabels.push(document.getElementById("slice-label").textContent)
);
window.__playbackObserver.observe(
  document.getElementById("slice-label"),{childList:true,subtree:true}
);
document.getElementById("play-pause").click();
return true;
""",
            )
            playback_deadline = time.monotonic() + min(timeout, 6.0)
            while True:
                playback_labels = _execute(
                    base_url,
                    session_id,
                    """
return [...new Set(window.__playbackLabels)];
""",
                )
                if playback_labels[:3] == ["Slice 0", "Δ0→1", "Slice 1"]:
                    break
                if time.monotonic() >= playback_deadline:
                    break
                time.sleep(0.1)
            playback = _execute(
                base_url,
                session_id,
                """
document.getElementById("play-pause").click();
window.__playbackObserver.disconnect();
return {
  labels:[...new Set(window.__playbackLabels)],
  button:document.getElementById("play-pause").textContent,
  contexts:[...document.querySelectorAll(".view-context")].map(value=>value.textContent)
};
""",
            )
            if (
                playback["labels"][:3] != ["Slice 0", "Δ0→1", "Slice 1"]
                or playback["button"] != "Play"
                or set(playback["contexts"]) != {playback["labels"][-1]}
            ):
                raise AssertionError(
                    f"Slice/Difference playback is invalid: {playback}"
                )

            return {
                "status": "PASS",
                "browser": session["capabilities"]["browserName"],
                "browser_version": session["capabilities"]["browserVersion"],
                "geckodriver_version": session["capabilities"][
                    "moz:geckodriverVersion"
                ],
                "initial_slice": initial["label"],
                "slice_markers": initial["sliceMarkers"],
                "difference_markers": initial["differenceMarkers"],
                "topology_orbit": topology["orbit"],
                "orbit_guide_radius": topology["guideRadius"],
                "mean_satellite_radius": topology["meanSatelliteRadius"],
                "isl_node": isl["node"],
                "outside_service": outside_service,
                "routing_g_target": routing_g["target"],
                "routing_z_target": routing_z["target"],
                "connection_transition": connection_transition["label"],
                "parameter_transition": parameter_transition["label"],
                "zone_transition": zone_transition["label"],
                "route_transition": route_transition["label"],
                "isl_transition": isl_transition["transition"]["label"],
                "service_transition": service_transition["label"],
                "routing_task_transition": routing_transition["label"],
                "initial_comparison_geometry": (
                    f"Slice {connection_transition['comparisonSliceId']}"
                ),
                "maximum_orbit_plane_residual": topology[
                    "maximumResidual"
                ],
                "orbit_normal_change": topology["normalChange"],
                "playback_sequence": playback["labels"][:3],
                "screenshots": [
                    value for value in screenshots if value is not None
                ],
            }
        finally:
            if session_id is not None:
                try:
                    _request(
                        f"{base_url}/session/{session_id}",
                        method="DELETE",
                        timeout=timeout,
                    )
                except Exception:
                    pass
            _stop_driver(process, port)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page", type=Path)
    parser.add_argument(
        "--geckodriver",
        default=_default_geckodriver(),
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--screenshot-directory", type=Path)
    parser.add_argument(
        "--profile",
        choices=("rich", "portability"),
        default="rich",
    )
    arguments = parser.parse_args()
    if not arguments.geckodriver:
        parser.error("geckodriver is not available")
    result = run_browser_smoke(
        arguments.page,
        geckodriver=arguments.geckodriver,
        timeout=arguments.timeout,
        screenshot_directory=arguments.screenshot_directory,
        profile=arguments.profile,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
