#!/usr/bin/env python3
"""Smoke-test a generated BPF Compiler Tool with Firefox WebDriver."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile

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


def run_bpf_compiler_browser_smoke(
    page: Path,
    *,
    geckodriver: str,
    timeout: float = 30.0,
    screenshot_directory: Path | None = None,
) -> dict[str, object]:
    """Exercise the detached Plan/realization report in real Firefox."""

    resolved_page = page.resolve()
    if not resolved_page.is_file():
        raise FileNotFoundError(
            f"BPF Compiler inspection page not found: {resolved_page}"
        )
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
                value={"width": 1600, "height": 1400},
                timeout=timeout,
            )
            _request(
                f"{base_url}/session/{session_id}/url",
                method="POST",
                value={"url": resolved_page.as_uri()},
                timeout=timeout,
            )
            observed = _execute(
                base_url,
                session_id,
                """
const failures=[];
window.addEventListener("error",event=>failures.push(event.message));
const firstCard=document.querySelector("section.card")?.textContent||"";
const select=document.getElementById("realization"),boundary=document.getElementById("global-boundary");
if(select.options.length>1){select.value="1";select.dispatchEvent(new Event("change",{bubbles:true}));}
if(Number(boundary.max)>0){boundary.value=String(boundary.max);boundary.dispatchEvent(new Event("input",{bubbles:true}));}
return {
  title:document.title,
  firstCard,
  statCount:document.querySelectorAll(".stat").length,
  realizations:select.options.length,
  charts:document.querySelectorAll(".chart canvas").length,
  boundaries:Number(boundary.max)+1,
  timestamp:document.getElementById("compiler-timestamp").textContent,
  realizationDetail:document.getElementById("realization-detail").textContent.length,
  boundaryDetail:document.getElementById("boundary-workload").textContent.length,
  secondaryBoundary:!!document.getElementById("compiler-boundary"),
  synchronized:select.dataset.globalBoundary===document.getElementById("boundary-workload").dataset.globalBoundary,
  focusedCharts:[...document.querySelectorAll(".chart canvas")].every(value=>value.dataset.focusedRealization===select.value),
  retention:[...document.querySelectorAll("section.card")].at(-1)?.textContent||"",
  failures
};
""",
            )
            if (
                observed["title"]
                not in {"BPF Compiler Inspection", "BPF 编译器检查"}
                or "does not invoke Compiler" not in observed["firstCard"]
                or "bpf-compiler-inspection.json"
                not in observed["retention"]
                or observed["statCount"] < 1
                or observed["realizations"] < 1
                or observed["charts"] != 4
                or observed["boundaries"] < 1
                or not observed["timestamp"]
                or observed["realizationDetail"] < 20
                or observed["boundaryDetail"] < 20
                or observed["secondaryBoundary"]
                or not observed["synchronized"]
                or not observed["focusedCharts"]
                or observed["failures"]
            ):
                raise AssertionError(
                    f"BPF Compiler page smoke is invalid: {observed}"
                )
            screenshot = _capture_screenshot(
                base_url,
                session_id,
                screenshot_directory,
                "bpf-compiler-visual-overview.png",
            )
            return {
                "status": "PASS",
                "plans": observed["realizations"],
                "browser": session["capabilities"]["browserName"],
                "browser_version": session["capabilities"][
                    "browserVersion"
                ],
                "geckodriver_version": session["capabilities"][
                    "moz:geckodriverVersion"
                ],
                "screenshot": screenshot,
            }
        finally:
            if session_id is not None:
                try:
                    _request(
                        f"{base_url}/session/{session_id}",
                        method="DELETE",
                        timeout=timeout,
                    )
                except OSError:
                    pass
            _stop_driver(process, port)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page", type=Path)
    parser.add_argument("--geckodriver", default="geckodriver")
    parser.add_argument("--screenshot-directory", type=Path)
    arguments = parser.parse_args()
    print(
        run_bpf_compiler_browser_smoke(
            arguments.page,
            geckodriver=arguments.geckodriver,
            screenshot_directory=arguments.screenshot_directory,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_bpf_compiler_browser_smoke"]
