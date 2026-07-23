"""Small standalone-HTML and artifact-writing helpers."""

from __future__ import annotations

from html import escape
import json
from pathlib import Path


_BASE_CSS = """
:root {
  color-scheme: dark;
  --bg: #07111f;
  --panel: #0e2035;
  --panel2: #132a43;
  --ink: #edf6ff;
  --muted: #a9bfd3;
  --line: #294966;
  --cyan: #5ee5e5;
  --blue: #69a9ff;
  --pass: #62d894;
  --fail: #ff7474;
  --warn: #ffd166;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: radial-gradient(circle at 80% -10%, #173f62 0, var(--bg) 38rem);
  color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
}
header, main, footer { width: min(1440px, calc(100% - 32px)); margin: auto; }
header { padding: 28px 0 12px; }
h1, h2, h3 { line-height: 1.2; margin: 0 0 12px; }
h1 { font-size: clamp(26px, 4vw, 42px); letter-spacing: -.02em; }
h2 { margin-top: 28px; font-size: 22px; }
a { color: var(--cyan); }
.eyebrow { color: var(--cyan); font-weight: 750; letter-spacing: .12em; text-transform: uppercase; }
.muted { color: var(--muted); }
.grid { display: grid; gap: 14px; }
.stats { grid-template-columns: repeat(auto-fit, minmax(135px, 1fr)); }
.two { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.card {
  background: color-mix(in srgb, var(--panel) 94%, transparent);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 16px;
  box-shadow: 0 12px 40px #0004;
}
.stat strong { display: block; font-size: 27px; }
.status {
  display: inline-flex; align-items: center; gap: 7px; padding: 4px 9px;
  border-radius: 99px; font-weight: 800; letter-spacing: .04em;
}
.status.PASS { color: #042514; background: var(--pass); }
.status.FAIL, .status.FAILED { color: #310707; background: var(--fail); }
.status.OBSERVED { color: #2f2300; background: var(--warn); }
.warning { border-left: 4px solid var(--warn); padding: 10px 14px; background: #493b1938; }
button, select, input {
  color: var(--ink); background: var(--panel2); border: 1px solid var(--line);
  border-radius: 8px; padding: 8px 10px;
}
button { cursor: pointer; }
button:hover { border-color: var(--cyan); }
pre, code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; color: #d7eaff; }
details { border-left: 1px solid var(--line); margin: 6px 0 6px 10px; padding-left: 12px; }
summary { cursor: pointer; }
canvas, svg {
  width: 100%; display: block; border-radius: 10px;
  background: linear-gradient(180deg, #06101f, #0c1d2c);
}
.controls { display: flex; flex-wrap: wrap; gap: 9px; align-items: center; margin: 10px 0; }
.entry {
  display: block; min-height: 145px; color: inherit; text-decoration: none;
  transition: transform .15s ease, border-color .15s ease;
}
.entry:hover { transform: translateY(-2px); border-color: var(--cyan); }
.detail { min-height: 110px; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; border-bottom: 1px solid var(--line); padding: 7px; }
footer { color: var(--muted); padding: 28px 0 42px; }
@media (max-width: 820px) { .two { grid-template-columns: 1fr; } }
"""


def json_for_script(value: object) -> str:
    """Serialize JSON safely inside a script element."""

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")


def standalone_page(
    *,
    title: str,
    eyebrow: str,
    body: str,
    script: str = "",
) -> str:
    """Return one dependency-free, directly openable HTML page."""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>{_BASE_CSS}</style>
</head>
<body>
<header><div class="eyebrow">{escape(eyebrow)}</div><h1>{escape(title)}</h1></header>
<main>{body}</main>
<footer>Skyseed v2 repository-local offline inspection artifact</footer>
<script>{script}</script>
</body>
</html>
"""


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_json(path: Path, value: object) -> None:
    write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def write_failed_tool(directory: Path, tool_name: str, error: BaseException) -> None:
    """Write a conspicuous current-run failure artifact."""

    message = f"{type(error).__name__}: {error}"
    body = f"""
<section class="card">
  <span class="status FAILED">FAILED</span>
  <h2>{escape(tool_name)} 生成失败</h2>
  <p>本次运行没有产生成功结果，也没有使用旧 artifact 或替代计算。</p>
  <pre>{escape(message)}</pre>
</section>
"""
    write_text(
        directory / "index.html",
        standalone_page(
            title=f"{tool_name} — FAILED",
            eyebrow="Tool failure",
            body=body,
        ),
    )
    write_text(
        directory / "summary.txt",
        f"status=FAILED\ntool={tool_name}\nerror={message}\n",
    )


def write_aggregate_index(
    directory: Path,
    tool_results: dict[str, dict[str, object]],
    counts: dict[str, int],
) -> None:
    """Write the two-tool landing page without introducing a framework."""

    overall = (
        "PASS"
        if tool_results
        and all(result.get("status") == "PASS" for result in tool_results.values())
        else "FAIL"
    )
    cards = []
    for key, title, description in (
        ("topology", "Topology Tool", "查看静态对象树、轨道平面、地面站与一致性。"),
        ("zone", "Zone Tool", "查看 Zone 边界、自动探针、physical matches 与正式 resolution。"),
    ):
        result = tool_results.get(key, {"status": "FAILED", "message": "未执行"})
        status = str(result.get("status", "FAILED"))
        message = escape(str(result.get("message", "")))
        cards.append(
            f"""<a class="card entry" href="{key}/index.html">
<span class="status {escape(status)}">{escape(status)}</span>
<h2>{title}</h2><p>{description}</p><p class="muted">{message}</p>
</a>"""
        )
    stats = "".join(
        f'<div class="card stat"><span class="muted">{escape(label)}</span>'
        f"<strong>{counts.get(key, 0)}</strong></div>"
        for key, label in (
            ("autonomous_systems", "AS"),
            ("shells", "Shell"),
            ("orbits", "Orbit"),
            ("satellites", "Satellite"),
            ("ground_stations", "GroundStation"),
            ("zones", "Zone"),
        )
    )
    failures = [
        f"{name}: {result.get('message', '')}"
        for name, result in tool_results.items()
        if result.get("status") != "PASS"
    ]
    issue = (
        '<div class="warning"><strong>需要注意：</strong> '
        + escape("；".join(failures))
        + "</div>"
        if failures
        else '<p class="muted">两个工具均完成，详情与完整结果位于各自页面。</p>'
    )
    body = f"""
<section class="card">
  <span class="status {overall}">{overall}</span>
  <h2>本次工具快照</h2>
  {issue}
</section>
<section><h2>核心对象数量</h2><div class="grid stats">{stats}</div></section>
<section><h2>检查入口</h2><div class="grid two">{''.join(cards)}</div></section>
"""
    write_text(
        directory / "index.html",
        standalone_page(
            title="Skyseed v2 Tooling 总览",
            eyebrow="Current snapshot",
            body=body,
        ),
    )
