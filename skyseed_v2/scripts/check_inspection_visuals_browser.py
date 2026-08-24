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


def _topology(base_url: str, session_id: str, page: Path) -> dict[str, object]:
    _navigate(base_url, session_id, page)
    initial = _execute(
        base_url,
        session_id,
        """
const canvas=document.getElementById("orbit-canvas"),ctx=canvas.getContext("2d");
const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data;
const radius=Math.min(canvas.width,canvas.height)*.34;
let outside=0;
for(let y=0;y<canvas.height;y+=2)for(let x=0;x<canvas.width;x+=2){
  if(Math.hypot(x-canvas.width/2,y-canvas.height/2)>radius+3&&pixels[(y*canvas.width+x)*4+3])outside++;
}
const chinese=document.title;document.querySelector('[data-language="en"]').click();
return {chinese,english:document.title,yaw:Number(canvas.dataset.cameraYaw),pitch:Number(canvas.dataset.cameraPitch),orbitSegments:Number(canvas.dataset.orbitSegments),gridSegments:Number(canvas.dataset.gridSegments),outside};
""",
    )
    _drag(base_url, session_id, "orbit-canvas", 95, 54)
    moved = _execute(
        base_url,
        session_id,
        """const c=document.getElementById("orbit-canvas");return {yaw:Number(c.dataset.cameraYaw),pitch:Number(c.dataset.cameraPitch)};""",
    )
    reset = _execute(
        base_url,
        session_id,
        """document.getElementById("orbit-reset").click();const c=document.getElementById("orbit-canvas");return {yaw:Number(c.dataset.cameraYaw),pitch:Number(c.dataset.cameraPitch)};""",
    )
    if (
        initial["chinese"] != "拓扑检查"
        or initial["english"] != "Topology Inspection"
        or initial["orbitSegments"] < 1
        or initial["gridSegments"] < 1
        or initial["outside"] < 10
        or moved == {"yaw": initial["yaw"], "pitch": initial["pitch"]}
        or abs(reset["yaw"] - initial["yaw"]) > 1e-9
        or abs(reset["pitch"] - initial["pitch"]) > 1e-9
    ):
        raise AssertionError(
            f"Topology visual regression: {initial=} {moved=} {reset=}"
        )
    return {"outside_orbit_pixels": initial["outside"]}


def _zone(base_url: str, session_id: str, page: Path) -> dict[str, object]:
    _navigate(base_url, session_id, page)
    initial = _execute(
        base_url,
        session_id,
        """
const canvas=document.getElementById("zone-globe"),map=document.getElementById("zone-map");
const chinese=document.title;document.querySelector('[data-language="en"]').click();
return {chinese,english:document.title,count:Number(document.getElementById("probe-visible").textContent),rows:document.querySelectorAll("#probe-table tr").length,mapBoundaries:document.querySelectorAll("#zone-map .zone-boundary").length,mapProbes:document.querySelectorAll("#zone-map .zone-probe").length,boundarySegments:Number(canvas.dataset.boundarySegments),yaw:Number(canvas.dataset.cameraYaw),pitch:Number(canvas.dataset.cameraPitch)};
""",
    )
    filtered = _execute(
        base_url,
        session_id,
        """
const zone=document.getElementById("zone-filter"),category=document.getElementById("category-filter"),status=document.getElementById("status-filter");
zone.value=zone.options[1].value;zone.dispatchEvent(new Event("change",{bubbles:true}));
const zoneCount=Number(document.getElementById("probe-visible").textContent);
category.value=[...category.options].find(value=>value.value==="representative-center")?.value||category.options[1].value;category.dispatchEvent(new Event("change",{bubbles:true}));
status.value="all";status.dispatchEvent(new Event("change",{bubbles:true}));
const categoryCount=Number(document.getElementById("probe-visible").textContent);
status.value="multi";status.dispatchEvent(new Event("change",{bubbles:true}));
return {zoneCount,categoryCount,multiCount:Number(document.getElementById("probe-visible").textContent),rows:document.querySelectorAll("#probe-table tr").length,mapProbes:document.querySelectorAll("#zone-map .zone-probe").length,mapBoundaries:document.querySelectorAll("#zone-map .zone-boundary").length,canvasProbes:Number(document.getElementById("zone-globe").dataset.visibleProbes)};
""",
    )
    _drag(base_url, session_id, "zone-globe", -88, 47)
    moved = _execute(
        base_url,
        session_id,
        """const c=document.getElementById("zone-globe");return {yaw:Number(c.dataset.cameraYaw),pitch:Number(c.dataset.cameraPitch)};""",
    )
    if (
        initial["chinese"] != "Zone 检查"
        or initial["english"] != "Zone Inspection"
        or initial["boundarySegments"] < 1
        or initial["mapBoundaries"] < 1
        or initial["count"] != initial["rows"]
        or initial["count"] != initial["mapProbes"]
        or filtered["zoneCount"] >= initial["count"]
        or filtered["categoryCount"] >= filtered["zoneCount"]
        or filtered["multiCount"] != filtered["rows"]
        or filtered["multiCount"] != filtered["mapProbes"]
        or filtered["mapBoundaries"] < 1
        or moved == {"yaw": initial["yaw"], "pitch": initial["pitch"]}
    ):
        raise AssertionError(
            f"Zone visual regression: {initial=} {filtered=} {moved=}"
        )
    return {"initial_visible": initial["count"], "filtered": filtered}


