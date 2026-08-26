"""Small standalone-HTML and artifact-writing helpers."""

from __future__ import annotations

from html import escape
import json
from pathlib import Path
from typing import Mapping


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
.language-switch {
  position: fixed; z-index: 50; top: 18px; right: max(16px, calc((100vw - 1440px) / 2));
  display: inline-flex; gap: 4px; padding: 4px; border: 1px solid var(--line);
  border-radius: 10px; background: #07111fe8; box-shadow: 0 8px 25px #0006;
}
.language-switch button { padding: 5px 9px; border: 0; background: transparent; }
.language-switch button[aria-pressed="true"] { color: #031a22; background: var(--cyan); }
@media (max-width: 820px) { .two { grid-template-columns: 1fr; } }
"""

_COMMON_LANGUAGE_STRINGS = {
    "Skyseed v2 repository-local offline inspection artifact": (
        "Skyseed v2 仓库本地离线检查产物"
    ),
    "Tool failure": "工具失败",
    "Current snapshot": "当前快照",
    "Raw JSON": "原始 JSON",
    "Previous": "上一个",
    "Next": "下一个",
    "Reset view": "重置视角",
    "None": "无",
}


def _language_script(strings: Mapping[str, str]) -> str:
    pairs = tuple((str(english), str(chinese)) for english, chinese in strings.items())
    return rf"""
const SKYSEED_LANGUAGE_PAIRS={json_for_script(pairs)};
const SKYSEED_TEXT_STATE=new WeakMap();
const SKYSEED_ATTR_STATE=new WeakMap();
const SKYSEED_SKIP_LANGUAGE=new Set(["SCRIPT","STYLE","PRE","CODE"]);
let SKYSEED_LANGUAGE="zh-CN";
const skyseedNormalized=value=>String(value).replace(/\s+/g," ").trim();
function skyseedLanguagePair(value){{
  const normalized=skyseedNormalized(value);
  return SKYSEED_LANGUAGE_PAIRS.find(pair=>skyseedNormalized(pair[0])===normalized||skyseedNormalized(pair[1])===normalized)||null;
}}
function skyseedRegisterText(node){{
  if(SKYSEED_TEXT_STATE.has(node)||!node.parentElement||SKYSEED_SKIP_LANGUAGE.has(node.parentElement.tagName))return;
  const raw=node.nodeValue??"",trimmed=raw.trim(),pair=skyseedLanguagePair(trimmed);
  if(!pair)return;
  const start=raw.indexOf(trimmed),end=raw.length-start-trimmed.length;
  SKYSEED_TEXT_STATE.set(node,{{en:pair[0],zh:pair[1],prefix:raw.slice(0,start),suffix:end?raw.slice(raw.length-end):""}});
}}
function skyseedRegisterAttributes(element){{
  if(SKYSEED_ATTR_STATE.has(element))return;
  const values={{}};
  for(const name of ["title","placeholder","aria-label"]){{
    if(!element.hasAttribute(name))continue;
    const pair=skyseedLanguagePair(element.getAttribute(name));
    if(pair)values[name]={{en:pair[0],zh:pair[1]}};
  }}
  SKYSEED_ATTR_STATE.set(element,values);
}}
function skyseedTranslateText(node){{
  skyseedRegisterText(node);
  const value=SKYSEED_TEXT_STATE.get(node);
  if(value)node.nodeValue=value.prefix+(SKYSEED_LANGUAGE==="zh-CN"?value.zh:value.en)+value.suffix;
}}
function skyseedTranslateRoot(root){{
  if(root.nodeType===Node.TEXT_NODE){{skyseedTranslateText(root);return;}}
  const elements=[];
  if(root.nodeType===Node.ELEMENT_NODE)elements.push(root);
  if(root.querySelectorAll)elements.push(...root.querySelectorAll("*"));
  for(const element of elements){{
    skyseedRegisterAttributes(element);
    const attrs=SKYSEED_ATTR_STATE.get(element)||{{}};
    for(const [name,value] of Object.entries(attrs))element.setAttribute(name,SKYSEED_LANGUAGE==="zh-CN"?value.zh:value.en);
  }}
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
  const nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);
  for(const node of nodes)skyseedTranslateText(node);
}}
function skyseedSetLanguage(language){{
  SKYSEED_LANGUAGE=language==="en"?"en":"zh-CN";
  document.documentElement.lang=SKYSEED_LANGUAGE;
  const titlePair=skyseedLanguagePair(document.title);
  if(titlePair)document.title=SKYSEED_LANGUAGE==="zh-CN"?titlePair[1]:titlePair[0];
  document.querySelectorAll(".language-switch button").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.language===SKYSEED_LANGUAGE)));
  skyseedTranslateRoot(document.body);
}}
document.querySelectorAll(".language-switch button").forEach(button=>button.addEventListener("click",()=>skyseedSetLanguage(button.dataset.language)));
const SKYSEED_LANGUAGE_OBSERVER=new MutationObserver(records=>{{
  for(const record of records)for(const node of record.addedNodes)skyseedTranslateRoot(node);
}});
SKYSEED_LANGUAGE_OBSERVER.observe(document.body,{{childList:true,subtree:true}});
skyseedSetLanguage("zh-CN");
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
    language_strings: Mapping[str, str] | None = None,
) -> str:
    """Return one dependency-free, directly openable HTML page."""

    language_switch = ""
    language_script = ""
    if language_strings:
        merged_language_strings = dict(_COMMON_LANGUAGE_STRINGS)
        merged_language_strings.update(language_strings)
        language_switch = """<nav class="language-switch" aria-label="界面语言 / Interface language">
<button type="button" data-language="zh-CN" aria-pressed="true">中文</button>
<button type="button" data-language="en" aria-pressed="false">EN</button>
</nav>"""
        language_script = _language_script(merged_language_strings)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>{_BASE_CSS}</style>
