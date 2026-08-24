#!/usr/bin/env python3
"""Smoke-test a generated BPF Compiler Tool with Firefox WebDriver."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile

try:
    from scripts.check_slice_tool_browser import (
        _available_port,
        _capture_screenshot,
        _execute,
        _request,
        _stop_driver,
        _wait_for_driver,
    )
except ModuleNotFoundError:
    from check_slice_tool_browser import (  # type: ignore[no-redef]
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
    """Exercise interval selection and every semantic view in real Firefox."""

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
const chineseTitle=document.title;
document.querySelector('[data-language="en"]').click();
const englishTitle=document.title;
const initialActive=document.querySelector(".view.active").id;
const tabs=[...document.querySelectorAll(".tab")];
const viewCounts={};
for(const tab of tabs){
  tab.click();
  const view=document.getElementById(`view-${tab.dataset.view}`);
  viewCounts[tab.dataset.view]=view.textContent.trim().length;
}
const selector=document.getElementById("plan-select");
selector.value=String(selector.options.length-1);
selector.dispatchEvent(new Event("change",{bubbles:true}));
document.querySelector('[data-view="operations"]').click();
document.getElementById("plan-timeline").scrollIntoView({block:"start"});
return {
  chineseTitle,
  englishTitle,
  plans:selector.options.length,
  timelineButtons:document.querySelectorAll(".plan-button").length,
  selected:Number(selector.value),
  activePlanButtons:document.querySelectorAll(".plan-button.active").length,
  summaryCards:document.querySelectorAll("#interval-summary .summary-card").length,
  initialActive,
  operationLines:document.querySelectorAll("#backend-operations .operation-line").length,
  operationText:document.getElementById("backend-operations").textContent,
  loweringRows:document.querySelectorAll("#lowering-list .lowering-row").length,
  viewCounts,
  rawBytes:document.getElementById("raw-json").textContent.length,
  failures
};
""",
            )
            if (
                observed["chineseTitle"] != "BPF Compiler 检查工具"
                or observed["englishTitle"] != "BPF Compiler Inspection Tool"
                or observed["plans"] < 1
                or observed["timelineButtons"] != observed["plans"]
                or observed["selected"] != observed["plans"] - 1
                or observed["activePlanButtons"] != 1
                or observed["summaryCards"] != 5
                or observed["initialActive"] != "view-operations"
                or observed["operationLines"] < 1
                or "BpfCompiledRealization operation inventory"
                not in observed["operationText"]
                or "Backend ordering / dependency relationships"
                not in observed["operationText"]
                or observed["loweringRows"] < 1
                or any(
                    count < 1 for count in observed["viewCounts"].values()
                )
                or observed["rawBytes"] < 100
                or observed["failures"]
            ):
                raise AssertionError(
                    f"BPF Compiler page smoke is invalid: {observed}"
                )
            screenshot = _capture_screenshot(
                base_url,
                session_id,
                screenshot_directory,
                "bpf-compiler-backend-operations.png",
            )
            return {
                "status": "PASS",
                "plans": observed["plans"],
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