def _traffic(base_url: str, session_id: str, page: Path) -> dict[str, object]:
    _navigate(base_url, session_id, page)
    identity = _execute(
        base_url,
        session_id,
        """
const canvas=document.getElementById("globe");
const chinese=document.title;document.querySelector('[data-language="en"]').click();
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
const chinese=document.title;document.querySelector('[data-language="en"]').click();
const parameter=document.getElementById("parameter-overview").textContent;
document.querySelector('[data-view="forwarding"]').click();
const route=document.getElementById("route-select");
const canonical=[...route.options].find(value=>value.textContent.includes("O1-S1")&&value.textContent.includes("GS-Marsala"));
route.value=canonical.value;route.dispatchEvent(new Event("change",{bubbles:true}));
const event=document.getElementById("route-event"),first=event.options[0].value;
event.value=first;event.dispatchEvent(new Event("change",{bubbles:true}));
const firstContext=document.getElementById("route-precedence").textContent;
const firstSelected=document.querySelector("[data-selected-precedence]").dataset.selectedPrecedence;
const remove=[...event.options].find(value=>value.textContent.includes("REMOVED"));
event.value=remove.value;event.dispatchEvent(new Event("change",{bubbles:true}));
const removeContext=document.getElementById("route-precedence").textContent;
const removeSelected=document.querySelector("[data-selected-precedence]").dataset.selectedPrecedence;
const zoneRoute=[...route.options].find(value=>value.textContent.includes("Z:"));
route.value=zoneRoute.value;route.dispatchEvent(new Event("change",{bubbles:true}));document.querySelector('[data-side="after"]').click();
const zoneDetail=document.getElementById("spatial-detail").textContent;
const groundRoute=[...route.options].find(value=>!value.textContent.includes("Z:"));
route.value=groundRoute.value;route.dispatchEvent(new Event("change",{bubbles:true}));
const groundDetail=document.getElementById("spatial-detail").textContent;
return {chinese,english:document.title,parameter,firstContext,removeContext,firstSelected,removeSelected,zoneDetail,groundDetail};
""",
    )
    if (
        result["chinese"] != "Planner 检查工具"
        or result["english"] != "Planner Inspection Tool"
        or not all(value in result["parameter"] for value in ("2530", "1590", "940", "Publication batching", "Parameter deadband"))
        or result["firstSelected"] == result["removeSelected"]
        or result["firstContext"] == result["removeContext"]
        or "BEFORE next hop" not in result["removeContext"]
        or "Semantic target" not in result["zoneDetail"]
        or "Current service/path-terminal satellite" not in result["zoneDetail"]
        or "Semantic target: GroundStation" not in result["groundDetail"]
        or "no Zone footprint is expected" not in result["groundDetail"]
    ):
        raise AssertionError(f"Planner presentation regression: {result}")
    return {"selected_add": result["firstSelected"], "selected_remove": result["removeSelected"]}


def run_visual_browser_gate(
    inspection_root: Path,
    *,
    geckodriver: str,
    timeout: float = 45.0,
) -> dict[str, object]:
    """Run the accepted manual-review interaction assertions in real Firefox."""

    root = Path(inspection_root).resolve()
    pages = {name: root / name / "index.html" for name in ("topology", "zone", "planner", "traffic")}
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
                value={"capabilities": {"alwaysMatch": {"browserName": "firefox", "moz:firefoxOptions": {"args": ["-headless"]}}}},
                timeout=timeout,
            )
            session_id = session["sessionId"]
            _request(
                f"{base_url}/session/{session_id}/window/rect",
                method="POST",
                value={"width": 1600, "height": 1200},
            )
            return {
                "status": "PASS",
                "browser": session["capabilities"]["browserName"],
                "browser_version": session["capabilities"]["browserVersion"],
                "topology": _topology(base_url, session_id, pages["topology"]),
                "zone": _zone(base_url, session_id, pages["zone"]),
                "planner": _planner(base_url, session_id, pages["planner"]),
                "traffic": _traffic(base_url, session_id, pages["traffic"]),
            }
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
    arguments = parser.parse_args()
    print(run_visual_browser_gate(arguments.inspection_root, geckodriver=arguments.geckodriver))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_visual_browser_gate"]