</head>
<body>
{language_switch}
<header><div class="eyebrow">{escape(eyebrow)}</div><h1>{escape(title)}</h1></header>
<main>{body}</main>
<footer>Skyseed v2 repository-local offline inspection artifact</footer>
<script>{script}\n{language_script}</script>
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
            language_strings={
                f"{tool_name} — FAILED": f"{tool_name} — 失败",
                f"{tool_name} generation failed": f"{tool_name} 生成失败",
                "This run produced no successful result and used neither a stale artifact nor substitute computation.": "本次运行没有产生成功结果，也没有使用旧 artifact 或替代计算。",
            },
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
    """Write the available-tool landing page without introducing a framework."""

    overall = (
        "PASS"
        if tool_results
        and all(result.get("status") == "PASS" for result in tool_results.values())
        else "FAIL"
    )
    cards = []
    for key, title, description in (
        (
            "topology",
            "Topology Tool",
            "Inspect selectable static orbit planes, GroundStations, and consistency.",
        ),
        (
            "zone",
            "Zone Tool",
            "Inspect and select static Zone boundaries and geometry.",
        ),
        (
            "slice",
            "Slice / Difference Tool",
            "Inspect sampled formal state and Difference evolution by Slice.",
        ),
        (
            "observation",
            "System Observation Tool",
            "Inspect measured execution stages, rollups, work, and resources.",
        ),
        (
            "planner",
            "Planner Tool",
            "Compare sampled formal evidence with published planned state.",
        ),
        (
            "compiler",
            "BPF Compiler Tool",
            "Trace Plan deltas into interval operations and complete baselines.",
        ),
    ):
        if key not in ("topology", "zone") and key not in tool_results:
            continue
        result = tool_results.get(
            key, {"status": "FAILED", "message": "Not executed"}
        )
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
        '<div class="warning"><strong>Needs attention:</strong> '
        + escape("；".join(failures))
        + "</div>"
        if failures
        else '<p class="muted">All available Tools completed; each entry contains its exact evidence and audit links.</p>'
    )
    body = f"""
<section class="card">
  <span class="status {overall}">{overall}</span>
  <h2>Tool snapshot</h2>
  {issue}
  <p>Read from formal structure and sampled Slice evidence, through Planner publications and Compiler realizations, then use System Observation for measured execution cost. Every Tool is detached and read-only; a visual selection never creates a domain fact.</p>
</section>
<section><h2>Core object inventory</h2><div class="grid stats">{stats}</div></section>
<section><h2>Inspection entries</h2><div class="grid two">{''.join(cards)}</div></section>
"""
    write_text(
        directory / "index.html",
        standalone_page(
            title="Skyseed v2 Tool Overview",
            eyebrow="Current snapshot",
            body=body,
            language_strings={
                "Skyseed v2 Tool Overview": "Skyseed v2 工具总览",
                "Tool snapshot": "本次工具快照",
                "Core object inventory": "核心对象数量",
                "Inspection entries": "检查入口",
                "Topology Tool": "Topology 工具",
                "Zone Tool": "Zone 工具",
                "Slice / Difference Tool": "Slice / Difference 工具",
                "System Observation Tool": "系统 Observation 工具",
                "Planner Tool": "Planner 工具",
                "BPF Compiler Tool": "BPF Compiler 工具",
                "Inspect selectable static orbit planes, GroundStations, and consistency.": "查看可选择的静态轨道平面、地面站与一致性。",
                "Inspect and select static Zone boundaries and geometry.": "查看并直接选择静态 Zone 边界与几何信息。",
                "Inspect sampled formal state and Difference evolution by Slice.": "逐 Slice 检查采样的正式状态和 Difference 演化。",
                "Inspect measured execution stages, rollups, work, and resources.": "检查已测量的执行 stage、rollup、工作量与资源。",
                "Compare sampled formal evidence with published planned state.": "比较采样的正式 evidence 与已发布的 planned state。",
                "Trace Plan deltas into interval operations and complete baselines.": "追踪 Plan delta 如何转换为区间操作和完整 baseline。",
                "All available Tools completed; each entry contains its exact evidence and audit links.": "所有可用 Tool 均已完成；每个入口都包含精确 evidence 和审计链接。",
                "Read from formal structure and sampled Slice evidence, through Planner publications and Compiler realizations, then use System Observation for measured execution cost. Every Tool is detached and read-only; a visual selection never creates a domain fact.": "阅读顺序从正式结构与采样 Slice evidence 开始，再查看 Planner 发布和 Compiler realization，最后用 System Observation 检查已测量的执行成本。所有 Tool 均分离且只读；可视化选择不会创建领域事实。",
            },
        ),
    )
