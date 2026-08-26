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
    """Exercise lazy Plan selection and primary views in real Firefox."""

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
const chineseProse=document.querySelector("section.card")?.textContent||"";
document.querySelector('[data-language="en"]')?.click();
const englishTitle=document.title;
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
const selectedLast=Number(selector.value);
document.querySelector('[data-view="overview"]').click();
document.getElementById("plan-scrubber").scrollIntoView({block:"start"});
const bucketRows=[...document.querySelectorAll("#chart-input [data-bucket]")];
const bucketCount=bucketRows.length;
const lastBucket=bucketRows.at(-1);
lastBucket?.click();
const bucketPlans=[...document.querySelectorAll("[data-bucket-plan]")];
const drillPlan=Number(bucketPlans[0]?.dataset.bucketPlan);
bucketPlans[0]?.click();
return {
  chineseTitle,
  chineseProse,
  englishTitle,
  plans:selector.options.length,
  timelineButtons:document.querySelectorAll(".plan-button").length,
  selected:Number(selector.value),
  selectedLast,
  activePlanButtons:document.querySelectorAll(".plan-button.active").length,
  transformMetrics:document.querySelectorAll("#selected-transform .metric").length,
  diagnosticCharts:document.querySelectorAll(".diagnostic-chart").length,
  chartRows:document.querySelectorAll(".diagnostic-chart .chart-row").length,
  bucketCount,
  maximumBucketCount:Math.max(...["chart-input","chart-baseline","chart-churn"].map(id=>document.querySelectorAll(`#${id} [data-bucket]`).length)),
  bucketPlanCount:bucketPlans.length,
  drillPlan,
  selectedAfterDrill:Number(selector.value),
  manifestPlans:window.__SKYSEED_BPF_COMPILER_MANIFEST__?.plans?.length??0,
  initialPayloadEmbedded:document.documentElement.innerHTML.includes('"same_next_hop_changed":true'),
  viewCounts,
  failures
};
""",
            )
            if (
                observed["chineseTitle"] != "Skyseed v2 BPF Compiler 检查"
                or "Compiler 是一次性确定性转换器" not in observed["chineseProse"]
                or "完整不可变 realization baseline" not in observed["chineseProse"]
                or observed["englishTitle"]
                != "Skyseed v2 BPF Compiler Inspection"
                or observed["plans"] < 1
                or observed["timelineButtons"] != observed["plans"]
                or observed["selectedLast"] != observed["plans"] - 1
                or observed["activePlanButtons"] != 1
                or observed["transformMetrics"] < 10
                or observed["diagnosticCharts"] != 4
                or observed["bucketCount"] < 1
                or observed["maximumBucketCount"] > 12
                or (
                    observed["plans"] > 12
                    and observed["bucketCount"] >= observed["plans"]
                )
                or observed["bucketPlanCount"] < 1
                or observed["selectedAfterDrill"] != observed["drillPlan"]
                or observed["manifestPlans"] != observed["plans"]
                or observed["initialPayloadEmbedded"]
                or any(
                    count < 1 for count in observed["viewCounts"].values()
                )
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
