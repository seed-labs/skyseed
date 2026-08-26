"""Render the offline System Observation inspection page."""

from __future__ import annotations

from html import escape
from typing import Any

from tools._html import json_for_script, standalone_page


def render_observation_page(data: dict[str, Any]) -> str:
    """Render summaries plus unified logical-scope interactive views."""

    inventory = data["inventory"]
    components = data["components"]
    component_cards = "".join(
        f"""<div class="card stat">
<span class="muted">{escape(str(row['component']))}</span>
<strong>{row['observations']}</strong>
<small>observations · {len(row['stages'])} stages</small>
</div>"""
        for row in components
    )
    top_level_rows = "".join(
        f"""<tr><td>{escape(str(row['component']))}</td>
<td>{escape(str(row['label']))}</td>
<td>{escape(str(row['duration_role']))}</td><td>{row['observations']}</td>
<td>{_optional_duration(row['bootstrap_run_seconds'])}</td>
<td>{_optional_duration(row['steady_p50_seconds'])}</td>
<td>{_optional_duration(row['steady_p95_seconds'])}</td>
<td>{_optional_duration(row['steady_max_seconds'])}</td>
<td>{_optional_duration(row['one_shot_p50_seconds'])}</td>
<td>{_optional_duration(row['one_shot_p95_seconds'])}</td>
<td>{_optional_duration(row['one_shot_max_seconds'])}</td>
<td>{_optional_duration(row['run_seconds'])}</td></tr>"""
        for row in data["top_level_timing_series"]
    )
    def summary_rows(role: str) -> str:
        return "".join(
            f"""<tr data-component="{escape(str(component['component']))}">
<td>{escape(str(component['component']))}</td>
<td>{escape(str(stage['label']))}</td>
<td>{escape(str(stage['duration_role']))}</td><td>{stage['observations']}</td>
<td>{_optional_duration(stage['bootstrap_run_seconds'])}</td>
<td>{_optional_duration(stage['steady_p50_seconds'])}</td>
<td>{_optional_duration(stage['steady_p95_seconds'])}</td>
<td>{_optional_duration(stage['steady_max_seconds'])}</td>
<td>{_optional_duration(stage['one_shot_p50_seconds'])}</td>
<td>{_optional_duration(stage['one_shot_p95_seconds'])}</td>
<td>{_optional_duration(stage['one_shot_max_seconds'])}</td>
<td>{_optional_duration(stage['run_seconds'])}</td></tr>"""
            for component in components
            for stage in component["stages"]
            if (
                (role == "independent")
                == (
                    component["component"] == "slicer"
                    and stage["label"] == "difference"
                )
                and (
                    stage["duration_role"] == role
                    if role != "independent"
                    else stage["duration_role"] == "stage"
                )
            )
        )

    leaf_stage_rows = summary_rows("stage")
    independent_stage_rows = summary_rows("independent")
    rollup_stage_rows = summary_rows("rollup")
    sample_stage_rows = summary_rows("sample")
    component_options = "".join(
        f'<option value="{escape(str(row["component"]))}">'
        f'{escape(str(row["component"]))}</option>'
        for row in components
    )
    work_options = "".join(
        f'<option value="{escape(name)}">{escape(name)}</option>'
        for name in data["stage_work_measurement_names"]
    )
    resource_notice = (
        ", ".join(data["resource_measurement_names"])
        if data["resource_measurement_names"]
        else "No resource measurements were recorded for this run."
    )
    body = f"""
<style>
.observation-toolbar {{ position:sticky;top:0;z-index:8;padding:10px;background:#07111ff2; }}
.mode-switch button[aria-pressed="true"] {{ border-color:var(--cyan);color:var(--cyan); }}
.observation-view[hidden] {{ display:none; }}
.timeline-chart {{ height:410px;min-height:360px; }}
.timeline-point {{ cursor:pointer; }}
.timeline-point:hover,.timeline-point.selected {{ stroke:#fff;stroke-width:2.5; }}
.timeline-line {{ fill:none;opacity:.45;stroke-width:1.3; }}
.timeline-grid {{ stroke:#294966;stroke-width:1; }}
.timeline-reference {{ stroke:var(--warn);stroke-dasharray:7 5;stroke-width:1.4; }}
.chart-legend {{ display:flex;flex-wrap:wrap;gap:16px;color:var(--muted);margin:8px 0; }}
.role-mark {{ display:inline-block;width:12px;height:12px;margin-right:5px;vertical-align:-1px; }}
.role-rollup {{ border:2px solid var(--cyan);border-radius:50%; }}
.role-stage {{ background:var(--blue);border-radius:50%; }}
.role-sample {{ background:var(--warn);transform:rotate(45deg); }}
.role-independent {{ border:2px dashed var(--warn);border-radius:3px; }}
.duration-role {{ display:inline-block;padding:2px 7px;border-radius:99px;font-size:12px;white-space:nowrap; }}
.duration-role.rollup {{ color:var(--cyan);border:1px solid var(--cyan); }}
.duration-role.stage {{ color:#07111f;background:var(--blue); }}
.duration-role.sample {{ color:#271d00;background:var(--warn); }}
.duration-role.independent {{ color:var(--warn);border:1px dashed var(--warn); }}
.duration-bar {{ display:inline-block;min-width:2px;height:8px;border-radius:5px;background:var(--cyan); }}
.scroll-table {{ max-height:430px;overflow:auto; }}
.numeric {{ font-variant-numeric:tabular-nums;text-align:right; }}
tr.selectable {{ cursor:pointer; }} tr.selectable:hover {{ background:#18334d; }}
.unit-summary {{ display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin:12px 0; }}
.unit-summary div {{ padding:10px;background:#0a192a;border-radius:8px;overflow-wrap:anywhere; }}
.decomposition {{ display:flex;width:100%;min-height:34px;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#07111f; }}
.decomposition span {{ min-width:2px;padding:7px 4px;overflow:hidden;white-space:nowrap;font-size:11px;color:#05101d;font-weight:750; }}
.decomposition-legend {{ display:flex;flex-wrap:wrap;gap:7px 14px;margin:8px 0; }}
.decomposition-legend button {{ border:0;padding:2px;background:transparent;text-align:left; }}
.stage-swatch {{ display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:5px;vertical-align:-1px; }}
.rollup-note {{ border-left:4px solid var(--cyan);padding:9px 12px;background:#12334b66; }}
.independent-note {{ border-left:4px dashed var(--warn);padding:9px 12px;background:#493b1938;margin-top:12px; }}
.annotation-panel {{ min-height:68px; }}
.unit-scrubber {{ overflow-x:auto; padding:8px 0 12px; }}
.unit-scrubber-track {{ position:relative; height:54px; min-width:100%; border-bottom:2px solid #355672; }}
.unit-scrubber-button {{ position:absolute; bottom:6px; transform:translateX(-50%); min-width:30px; padding:5px 6px; font-size:11px; border-radius:99px; }}
.unit-scrubber-button.bootstrap {{ border-color:var(--warn); }}
.unit-scrubber-button.selected {{ background:var(--cyan); color:#07111f; border-color:var(--cyan); }}
.heatmap-scroll {{ overflow:auto; max-height:520px; border:1px solid var(--line); border-radius:8px; }}
.heatmap-row {{ display:grid; grid-template-columns:190px repeat(var(--heatmap-columns),26px); min-width:max-content; }}
.heatmap-row.rollup-band {{ border-top:2px solid var(--cyan); }}
.heatmap-label,.heatmap-column,.heatmap-cell {{ min-height:25px; border-right:1px solid #16314a; border-bottom:1px solid #16314a; }}
.heatmap-label {{ position:sticky; left:0; z-index:3; padding:4px 7px; background:#0a192a; font-size:11px; }}
.heatmap-column {{ position:sticky; top:0; z-index:2; padding:4px 1px; background:#102a43; color:var(--muted); font-size:10px; cursor:pointer; }}
.heatmap-column.selected {{ color:var(--cyan); background:#193d59; }}
.heatmap-cell {{ padding:0; border-radius:0; cursor:pointer; }}
.heatmap-cell.missing {{ background:#0b1722; cursor:default; }}
.heatmap-cell.zero {{ background:#0b1722; box-shadow:inset 0 0 0 1px #7590a5; }}
.heatmap-cell.selected {{ box-shadow:inset 0 0 0 2px #fff; }}
@media(max-width:820px) {{ .timeline-chart {{ height:500px; }} }}
</style>
<section class="card"><span class="status OBSERVED">OBSERVED</span>
  <h2>Detached execution evidence</h2>
  <p>This non-authoritative Tool parses, aggregates, and visualizes records emitted by completed execution. It never runs Slicer, Planner, Compiler, or Runner work.</p>
  <h3>Current observation strategy</h3><p>Execution components emit stage duration, logical scope, work/cardinality, and optional resource samples to an in-memory observer. This detached Tool reads the persisted records only; it does not execute, replay, or validate domain computation.</p>
  <details><summary>How to read duration roles</summary><p>A rollup is an enclosing wall-time envelope and overlaps its children. A leaf is an exclusive measured stage inside that envelope. Independent work sits outside the primary rollup. Unattributed / gap is rollup duration minus measured exclusive leaves; it is overhead or currently uninstrumented time, not a new additive stage. A sample carries evidence such as RSS with zero duration.</p></details>
  <pre>{escape(str(data['metadata']))}</pre>
</section>
<section><h2>Cross-component overview</h2><div class="grid stats">
  <div class="card stat"><span class="muted">Records</span><strong>{inventory['observations']}</strong></div>
  <div class="card stat"><span class="muted">Bootstrap</span><strong>{inventory['bootstrap_observations']}</strong></div>
  <div class="card stat"><span class="muted">Steady</span><strong>{inventory['steady_observations']}</strong></div>
  <div class="card stat"><span class="muted">One-shot</span><strong>{inventory['one_shot_observations']}</strong></div>{component_cards}
</div></section>
<section class="card"><h2>Top-level timing series</h2>
  <p class="muted">Each row is an independent series. Slicer complete-slice and difference are separate top-level work; Planner total-synchronize is its own rollup. Rows are never added into a component or pipeline total.</p>
  <div class="scroll-table"><table><thead><tr><th>Component</th><th>Stage</th><th>Role</th><th>N</th><th>Bootstrap run</th><th>Steady p50</th><th>Steady p95</th><th>Steady max</th><th>One-shot p50</th><th>One-shot p95</th><th>One-shot max</th><th>Series run</th></tr></thead><tbody>{top_level_rows}</tbody></table></div>
</section>
<section class="card"><h2>Exclusive leaf stages</h2>
  <p class="muted">These measured stages form the attributable decomposition inside their component rollup. Bootstrap is separate from steady-state percentiles.</p>
  <div class="scroll-table"><table><thead><tr><th>Component</th><th>Stage</th><th>Role</th><th>N</th><th>Bootstrap run</th><th>Steady p50</th><th>Steady p95</th><th>Steady max</th><th>One-shot p50</th><th>One-shot p95</th><th>One-shot max</th><th>Series run</th></tr></thead><tbody>{leaf_stage_rows}</tbody></table></div>
</section>
<section class="card"><h2>Rollup envelopes</h2>
  <p class="muted">Rollups overlap their child stages. They are presented as enclosing wall-time measurements and are never added to the exclusive leaf table.</p>
  <div class="scroll-table"><table><thead><tr><th>Component</th><th>Rollup</th><th>Role</th><th>N</th><th>Bootstrap run</th><th>Steady p50</th><th>Steady p95</th><th>Steady max</th><th>One-shot p50</th><th>One-shot p95</th><th>One-shot max</th><th>Series run</th></tr></thead><tbody>{rollup_stage_rows}</tbody></table></div>
</section>
<section class="card"><h2>Independent work</h2>
  <p class="muted">Slicer Difference work occurs outside complete-slice. It is not a child of that rollup and is not included in its leaf subtotal.</p>
  <div class="scroll-table"><table><thead><tr><th>Component</th><th>Stage</th><th>Role</th><th>N</th><th>Bootstrap run</th><th>Steady p50</th><th>Steady p95</th><th>Steady max</th><th>One-shot p50</th><th>One-shot p95</th><th>One-shot max</th><th>Series run</th></tr></thead><tbody>{independent_stage_rows}</tbody></table></div>
</section>
<section class="card"><h2>Resource samples</h2>
  <p class="muted">Samples carry resource evidence and have zero duration. They do not contribute execution cost.</p>
  <div class="scroll-table"><table><thead><tr><th>Component</th><th>Sample</th><th>Role</th><th>N</th><th>Bootstrap run</th><th>Steady p50</th><th>Steady p95</th><th>Steady max</th><th>One-shot p50</th><th>One-shot p95</th><th>One-shot max</th><th>Series run</th></tr></thead><tbody>{sample_stage_rows}</tbody></table></div>
</section>
<section class="card"><h2>Unified run inspection</h2>
<div class="controls mode-switch" aria-label="Observation inspection mode">
  <button id="mode-progression" type="button" aria-pressed="true">Stage progression</button>
  <button id="mode-unit" type="button" aria-pressed="false">Per-Slice / Plan breakdown</button>
</div>
<div class="observation-view" id="progression-view">
  <p class="muted">One stage across the complete run, positioned by simulation scope. Slicer points use Slice ID/timestamp; Planner points use publication identity and publication coverage.</p>
  <div class="controls observation-toolbar">
    <label>Component <select id="component-filter"><option value="">All</option>{component_options}</select></label>
    <label>Duration lane <select id="role-filter"><option value="stage" selected>Exclusive leaf stages</option><option value="rollup">Rollup envelopes</option><option value="independent">Independent work</option><option value="sample">Resource samples</option><option value="">All roles</option></select></label>
    <label>Stage <select id="stage-filter"><option value="">All</option></select></label>
    <label>Phase <select id="phase-filter"><option value="">All</option><option>bootstrap</option><option>steady</option><option>idle</option><option>one-shot</option></select></label>
    <label><input id="fit-selection" type="checkbox"> Fit selection</label>
    <button id="clear-annotation" type="button">Clear point label</button>
  </div>
  <div class="chart-legend"><span><i class="role-mark role-rollup"></i>rollup envelope (not additive)</span><span><i class="role-mark role-stage"></i>leaf/stage</span><span><i class="role-mark role-sample"></i>zero-duration sample</span><span><i class="role-mark role-independent"></i>independent work</span></div>
  <svg id="timeline-chart" class="timeline-chart" viewBox="0 0 1200 410" role="img" aria-label="Observation duration by logical simulation scope"></svg>
  <p id="axis-note" class="muted"></p>
  <div class="scroll-table"><table><thead><tr><th>Unit</th><th>Simulation scope</th><th>Component / stage</th><th>Role</th><th>Phase</th><th>Duration</th><th>Evidence #</th></tr></thead><tbody id="timeline-rows"></tbody></table></div>
</div>
<div class="observation-view" id="unit-view" hidden>
  <p class="muted">Select one Slice to inspect all Slicer work for it, or one Plan/publication to inspect one complete Planner synchronization.</p>
  <div class="controls observation-toolbar"><label>Component <select id="unit-component"></select></label><label>Slice / Plan (secondary navigation) <select id="unit-selector"></select></label></div>
  <div class="unit-scrubber" aria-label="Slice or Plan visual selector"><div id="unit-scrubber-track" class="unit-scrubber-track"></div></div>
  <h3>Stage × Slice / Plan duration heatmap</h3>
  <p id="heatmap-note" class="muted"></p><p class="muted">Missing means no observation record; an outlined cell is an exact measured zero. The lower rollup band has its own scale and is never added to leaf cells.</p><div id="unit-heatmap" class="heatmap-scroll"></div>
  <div id="unit-summary" class="unit-summary"></div>
  <h3>Attributed time within the primary rollup</h3><div id="unit-decomposition"></div>
  <div class="rollup-note"><strong>Rollup envelopes</strong><p>Shown separately from the leaf decomposition. They are never summed with child stages.</p><div id="unit-rollups"></div></div>
  <div id="unit-independent" class="independent-note"></div>
  <h3>All observations in this unit</h3>
  <div class="scroll-table"><table><thead><tr><th>Stage</th><th>Hierarchy</th><th>Phase</th><th>Duration</th><th>Work/cardinality</th><th>Evidence #</th></tr></thead><tbody id="unit-records"></tbody></table></div>
  <h3>Work/cardinality values</h3>
  <div class="scroll-table"><table><thead><tr><th>Stage</th><th>Measurement</th><th>Value</th></tr></thead><tbody id="unit-work"></tbody></table></div>
</div></section>
<section class="grid two">
  <div class="card annotation-panel"><h2>Selected observation</h2><p id="selected-label" class="muted">Select a chart point or evidence row.</p><table><tbody id="selected-work"></tbody></table></div>
  <div class="card"><h2>Optional resources</h2><p class="muted">{escape(resource_notice)}</p><table><thead><tr><th>Unit</th><th>Measurement</th><th>Value</th></tr></thead><tbody id="resource-rows"></tbody></table></div>
</section>
<section class="card"><h2>Time versus work</h2>
  <p class="muted">Compare leaf-stage duration with a positive, actually recorded cardinality. Rollups and resource samples are excluded.</p>
  <div class="controls"><label>Work measurement <select id="work-metric"><option value="">Choose…</option>{work_options}</select></label></div>
  <div class="scroll-table"><table><thead><tr><th>Unit</th><th>Component / stage</th><th>Phase</th><th>Work</th><th>Duration</th><th>Seconds / unit</th></tr></thead><tbody id="time-work-rows"></tbody></table></div>
</section>
<section class="card"><h2>Machine evidence</h2><p><a href="execution-observations.jsonl">JSONL records</a> · <a href="observation-summary.json">aggregate summary</a>. The browser uses one external compact record script; the complete run is not embedded in this HTML.</p></section>
"""
    return standalone_page(
        title="Skyseed v2 System Observation",
        eyebrow="What work ran · how much · what it cost",
        body=body,
        script=_script(data),
        language_strings={
            "Skyseed v2 System Observation": "Skyseed v2 系统 Observation",
            "What work ran · how much · what it cost": "执行了什么 · 工作量 · 成本",
            "Detached execution evidence": "分离的执行 evidence",
            "This non-authoritative Tool parses, aggregates, and visualizes records emitted by completed execution. It never runs Slicer, Planner, Compiler, or Runner work.": "这个非权威 Tool 只解析、聚合和可视化已完成执行发出的记录；不会运行 Slicer、Planner、Compiler 或 Runner 工作。",
            "Current observation strategy": "当前 Observation 策略",
            "Execution components emit stage duration, logical scope, work/cardinality, and optional resource samples to an in-memory observer. This detached Tool reads the persisted records only; it does not execute, replay, or validate domain computation.": "执行组件把 stage duration、逻辑范围、工作量 / 基数以及可选资源样本发送到内存 observer。这个分离 Tool 只读取已持久化记录；不会执行、重放或验证领域计算。",
            "How to read duration roles": "如何阅读 duration role",
            "A rollup is an enclosing wall-time envelope and overlaps its children. A leaf is an exclusive measured stage inside that envelope. Independent work sits outside the primary rollup. Unattributed / gap is rollup duration minus measured exclusive leaves; it is overhead or currently uninstrumented time, not a new additive stage. A sample carries evidence such as RSS with zero duration.": "Rollup 是包围子 stage 并与其重叠的 wall-time 包络。Leaf 是包络内独占测量的 stage。Independent work 位于主 rollup 之外。Unattributed / gap 是 rollup duration 减去已测量的独占 leaf；它表示开销或尚未仪器化的时间，不是新的可加 stage。Sample 携带 RSS 等 evidence，duration 为零。",
            "Cross-component overview": "跨组件总览",
            "Top-level timing series": "顶层计时序列",
            "Exclusive leaf stages": "独占 leaf stage",
            "Rollup envelopes": "Rollup 包络",
            "Independent work": "独立工作",
            "Resource samples": "资源样本",
            "Unified run inspection": "统一运行检查",
            "Stage progression": "Stage 进展",
            "Per-Slice / Plan breakdown": "按 Slice / Plan 分解",
            "Each row is an independent series. Slicer complete-slice and difference are separate top-level work; Planner total-synchronize is its own rollup. Rows are never added into a component or pipeline total.": "每一行都是独立序列。Slicer complete-slice 与 difference 是分开的顶层工作；Planner total-synchronize 是自身的 rollup。绝不把这些行相加成组件或 pipeline 总成本。",
            "These measured stages form the attributable decomposition inside their component rollup. Bootstrap is separate from steady-state percentiles.": "这些已测 stage 构成组件 rollup 内可归因的分解；bootstrap 与 steady-state percentile 分开统计。",
            "Rollups overlap their child stages. They are presented as enclosing wall-time measurements and are never added to the exclusive leaf table.": "Rollup 与其子 stage 重叠，只表示包围它们的 wall-time；绝不与独占 leaf 表相加。",
            "Slicer Difference work occurs outside complete-slice. It is not a child of that rollup and is not included in its leaf subtotal.": "Slicer Difference 工作发生在 complete-slice 之外；它不是该 rollup 的子项，也不计入 leaf 小计。",
            "Samples carry resource evidence and have zero duration. They do not contribute execution cost.": "Sample 携带资源 evidence，duration 为零，不计入执行成本。",
            "One stage across the complete run, positioned by simulation scope. Slicer points use Slice ID/timestamp; Planner points use publication identity and publication coverage.": "沿完整运行查看一个 stage，并按仿真逻辑范围定位。Slicer 点使用 Slice ID / timestamp；Planner 点使用 publication identity 和 coverage。",
            "Select one Slice to inspect all Slicer work for it, or one Plan/publication to inspect one complete Planner synchronization.": "选择一个 Slice 查看其全部 Slicer 工作，或选择一个 Plan / publication 查看一次完整 Planner synchronization。",
            "Missing means no observation record; an outlined cell is an exact measured zero. The lower rollup band has its own scale and is never added to leaf cells.": "Missing 表示没有 observation record；描边单元格表示精确测得的零。下方 rollup 区带使用独立刻度，绝不与 leaf 单元格相加。",
            "Shown separately from the leaf decomposition. They are never summed with child stages.": "与 leaf 分解分开显示，绝不和子 stage 求和。",
            "Compare leaf-stage duration with a positive, actually recorded cardinality. Rollups and resource samples are excluded.": "比较 leaf-stage duration 与实际记录且大于零的基数；排除 rollup 和资源 sample。",
            "Stage × Slice / Plan duration heatmap": "Stage × Slice / Plan duration 热力图",
            "Attributed time within the primary rollup": "主 rollup 内的已归因时间",
            "All observations in this unit": "本工作单元的全部 observation",
            "Work/cardinality values": "工作量 / 基数值",
            "Selected observation": "已选 observation",
            "Optional resources": "可选资源",
            "Time versus work": "时间与工作量",
            "Machine evidence": "机器 evidence",
        },
    )


