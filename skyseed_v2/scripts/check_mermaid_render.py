#!/usr/bin/env python3
"""Render every in-scope Mermaid block to SVG in headless Firefox."""

from __future__ import annotations

import argparse
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import subprocess
import tempfile
import time

from check_documentation import (
    MERMAID_BLOCK,
    OWNER_READMES,
    PROJECT_ROOT,
    _current_documents,
    _full_repository_documents,
)
from _webdriver import (
    _available_port,
    _execute,
    _request,
    _stop_driver,
    _wait_for_driver,
)

PINNED_MERMAID_VERSION = "10.9.1"
PINNED_MERMAID_SHA256 = (
    "61b335a46df05a7ce1c98378f60e5f3e77a7fb608a1056997e8a649304a936d6"
)


def _render(
    *,
    documents: tuple[Path, ...],
    mermaid_javascript: Path,
    geckodriver: Path,
    timeout: float,
) -> tuple[int, str, str]:
    script = mermaid_javascript.read_text(encoding="utf-8").replace(
        "</script", "<\\/script"
    )
    diagrams = [
        (path.relative_to(PROJECT_ROOT).as_posix(), index, block)
        for path in documents
        for index, block in enumerate(
            MERMAID_BLOCK.findall(path.read_text(encoding="utf-8")),
            start=1,
        )
    ]
    if not diagrams:
        raise AssertionError("no Mermaid blocks found")
    containers = "\n".join(
        f'<section data-source="{escape(path)}:{index}">'
        f'<div id="diagram-{ordinal:03d}"></div></section>'
        for ordinal, (path, index, _) in enumerate(diagrams)
    )
    sources = json.dumps(
        [block for _, _, block in diagrams],
        ensure_ascii=False,
    ).replace("</", "<\\/")
    labels = json.dumps(
        [f"{path}:{index}" for path, index, _ in diagrams],
        ensure_ascii=False,
    ).replace("</", "<\\/")
    page = f"""<!doctype html>
<meta charset="utf-8">
<title>Skyseed Mermaid render gate</title>
{containers}
<script>{script}</script>
<script>
window.__renderState={{done:false,error:null,count:0,version:null}};
(async()=>{{
  try{{
    mermaid.initialize({{startOnLoad:false,securityLevel:"strict"}});
    const sources={sources};
    const labels={labels};
    for(let index=0;index<sources.length;index++){{
      const target=document.getElementById(
        `diagram-${{String(index).padStart(3,"0")}}`
      );
      let result;
      try{{
        result=await mermaid.render(`skyseed-${{index}}`,sources[index]);
      }}catch(error){{
        window.__renderState={{
          done:true,
          error:`${{labels[index]}}: ${{String(error&&error.stack||error)}}`,
          count:document.querySelectorAll("svg").length,
          version:mermaid.version||"embedded"
        }};
        return;
      }}
      target.innerHTML=result.svg;
      if(typeof result.bindFunctions==="function") result.bindFunctions(target);
    }}
    window.__renderState={{
      done:true,error:null,count:document.querySelectorAll("svg").length,
      version:mermaid.version||"embedded"
    }};
  }}catch(error){{
    window.__renderState={{
      done:true,error:String(error&&error.stack||error),count:0,
      version:mermaid.version||"embedded"
    }};
  }}
}})();
</script>"""

    render_root = PROJECT_ROOT / "build"
    render_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="skyseed-mermaid-render-",
        dir=render_root,
    ) as root:
        page_path = Path(root) / "render.html"
        page_path.write_text(page, encoding="utf-8")
        port = _available_port()
        base_url = f"http://127.0.0.1:{port}"
        with tempfile.TemporaryFile() as driver_log:
            process = subprocess.Popen(
                (str(geckodriver), "--port", str(port)),
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
                if not isinstance(session, dict):
                    raise RuntimeError("WebDriver did not return a session")
                session_id = session["sessionId"]
                _request(
                    f"{base_url}/session/{session_id}/url",
                    method="POST",
                    value={"url": page_path.resolve().as_uri()},
                    timeout=timeout,
                )
                deadline = time.monotonic() + timeout
                state = None
                while time.monotonic() < deadline:
                    state = _execute(
                        base_url,
                        session_id,
                        "return window.__renderState||null;",
                    )
                    if isinstance(state, dict) and state.get("done"):
                        break
                    time.sleep(0.1)
                if not isinstance(state, dict) or not state.get("done"):
                    raise TimeoutError("Mermaid rendering did not finish")
                if state.get("error"):
                    raise AssertionError(
                        f"Mermaid visual render failed: {state['error']}"
                    )
                svg_evidence = _execute(
                    base_url,
                    session_id,
                    """
return [...document.querySelectorAll("svg")].map(svg=>({
  viewBox:svg.getAttribute("viewBox"),
  width:svg.getBoundingClientRect().width,
  height:svg.getBoundingClientRect().height,
  elements:svg.querySelectorAll("*").length
}));
""",
                )
                if (
                    state["count"] != len(diagrams)
                    or len(svg_evidence) != len(diagrams)
                    or any(
                        not value["viewBox"]
                        or value["width"] <= 0
                        or value["height"] <= 0
                        or value["elements"] == 0
                        for value in svg_evidence
                    )
                ):
                    raise AssertionError(
                        f"incomplete SVG render evidence: {svg_evidence}"
                    )
                capabilities = session["capabilities"]
                return (
                    len(diagrams),
                    str(capabilities["browserVersion"]),
                    str(state["version"]),
                )
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
    parser.add_argument("--scope", choices=("focused", "full"), required=True)
    parser.add_argument("--mermaid-js", type=Path, required=True)
    parser.add_argument("--mermaid-version", required=True)
    parser.add_argument(
        "--geckodriver",
        type=Path,
        default=Path("/snap/bin/geckodriver"),
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    arguments = parser.parse_args()
    javascript_hash = sha256(arguments.mermaid_js.read_bytes()).hexdigest()
    if (
        arguments.mermaid_version != PINNED_MERMAID_VERSION
        or javascript_hash != PINNED_MERMAID_SHA256
    ):
        raise AssertionError(
            "Mermaid renderer does not match the project pin: "
            f"version={arguments.mermaid_version!r}, sha256={javascript_hash}"
        )
    documents = (
        tuple(sorted({*_current_documents(), *OWNER_READMES}))
        if arguments.scope == "focused"
        else _full_repository_documents()
    )
    count, firefox_version, mermaid_version = _render(
        documents=documents,
        mermaid_javascript=arguments.mermaid_js.resolve(),
        geckodriver=arguments.geckodriver,
        timeout=arguments.timeout,
    )
    print(f"Mermaid documents: {len(documents)}")
    print(f"Mermaid diagrams rendered to SVG: {count}")
    print(f"Firefox version: {firefox_version}")
    print(f"Mermaid version label: {arguments.mermaid_version}")
    print(
        "Mermaid JavaScript SHA-256: "
        f"{javascript_hash}"
    )
    print(f"Mermaid runtime version property: {mermaid_version}")
    print("Mermaid visual render result: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