def _script(data: dict[str, Any]) -> str:
    summary = {
        "components": [row["component"] for row in data["components"]],
        "stage_work_measurement_names": data["stage_work_measurement_names"],
        "timeline": data["timeline"],
    }
    script = r'''
const OBSERVATION_SUMMARY=__OBSERVATION_SUMMARY__;
const state={records:[],filtered:[],selected:null,units:{},sliceTimes:new Map(),simulationMin:null,simulationMax:null,sharedDurationMax:0};
const HEATMAP_MAX_COLUMNS=64;
const COLORS={slicer:"#5ee5e5",planner:"#69a9ff",compiler:"#ffd166",runner:"#62d894"};
const esc=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const seconds=value=>Number(value).toLocaleString(undefined,{maximumSignificantDigits:5})+" s";
const timestamp=value=>value?String(value).replace("T"," ").replace("+00:00","Z"):"—";
const parseTime=value=>{const result=Date.parse(value);return Number.isFinite(result)?result:null;};
function unitKey(record){const scope=record.scope||{};if(record.component==="planner"&&Number.isInteger(scope.publication_index))return `planner:${scope.publication_index}`;if(record.component==="compiler"&&Number.isInteger(scope.compile_index))return `compiler:${scope.compile_index}`;const sliceId=Number.isInteger(scope.slice_id)?scope.slice_id:scope.current_slice_id;if(record.component==="slicer"&&Number.isInteger(sliceId))return `slicer:${sliceId}`;return null;}
function unitLabel(record){const key=unitKey(record);if(!key)return "—";const [component,index]=key.split(":");return component==="planner"?`P${index}`:component==="compiler"?`C${index} / R${index}`:`S${index}`;}
function isIndependent(record){return record.component==="slicer"&&record.stage==="difference";}
function hierarchy(record){if(isIndependent(record))return "independent";return record.duration_role||"stage";}
function roleLabel(record){const role=hierarchy(record);if(role==="rollup")return "rollup envelope";if(role==="sample")return "sample";if(role==="independent")return "independent stage";return "leaf/stage";}
function roleBadge(record){const role=hierarchy(record);return `<span class="duration-role ${role}">${esc(roleLabel(record))}</span>`;}
function scopeCoverage(record){
  const scope=record.scope||{};
  if(record.component==="planner"||record.component==="compiler"){
    const publication=Number.isInteger(scope.publication_start_slice_id)&&Number.isInteger(scope.publication_end_slice_id)?`publication (${scope.publication_start_slice_id},${scope.publication_end_slice_id}]`:null;
    const planning=Number.isInteger(scope.planning_start_slice_id)&&Number.isInteger(scope.planning_end_slice_id)?`planning (${scope.planning_start_slice_id},${scope.planning_end_slice_id}]`:null;
    const time=scope.start_timestamp!==undefined||scope.end_timestamp!==undefined?`${timestamp(scope.start_timestamp)} → ${timestamp(scope.end_timestamp)}`:null;
    return [publication,planning,time].filter(Boolean).join(" · ")||`processed through S${scope.processed_through_slice_id??"?"}`;
  }
  if(record.component==="slicer"){const current=scope.slice_id??scope.current_slice_id,previous=scope.previous_slice_id;return `${Number.isInteger(previous)?`Difference S${previous}→S${current} · `:""}${timestamp(scope.timestamp)}`;}
  return Object.keys(scope).length?JSON.stringify(scope):"—";
}
function keyWork(record,limit=4){const values=Object.entries(record.work||{});if(!values.length)return "no work counts";return values.slice(0,limit).map(([name,value])=>`${name}=${Number(value).toLocaleString()}`).join(" · ")+(values.length>limit?" · …":"");}
function buildLogicalContext(){
  for(const record of state.records){const scope=record.scope||{};if(record.component==="slicer"){const id=Number.isInteger(scope.slice_id)?scope.slice_id:scope.current_slice_id,time=parseTime(scope.timestamp);if(Number.isInteger(id)&&time!==null&&!state.sliceTimes.has(id))state.sliceTimes.set(id,time);}}
  const maps={};
  for(const record of state.records){const key=unitKey(record);if(!key)continue;const [component,indexText]=key.split(":"),index=Number(indexText);maps[component]=maps[component]||new Map();if(!maps[component].has(index))maps[component].set(index,{component,index,label:component==="planner"?`P${index}`:component==="compiler"?`C${index} / R${index}`:`S${index}`,records:[]});maps[component].get(index).records.push(record);}
  state.units=Object.fromEntries(Object.entries(maps).map(([component,map])=>[component,[...map.values()].sort((a,b)=>a.index-b.index)]));
  for(const units of Object.values(state.units))for(const unit of units){const primaryStage=unit.component==="planner"?"total-synchronize":unit.component==="compiler"?"total-compile":"complete-slice",anchor=unit.records.find(record=>record.stage===primaryStage)||unit.records[0];unit.anchor=anchor;unit.phase=anchor.phase;if(unit.component==="slicer")unit.position=state.sliceTimes.get(unit.index)??parseTime(anchor.scope.timestamp);else unit.position=state.sliceTimes.get(anchor.scope.publication_end_slice_id)??parseTime(anchor.scope.end_timestamp);unit.coverage=scopeCoverage(anchor);}
  const positions=Object.values(state.units).flat().map(unit=>unit.position).filter(Number.isFinite);state.simulationMin=positions.length?Math.min(...positions):null;state.simulationMax=positions.length?Math.max(...positions):null;
  state.sharedDurationMax=Math.max(0,...state.records.filter(record=>["slicer","planner","compiler"].includes(record.component)&&record.duration_role!=="sample").map(record=>Number(record.duration_seconds)));
}
function recordPosition(record){const key=unitKey(record);if(key){const [component,index]=key.split(":");return state.units[component]?.find(unit=>unit.index===Number(index))?.position??null;}for(const name of ["timestamp","end_timestamp","start_timestamp"]){const value=parseTime(record.scope?.[name]);if(value!==null)return value;}return null;}
function roleMatches(record,role){return !role||hierarchy(record)===role;}
function filteredRecords(){const component=document.getElementById("component-filter").value,role=document.getElementById("role-filter").value,stage=document.getElementById("stage-filter").value,phase=document.getElementById("phase-filter").value;return state.records.filter(record=>(!component||record.component===component)&&roleMatches(record,role)&&(!stage||record.stage===stage)&&(!phase||record.phase===phase));}
function updateStages(){const select=document.getElementById("stage-filter"),selected=select.value,component=document.getElementById("component-filter").value,role=document.getElementById("role-filter").value,stages=[...new Set(state.records.filter(record=>(!component||record.component===component)&&roleMatches(record,role)).map(record=>record.stage))].sort();select.innerHTML='<option value="">All</option>'+stages.map(stage=>`<option ${stage===selected?"selected":""} value="${esc(stage)}">${esc(stage)}</option>`).join("");if(selected&&!stages.includes(selected))select.value="";}
function niceStep(value){if(!(value>0))return 1;const power=10**Math.floor(Math.log10(value)),fraction=value/power;return (fraction<=1?1:fraction<=2?2:fraction<=5?5:10)*power;}
function durationAxis(maximum){const step=niceStep(Math.max(maximum,1e-12)/5),max=Math.max(step,Math.ceil(maximum/step)*step);return {step,max,ticks:Array.from({length:Math.round(max/step)+1},(_,index)=>index*step)};}
function chooseTicks(values,limit=8){if(values.length<=limit)return values;return [...new Set(Array.from({length:limit},(_,index)=>values[Math.round(index*(values.length-1)/(limit-1))]))];}
function axisUnits(records){const components=[...new Set(records.map(record=>record.component))],units=components.flatMap(component=>state.units[component]||[]).filter(unit=>Number.isFinite(unit.position)),byPosition=new Map();for(const unit of units){if(!byPosition.has(unit.position))byPosition.set(unit.position,[]);byPosition.get(unit.position).push(unit.label);}return chooseTicks([...byPosition.entries()].sort((a,b)=>a[0]-b[0])).map(([position,labels])=>({position,label:labels.join(" · ")}));}
function renderAnnotation(record,x,y){const lines=[`${record.component} / ${record.stage} · ${roleLabel(record)}`,`${unitLabel(record)} · ${record.phase}`,scopeCoverage(record),`duration ${seconds(record.duration_seconds)}`,keyWork(record,3)],boxX=650,boxY=14,boxW=520,boxH=112;return `<g id="persistent-annotation"><line x1="${x}" y1="${y}" x2="${boxX}" y2="${boxY+boxH}" stroke="#edf6ff" opacity=".7"/><rect x="${boxX}" y="${boxY}" width="${boxW}" height="${boxH}" rx="8" fill="#102a43" stroke="#edf6ff"/><text x="${boxX+12}" y="${boxY+20}" fill="#edf6ff" font-size="12">${lines.map((line,index)=>`<tspan x="${boxX+12}" dy="${index?20:0}">${esc(line)}</tspan>`).join("")}</text></g>`;}
function pointMarkup(record,x,y,color){const title=esc(`${record.component} / ${record.stage} · ${unitLabel(record)} · ${seconds(record.duration_seconds)} · ${scopeCoverage(record)}`),selected=state.selected?.sequence===record.sequence?" selected":"";if(record.duration_role==="rollup")return `<circle class="timeline-point${selected}" data-sequence="${record.sequence}" cx="${x}" cy="${y}" r="6" fill="#07111f" stroke="${color}" stroke-width="2"><title>${title}</title></circle>`;if(record.duration_role==="sample")return `<rect class="timeline-point${selected}" data-sequence="${record.sequence}" x="${x-4}" y="${y-4}" width="8" height="8" transform="rotate(45 ${x} ${y})" fill="#ffd166"><title>${title}</title></rect>`;if(isIndependent(record))return `<rect class="timeline-point${selected}" data-sequence="${record.sequence}" x="${x-5}" y="${y-5}" width="10" height="10" rx="2" fill="#07111f" stroke="#ffd166" stroke-width="2" stroke-dasharray="3 2"><title>${title}</title></rect>`;return `<circle class="timeline-point${selected}" data-sequence="${record.sequence}" cx="${x}" cy="${y}" r="4.5" fill="${color}"><title>${title}</title></circle>`;}
function renderTimeline(){
  state.filtered=filteredRecords();const records=state.filtered,svg=document.getElementById("timeline-chart");
  if(!records.length){svg.innerHTML='<text x="48" y="70" fill="#a9bfd3">No matching records</text>';document.getElementById("timeline-rows").innerHTML="";return;}
  if(state.selected&&!records.some(record=>record.sequence===state.selected.sequence))clearSelection(false);
  const fit=document.getElementById("fit-selection").checked,visibleMax=Math.max(0,...records.map(record=>Number(record.duration_seconds))),axis=durationAxis(fit?visibleMax:state.sharedDurationMax||visibleMax),left=78,right=1172,top=28,bottom=330,width=right-left,height=bottom-top,min=state.simulationMin,max=state.simulationMax,fallbackUnits=[...new Set(records.map(unitKey).filter(Boolean))];
  const xFor=record=>{const value=recordPosition(record);if(Number.isFinite(value)&&Number.isFinite(min)&&Number.isFinite(max)&&max>min)return left+(value-min)/(max-min)*width;const key=unitKey(record),index=Math.max(0,fallbackUnits.indexOf(key));return left+(index/Math.max(1,fallbackUnits.length-1))*width;},yFor=value=>bottom-(Number(value)/axis.max)*height;
  const yGrid=axis.ticks.map(value=>{const y=yFor(value);return `<line class="timeline-grid" x1="${left}" y1="${y}" x2="${right}" y2="${y}"/><text x="${left-10}" y="${y+4}" fill="#a9bfd3" text-anchor="end" font-size="11">${esc(seconds(value))}</text>`;}).join("");
  const interval=Number(OBSERVATION_SUMMARY.timeline.slice_interval_seconds),reference=interval>0&&interval<=axis.max?`<line class="timeline-reference" x1="${left}" y1="${yFor(interval)}" x2="${right}" y2="${yFor(interval)}"/><text x="${right-4}" y="${yFor(interval)-6}" fill="#ffd166" text-anchor="end" font-size="11">formal Slice interval · ${esc(seconds(interval))}</text>`:"";
  const xTicks=axisUnits(records).map(value=>{const x=Number.isFinite(min)&&Number.isFinite(max)&&max>min?left+(value.position-min)/(max-min)*width:left,offset=Number.isFinite(min)?`+${seconds((value.position-min)/1000)}`:"";return `<line class="timeline-grid" x1="${x}" y1="${bottom}" x2="${x}" y2="${bottom+5}"/><text x="${x}" y="${bottom+18}" fill="#a9bfd3" text-anchor="middle" font-size="10"><tspan x="${x}">${esc(value.label)}</tspan><tspan x="${x}" dy="13">${esc(offset)}</tspan></text>`;}).join("");
  const series=new Map();for(const record of records){if(record.duration_role==="sample")continue;const key=`${record.component}\u0000${record.stage}`;if(!series.has(key))series.set(key,[]);series.get(key).push(record);}
  const lines=[...series.values()].map(values=>values.sort((a,b)=>(recordPosition(a)??0)-(recordPosition(b)??0)||a.sequence-b.sequence)).filter(values=>values.length>1).map(values=>`<polyline class="timeline-line" stroke="${COLORS[values[0].component]||"#d99cff"}" points="${values.map(record=>`${xFor(record)},${yFor(record.duration_seconds)}`).join(" ")}"/>`).join("");
  const points=records.map(record=>pointMarkup(record,xFor(record),yFor(record.duration_seconds),COLORS[record.component]||"#d99cff")).join(""),annotation=state.selected&&records.some(record=>record.sequence===state.selected.sequence)?renderAnnotation(state.selected,xFor(state.selected),yFor(state.selected.duration_seconds)):"";
  svg.innerHTML=`${yGrid}<line x1="${left}" y1="${bottom}" x2="${right}" y2="${bottom}" stroke="#6b879e"/><line x1="${left}" y1="${top}" x2="${left}" y2="${bottom}" stroke="#6b879e"/>${reference}${lines}${points}${xTicks}${annotation}<text x="${(left+right)/2}" y="398" fill="#a9bfd3" text-anchor="middle">logical simulation scope →</text>`;
  svg.querySelectorAll("[data-sequence]").forEach(element=>element.addEventListener("click",event=>{event.stopPropagation();selectRecord(Number(element.dataset.sequence));}));svg.onclick=event=>{if(event.target===svg)clearSelection();};
  const relativeMax=Math.max(visibleMax,1e-15);document.getElementById("timeline-rows").innerHTML=records.map(record=>`<tr class="selectable" data-record-sequence="${record.sequence}"><td>${esc(unitLabel(record))}</td><td>${esc(scopeCoverage(record))}</td><td>${esc(record.component)} / ${esc(record.stage)}</td><td>${roleBadge(record)}</td><td>${esc(record.phase)}</td><td class="numeric">${seconds(record.duration_seconds)} <i class="duration-bar" style="width:${Math.max(2,Math.round(Number(record.duration_seconds)/relativeMax*90))}px"></i></td><td>${record.sequence}</td></tr>`).join("");
  document.querySelectorAll("[data-record-sequence]").forEach(element=>element.addEventListener("click",()=>selectRecord(Number(element.dataset.recordSequence))));document.getElementById("axis-note").textContent=`X-axis: ${OBSERVATION_SUMMARY.timeline.x_axis_semantics}. Y-axis: ${fit?"fit to current selection":"shared Slicer/Planner duration scale"}.`;renderTimeWork();
}
function selectRecord(sequence){const record=state.records.find(value=>value.sequence===sequence);if(!record)return;if(state.selected?.sequence===sequence){clearSelection();return;}state.selected=record;document.getElementById("selected-label").textContent=`#${record.sequence} · ${record.component} / ${record.stage} · ${unitLabel(record)} · ${record.phase} · ${seconds(record.duration_seconds)} · ${scopeCoverage(record)}`;const values=Object.entries(record.work||{});document.getElementById("selected-work").innerHTML=values.length?values.map(([name,value])=>`<tr><th>${esc(name)}</th><td class="numeric">${Number(value).toLocaleString()}</td></tr>`).join(""):'<tr><td class="muted">No work measurements</td></tr>';if(!document.getElementById("progression-view").hidden)renderTimeline();else {const key=unitKey(record);if(key){const [component,index]=key.split(":");document.getElementById("unit-component").value=component;renderUnitOptions(Number(index));renderUnit();}else renderHeatmap();}}
function clearSelection(render=true){state.selected=null;document.getElementById("selected-label").textContent="Select a chart point or evidence row.";document.getElementById("selected-work").innerHTML="";if(render&&!document.getElementById("progression-view").hidden)renderTimeline();}
function renderTimeWork(){const metric=document.getElementById("work-metric").value,records=state.filtered.filter(record=>metric&&record.duration_role==="stage"&&Number(record.work?.[metric])>0);document.getElementById("time-work-rows").innerHTML=records.map(record=>{const work=Number(record.work[metric]),cost=record.duration_seconds/work;return `<tr><td>${esc(unitLabel(record))}</td><td>${esc(record.component)} / ${esc(record.stage)}</td><td>${esc(record.phase)}</td><td class="numeric">${work.toLocaleString()}</td><td class="numeric">${seconds(record.duration_seconds)}</td><td class="numeric">${cost.toExponential(4)}</td></tr>`;}).join("");}
function setMode(mode){const progression=mode==="progression";document.getElementById("progression-view").hidden=!progression;document.getElementById("unit-view").hidden=progression;document.getElementById("mode-progression").setAttribute("aria-pressed",String(progression));document.getElementById("mode-unit").setAttribute("aria-pressed",String(!progression));if(progression)renderTimeline();else renderUnit();}
function initializeUnitSelectors(){const component=document.getElementById("unit-component"),names=["slicer","planner","compiler",...Object.keys(state.units).filter(value=>!["slicer","planner","compiler"].includes(value))].filter(value=>state.units[value]?.length);component.innerHTML=names.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join("");component.addEventListener("change",()=>{renderUnitOptions();renderUnit();});document.getElementById("unit-selector").addEventListener("change",renderUnit);renderUnitOptions();}
function renderUnitOptions(preferred=null){const component=document.getElementById("unit-component").value,select=document.getElementById("unit-selector"),units=state.units[component]||[],previous=preferred??Number(select.value);select.innerHTML=units.map(unit=>`<option value="${unit.index}">${esc(unit.label)} · ${esc(unit.coverage)}</option>`).join("");if(units.some(unit=>unit.index===previous))select.value=String(previous);}
function stageHue(stage){let hash=0;for(const character of String(stage))hash=(hash*31+character.charCodeAt(0))>>>0;return hash%360;}
function stageColor(stage,intensity=1){const ratio=Math.max(0,Math.min(1,Number(intensity)||0)),light=ratio===0?12:22+Math.sqrt(ratio)*43;return `hsl(${stageHue(stage)} 72% ${light}%)`;}
function selectUnit(component,index){document.getElementById("unit-component").value=component;renderUnitOptions(index);document.getElementById("unit-selector").value=String(index);renderUnit();}
function renderUnitScrubber(component,selectedIndex){const units=state.units[component]||[],track=document.getElementById("unit-scrubber-track"),width=Math.max(720,units.length*34),positions=units.map(unit=>unit.position).filter(Number.isFinite),minimum=positions.length?Math.min(...positions):0,maximum=positions.length?Math.max(...positions):Math.max(1,units.length-1);track.style.width=`${width}px`;track.innerHTML=units.map((unit,position)=>{const fraction=Number.isFinite(unit.position)&&maximum>minimum?(unit.position-minimum)/(maximum-minimum):position/Math.max(1,units.length-1);return `<button type="button" class="unit-scrubber-button ${unit.phase} ${unit.index===selectedIndex?"selected":""}" data-scrub-index="${unit.index}" style="left:${18+fraction*(width-36)}px" title="${esc(unit.label)} · ${esc(unit.phase)} · ${esc(unit.coverage)}">${esc(unit.label)}</button>`;}).join("");track.querySelectorAll("[data-scrub-index]").forEach(button=>button.addEventListener("click",()=>selectUnit(component,Number(button.dataset.scrubIndex))));track.querySelector(".selected")?.scrollIntoView({behavior:"auto",block:"nearest",inline:"center"});}
function heatColor(stage,value,maximum){if(!(value>0)||!(maximum>0))return "#0b1722";return stageColor(stage,Math.min(1,value/maximum));}
function renderHeatmap(){const component=document.getElementById("unit-component").value,selectedIndex=Number(document.getElementById("unit-selector").value),allUnits=state.units[component]||[],selectedPosition=Math.max(0,allUnits.findIndex(unit=>unit.index===selectedIndex)),start=Math.max(0,Math.min(Math.max(0,allUnits.length-HEATMAP_MAX_COLUMNS),selectedPosition-Math.floor(HEATMAP_MAX_COLUMNS/2))),units=allUnits.slice(start,start+HEATMAP_MAX_COLUMNS),records=allUnits.flatMap(unit=>unit.records),leafStages=[...new Set(records.filter(record=>record.duration_role==="stage"&&!isIndependent(record)).map(record=>record.stage))].sort(),rollupStages=[...new Set(records.filter(record=>record.duration_role==="rollup").map(record=>record.stage))].sort(),leafMaximum=Math.max(0,...records.filter(record=>record.duration_role==="stage"&&!isIndependent(record)).map(record=>Number(record.duration_seconds))),rollupMaximum=Math.max(0,...records.filter(record=>record.duration_role==="rollup").map(record=>Number(record.duration_seconds))),columns=units.length;
  const header=`<div class="heatmap-row" style="--heatmap-columns:${columns}"><span class="heatmap-label">exclusive leaf stage</span>${units.map(unit=>`<button class="heatmap-column ${unit.index===selectedIndex?"selected":""}" data-heatmap-unit="${unit.index}" title="${esc(unit.coverage)}">${esc(unit.label)}</button>`).join("")}</div>`;
  const rows=(stages,rollup)=>stages.map(stage=>`<div class="heatmap-row ${rollup?"rollup-band":""}" style="--heatmap-columns:${columns}"><span class="heatmap-label"><i class="stage-swatch" style="background:${stageColor(stage,1)}"></i>${esc(stage)}${rollup?" · rollup":""}</span>${units.map(unit=>{const record=unit.records.find(value=>value.stage===stage);if(!record)return '<span class="heatmap-cell missing" title="stage not executed"></span>';const duration=Number(record.duration_seconds),classes=`heatmap-cell ${duration===0?"zero":""} ${state.selected?.sequence===record.sequence?"selected":""}`;return `<button class="${classes}" data-heatmap-sequence="${record.sequence}" style="background:${heatColor(stage,duration,rollup?rollupMaximum:leafMaximum)}" title="${esc(unit.label)} · ${esc(stage)} · ${esc(seconds(duration))}"></button>`;}).join("")}</div>`).join("");
  document.getElementById("unit-heatmap").innerHTML=header+rows(leafStages,false)+rows(rollupStages,true);document.getElementById("heatmap-note").textContent=`Each stage keeps the same hue used in the selected-unit decomposition; lightness/intensity represents duration on the shared ${component} leaf scale. Empty dark cells mean not executed; outlined dark cells are recorded ≈0. Showing ${units.length} of ${allUnits.length} units${allUnits.length>units.length?" around the selected unit":""}; rollups occupy the separate lower band.`;document.querySelectorAll("[data-heatmap-unit]").forEach(button=>button.addEventListener("click",()=>selectUnit(component,Number(button.dataset.heatmapUnit))));document.querySelectorAll("[data-heatmap-sequence]").forEach(button=>button.addEventListener("click",()=>selectRecord(Number(button.dataset.heatmapSequence))));}
function renderUnit(){
  const component=document.getElementById("unit-component").value,index=Number(document.getElementById("unit-selector").value),unit=(state.units[component]||[]).find(value=>value.index===index);if(!unit)return;
  const records=[...unit.records].sort((a,b)=>a.sequence-b.sequence),rollups=records.filter(record=>record.duration_role==="rollup"),leaves=records.filter(record=>record.duration_role==="stage"&&!isIndependent(record)),independent=records.filter(isIndependent),leafTotal=leaves.reduce((sum,record)=>sum+Number(record.duration_seconds),0),anchor=unit.anchor,primaryStage=component==="planner"?"total-synchronize":component==="compiler"?"total-compile":"complete-slice",primary=rollups.find(record=>record===anchor)||rollups.find(record=>record.stage===primaryStage)||rollups[0],primaryDuration=primary?Number(primary.duration_seconds):null,gap=primaryDuration===null?null:primaryDuration-leafTotal,tolerance=primaryDuration===null?0:Math.max(1e-9,primaryDuration*1e-9),gapValid=gap!==null&&gap>=-tolerance;
  document.getElementById("unit-summary").innerHTML=[["Slice / Plan",unit.label],["Phase",unit.phase],["Simulation coverage",unit.coverage],["Primary rollup",primary?.stage||"—"],["Primary rollup duration",primary?seconds(primaryDuration):"—"],["Exclusive measured",seconds(leafTotal)],["Unattributed / gap",gapValid?seconds(Math.max(0,gap)):"not subtractable"],["Observations",records.length]].map(([label,value])=>`<div><span class="muted">${esc(label)}</span><br><strong>${esc(value)}</strong></div>`).join("");
  document.getElementById("unit-decomposition").innerHTML=leaves.length?`<div class="decomposition">${leaves.map(record=>{const share=leafTotal?Number(record.duration_seconds)/leafTotal*100:0,label=share>=9?esc(record.stage):"";return `<span data-decomposition-sequence="${record.sequence}" style="width:${share}%;background:${stageColor(record.stage,1)}" title="${esc(record.stage)} · ${seconds(record.duration_seconds)}">${label}</span>`;}).join("")}</div><div class="decomposition-legend">${leaves.map(record=>`<button type="button" data-decomposition-sequence="${record.sequence}" title="Select observation #${record.sequence}"><i class="stage-swatch" style="background:${stageColor(record.stage,1)}"></i>${esc(record.stage)} · ${esc(seconds(record.duration_seconds))}</button>`).join("")}</div><p class="muted">Primary rollup: ${primary?seconds(primaryDuration):"—"} · exclusive measured leaf subtotal: ${seconds(leafTotal)} · ${gapValid?`unattributed overhead: ${seconds(Math.max(0,gap))}`:"gap unavailable because the leaf subtotal exceeds the primary envelope"}. The subtotal excludes all nested/overlapping rollups${component==="slicer"?" and the independent Difference":""}. Short segments intentionally omit internal text; the adjacent legend remains exact.</p>`:'<p class="muted">No leaf-stage duration records for this unit.</p>';document.querySelectorAll("[data-decomposition-sequence]").forEach(element=>element.addEventListener("click",()=>selectRecord(Number(element.dataset.decompositionSequence))));
  document.getElementById("unit-rollups").innerHTML=rollups.length?`<table><tbody>${rollups.map(record=>`<tr><th>${esc(record.stage)}</th><td>${seconds(record.duration_seconds)}</td><td>${esc(keyWork(record))}</td></tr>`).join("")}</tbody></table>`:'<p class="muted">No rollup envelope recorded.</p>';
  const independentNode=document.getElementById("unit-independent");independentNode.hidden=!independent.length;independentNode.innerHTML=independent.length?`<strong>Independent work outside the primary rollup</strong>${independent.map(record=>`<p>${esc(record.stage)} · ${seconds(record.duration_seconds)} · ${esc(keyWork(record))}</p>`).join("")}`:"";
  document.getElementById("unit-records").innerHTML=records.map(record=>`<tr class="selectable" data-unit-sequence="${record.sequence}"><td>${esc(record.stage)}</td><td>${roleBadge(record)}</td><td>${esc(record.phase)}</td><td class="numeric">${seconds(record.duration_seconds)}</td><td>${esc(keyWork(record))}</td><td>${record.sequence}</td></tr>`).join("");document.querySelectorAll("[data-unit-sequence]").forEach(element=>element.addEventListener("click",()=>selectRecord(Number(element.dataset.unitSequence))));
  const work=records.flatMap(record=>Object.entries(record.work||{}).map(([name,value])=>({stage:record.stage,name,value})));document.getElementById("unit-work").innerHTML=work.length?work.map(value=>`<tr><td>${esc(value.stage)}</td><td>${esc(value.name)}</td><td class="numeric">${Number(value.value).toLocaleString()}</td></tr>`).join(""):'<tr><td colspan="3" class="muted">No work/cardinality values</td></tr>';
  renderUnitScrubber(component,index);renderHeatmap();
}
function initializeObservation(){
  const payload=window.__SKYSEED_EXECUTION_OBSERVATIONS__;if(!payload||!Array.isArray(payload.records))throw new Error("Observation record script is invalid");state.records=payload.records;buildLogicalContext();
  const resources=state.records.flatMap(record=>Object.entries(record.resources||{}).map(([name,value])=>({unit:unitLabel(record),name,value})));document.getElementById("resource-rows").innerHTML=resources.length?resources.map(item=>`<tr><td>${esc(item.unit)}</td><td>${esc(item.name)}</td><td class="numeric">${Number(item.value).toLocaleString()}</td></tr>`).join(""):'<tr><td colspan="3" class="muted">No resource measurements</td></tr>';
  for(const id of ["component-filter","role-filter","phase-filter"])document.getElementById(id).addEventListener("change",()=>{updateStages();renderTimeline();});document.getElementById("stage-filter").addEventListener("change",renderTimeline);document.getElementById("fit-selection").addEventListener("change",renderTimeline);document.getElementById("work-metric").addEventListener("change",renderTimeWork);document.getElementById("clear-annotation").addEventListener("click",()=>clearSelection());document.getElementById("mode-progression").addEventListener("click",()=>setMode("progression"));document.getElementById("mode-unit").addEventListener("click",()=>setMode("unit"));
  initializeUnitSelectors();updateStages();renderTimeline();if(state.filtered.length)selectRecord(state.filtered[0].sequence);
}
const recordScript=document.createElement("script");recordScript.src="observation-records.js";recordScript.onload=initializeObservation;recordScript.onerror=()=>{document.getElementById("timeline-rows").innerHTML='<tr><td colspan="7">Unable to load observation-records.js</td></tr>';};document.body.appendChild(recordScript);
'''
    return script.replace(
        "__OBSERVATION_SUMMARY__",
        json_for_script(summary),
        1,
    )


def _duration(value: object) -> str:
    return f"{float(value):.6g} s"


def _optional_duration(value: object) -> str:
    return "—" if value is None else _duration(value)


__all__ = ["render_observation_page"]
