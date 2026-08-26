"""Render the lazy, visual-first BPF Compiler inspection page."""

from __future__ import annotations

from html import escape
from typing import Any

from tools._html import standalone_page


def render_bpf_compiler_page(data: dict[str, Any]) -> str:
    """Return a compact bootstrap page; exact facts stay in lazy chunks."""

    if not isinstance(data, dict) or data.get("tool") != (
        "bpf-compiler-inspection"
    ):
        raise TypeError("data must be final BPF Compiler inspection view data")
    inventory = data["inventory"]
    body = f"""
<style>
.compiler-note {{ border-left:4px solid var(--cyan);padding:10px 13px;background:#12334b66; }}
.compiler-toolbar {{ position:sticky;top:0;z-index:8;padding:9px;background:#07111ff2; }}
.plan-scrubber {{ display:flex;gap:7px;overflow:auto;padding:8px 2px 13px; }}
.plan-button.active,.tab.active {{ border-color:var(--cyan);color:var(--cyan);background:#12334b; }}
.transform {{ display:grid;grid-template-columns:minmax(0,1fr) 48px minmax(0,1.2fr) 48px minmax(0,1fr);gap:8px;align-items:stretch; }}
.transform-arrow {{ align-self:center;text-align:center;font-size:28px;color:var(--cyan); }}
.metric-grid {{ display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:7px; }}
.metric {{ padding:9px;background:#0a192a;border-radius:8px; }} .metric strong {{ display:block;font-size:20px; }}
.chart-grid {{ display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px; }}
.diagnostic-chart {{ min-height:210px; }} .chart-row {{ display:grid;grid-template-columns:38px minmax(0,1fr) 92px;gap:7px;align-items:center;margin:5px 0; }}
.chart-row[data-bucket] {{ cursor:pointer;border:0;border-radius:5px;padding:3px;background:transparent;color:inherit;width:100%;text-align:left; }}
.chart-row[data-bucket]:hover,.chart-row[data-bucket].active {{ background:#17334a; }}
#chart-family .chart-row {{ grid-template-columns:112px minmax(0,1fr) 92px; }}
#chart-family .chart-row strong {{ font-size:12px;overflow-wrap:anywhere; }}
.bar-track {{ display:flex;height:19px;background:#081421;border:1px solid #1e3c55;border-radius:4px;overflow:hidden; }}
.bar-segment {{ min-width:1px; }} .bar-c {{ background:#5ee5e5; }} .bar-p {{ background:#ffd166; }} .bar-f {{ background:#69a9ff; }} .bar-x {{ background:#d99cff; }}
.bar-unchanged {{ background:#355672; }} .bar-changed {{ background:#ffd166; }} .bar-added {{ background:#62d894; }} .bar-removed {{ background:#ff7b8b; }}
.bar-resources {{ background:#5ee5e5; }} .bar-parameters {{ background:#ffd166; }} .bar-forwarding {{ background:#69a9ff; }} .bar-zone_edges {{ background:#62d894; }} .bar-zone_prefixes {{ background:#d99cff; }}
.legend {{ display:flex;flex-wrap:wrap;gap:7px 14px;color:var(--muted);font-size:12px; }} .legend i {{ display:inline-block;width:11px;height:11px;margin-right:4px; }}
.view[hidden] {{ display:none; }} .detail-layout {{ display:grid;grid-template-columns:minmax(0,1.5fr) minmax(280px,.8fr);gap:12px; }}
.evidence-list {{ max-height:680px;overflow:auto; }} .evidence-row {{ cursor:pointer;padding:9px;border-bottom:1px solid var(--line);overflow-wrap:anywhere; }} .evidence-row:hover {{ background:#18334d; }}
.exact-json {{ max-height:620px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere; }}
.pager {{ display:flex;gap:8px;align-items:center; }} .numeric {{ font-variant-numeric:tabular-nums; }}
.diff-kind {{ display:inline-block;padding:2px 7px;border-radius:99px;font-size:11px; }} .diff-kind.changed {{ background:#ffd166;color:#271d00; }} .diff-kind.added {{ background:#62d894;color:#041b12; }} .diff-kind.removed {{ background:#ff7b8b;color:#27070c; }}
.bucket-detail {{ margin-top:10px;padding:9px;border:1px solid var(--line);border-radius:8px; }} .bucket-detail button {{ margin:3px; }}
@media(max-width:900px) {{ .transform {{ grid-template-columns:1fr; }} .transform-arrow {{ transform:rotate(90deg); }} .chart-grid,.detail-layout {{ grid-template-columns:1fr; }} }}
</style>
<section class="card"><span class="status PASS">PASS</span>
  <h2>Compiled backend realization</h2>
  <p class="compiler-note"><strong>Detached, read-only evidence.</strong> This Tool receives already-produced Plan and BpfCompiledRealization values. It never invokes Compiler, Simulator, Planner, or runtime work.</p>
  <h3>Current translation strategy</h3><p>Compiler is a one-shot deterministic translator, not a Model or lifecycle owner: Plan delta → interval operations and Constraints → complete immutable realization baseline.</p>
  <p>{inventory['plans']} continuous Plans · {inventory['forwarding_intents']} interval forwarding intents · {inventory['baseline_records']} interval-end baseline records across the run.</p>
  <details><summary>How to read Compiler evidence</summary><p>A Plan delta is the logical change input. Compiler lowers it into interval operations and Constraints, then publishes a complete interval-end baseline. Adjacent churn compares two complete baselines; a large unchanged count is expected and is not repeated work. U/C/A/R mean unchanged, changed, added, and removed. A Zone-edge intent is backend service attachment state, not a fabricated Satellite-to-Zone network link; a Zone prefix is the backend packet prefix for that Zone.</p></details>
</section>
<section class="card compiler-toolbar">
  <div id="plan-scrubber" class="plan-scrubber" aria-label="Plan and realization selector"></div>
  <div class="controls"><label>Plan / realization <select id="plan-select"></select></label><span id="coverage-label" class="muted"></span></div>
  <div class="controls"><button class="tab active" data-view="overview">Visual overview</button><button class="tab" data-view="exact">Exact translation evidence</button><button class="tab" data-view="diff">Adjacent realization diff</button></div>
</section>
<main>
<div class="view" id="view-overview">
  <section><h2>What did these Plans turn into?</h2><div id="selected-transform" class="transform"></div></section>
  <section><h2>Run-wide diagnostic views</h2><p class="muted">Counts distinguish Plan delta input, interval operations/constraints, and the complete interval-end baseline. They are not presented as one-to-one amplification.</p>
    <div class="chart-grid">
      <div class="card diagnostic-chart"><h3>Plan input cardinality</h3><p class="muted">Contiguous Plan groups; C/P/F/Precedence values are summed within each group.</p><div id="chart-input"></div><div class="legend"><span><i class="bar-c"></i>Connection</span><span><i class="bar-p"></i>Parameter</span><span><i class="bar-f"></i>Forwarding</span><span><i class="bar-x"></i>precedence</span></div></div>
      <div class="card diagnostic-chart"><h3>Complete baseline size</h3><p class="muted">Per-realization median with min–max range; complete baselines are never summed.</p><div id="chart-baseline"></div></div>
      <div class="card diagnostic-chart"><h3>Adjacent baseline churn (U/C/A/R)</h3><p class="muted">Per-group medians and churn-ratio range across adjacent realization comparisons.</p><div id="chart-churn"></div><div class="legend"><span><i class="bar-unchanged"></i>unchanged</span><span><i class="bar-changed"></i>changed</span><span><i class="bar-added"></i>added</span><span><i class="bar-removed"></i>removed</span></div></div>
      <div class="card diagnostic-chart"><h3>Backend family bytes</h3><p class="muted">Whole-run serialized-byte composition by backend family.</p><div id="chart-family"></div></div>
    </div>
    <div id="bucket-detail" class="bucket-detail"></div>
  </section>
</div>
<div class="view" id="view-exact" hidden>
  <section class="card"><h2>Plan → realization drill-down</h2><p>Exact payload exists once in paged chunks. Plan transitions carry exact compiled intent references; compiled intents carry their exact origin transition. Constraint provenance is retained where it exists.</p>
    <div class="controls"><label>Evidence family <select id="detail-section"></select></label><label>Search current page <input id="detail-search" type="search" placeholder="canonical ID / key / operation"></label><span id="detail-count" class="muted"></span></div>
    <div class="pager"><button id="detail-prev">Previous page</button><span id="detail-page"></span><button id="detail-next">Next page</button></div>
  </section>
  <div class="detail-layout"><section class="card evidence-list" id="detail-list"></section><aside class="card"><h3>Selected exact evidence</h3><div id="reference-actions"></div><pre id="exact-json" class="exact-json">Select an entry.</pre></aside></div>
</div>
<div class="view" id="view-diff" hidden>
  <section class="card"><h2>Ri-1 → Ri exact baseline diff</h2><p>Equality uses deterministic backend-family keys and exact detached values. Unchanged records remain summarized; only changed/added/removed keys are paged. Exact before/after values are resolved lazily from their sole baseline payload.</p>
    <div class="controls"><label>Baseline family <select id="diff-family"><option>resources</option><option>parameters</option><option>forwarding</option><option>zone_edges</option><option>zone_prefixes</option></select></label><span id="diff-summary"></span></div>
    <div class="pager"><button id="diff-prev">Previous page</button><span id="diff-page"></span><button id="diff-next">Next page</button></div>
  </section>
  <div class="detail-layout"><section class="card evidence-list" id="diff-list"></section><aside class="card"><h3>Exact before / after</h3><pre id="diff-exact" class="exact-json">Select a changed key.</pre></aside></div>
</div>
</main>
<section class="card"><h2>Machine evidence and audit</h2><p><a href="manifest.json">bootstrap manifest</a> · <a href="inventory.json">complete file inventory</a> · <a href="checksums.sha256">checksums</a> · exact chunks under <code>chunks/</code>.</p><p class="muted">The HTML embeds no complete manifest or realization payload. Dynamic script loading preserves file:// compatibility.</p></section>
"""
    return standalone_page(
        title="Skyseed v2 BPF Compiler Inspection",
        eyebrow="Plan delta → interval operations → immutable baseline",
        body=body,
        script=_script(),
        language_strings={
            "Skyseed v2 BPF Compiler Inspection": "Skyseed v2 BPF Compiler 检查",
            "Plan delta → interval operations → immutable baseline": "Plan 增量 → 区间操作 → 不可变 baseline",
            "Compiled backend realization": "已编译的后端 realization",
            "Detached, read-only evidence.": "分离、只读的 evidence。",
            "Current translation strategy": "当前转换策略",
            "Compiler is a one-shot deterministic translator, not a Model or lifecycle owner: Plan delta → interval operations and Constraints → complete immutable realization baseline.": "Compiler 是一次性确定性转换器，不是 Model 或 lifecycle owner：Plan delta → 区间操作与 Constraint → 完整不可变 realization baseline。",
            "Plan and realization selector": "Plan 与 realization 选择器",
            "Plan / realization": "Plan / realization",
            "Visual overview": "可视化总览",
            "Exact translation evidence": "精确转换 evidence",
            "Adjacent realization diff": "相邻 realization 差异",
            "What did these Plans turn into?": "这些 Plan 被转换成了什么？",
            "Run-wide diagnostic views": "全运行诊断视图",
            "How to read Compiler evidence": "如何阅读 Compiler evidence",
            "A Plan delta is the logical change input. Compiler lowers it into interval operations and Constraints, then publishes a complete interval-end baseline. Adjacent churn compares two complete baselines; a large unchanged count is expected and is not repeated work. U/C/A/R mean unchanged, changed, added, and removed. A Zone-edge intent is backend service attachment state, not a fabricated Satellite-to-Zone network link; a Zone prefix is the backend packet prefix for that Zone.": "Plan delta 是逻辑变化输入。Compiler 将其降低为区间操作和 Constraint，随后发布完整的区间末 baseline。相邻 churn 比较两个完整 baseline；大量 unchanged 是预期结果，不表示重复工作。U/C/A/R 分别是 unchanged、changed、added 和 removed。Zone-edge intent 是后端服务附着状态，不是伪造的 Satellite-to-Zone 网络链路；Zone prefix 是该 Zone 的后端报文前缀。",
            "Plan input cardinality": "Plan 输入基数",
            "Complete baseline size": "完整 baseline 大小",
            "Adjacent baseline churn (U/C/A/R)": "相邻 baseline churn（U/C/A/R）",
            "Backend family bytes": "后端 family 字节数",
            "Counts distinguish Plan delta input, interval operations/constraints, and the complete interval-end baseline. They are not presented as one-to-one amplification.": "这些计数分别表示 Plan delta 输入、区间操作 / Constraint 与完整区间末 baseline；不能把它们误读为一一放大关系。",
            "Contiguous Plan groups; C/P/F/Precedence values are summed within each group.": "按连续 Plan 分组；每组内的 C/P/F/Precedence workload 求和。",
            "Per-realization median with min–max range; complete baselines are never summed.": "展示每个 realization baseline 的中位数与最小—最大范围；绝不把多个完整 baseline 相加。",
            "Per-group medians and churn-ratio range across adjacent realization comparisons.": "展示组内相邻 realization 比较的中位数与 churn ratio 范围。",
            "Whole-run serialized-byte composition by backend family.": "按后端 family 展示全运行序列化字节构成。",
            "Exact payload exists once in paged chunks. Plan transitions carry exact compiled intent references; compiled intents carry their exact origin transition. Constraint provenance is retained where it exists.": "精确 payload 只在分页 chunk 中保存一次。Plan transition 携带准确的 compiled intent 引用；compiled intent 保留其准确来源 transition；存在 Constraint provenance 时也会保留。",
            "Equality uses deterministic backend-family keys and exact detached values. Unchanged records remain summarized; only changed/added/removed keys are paged. Exact before/after values are resolved lazily from their sole baseline payload.": "相等性依据确定性的 backend-family key 和精确的分离值。Unchanged 记录只做汇总；仅对 changed / added / removed key 分页。精确 before / after 值从唯一 baseline payload 按需解析。",
            "Machine evidence and audit": "机器 evidence 与审计",
            "The HTML embeds no complete manifest or realization payload. Dynamic script loading preserves file:// compatibility.": "HTML 不内嵌完整 manifest 或 realization payload；动态脚本加载保持 file:// 兼容。",
            "Plan → realization drill-down": "Plan → realization 下钻",
            "Evidence family": "Evidence family",
            "Search current page": "搜索当前页",
            "Previous page": "上一页",
            "Next page": "下一页",
            "Selected exact evidence": "已选精确 evidence",
            "Select an entry.": "请选择一条记录。",
            "Ri-1 → Ri exact baseline diff": "Ri-1 → Ri 精确 baseline 差异",
            "Baseline family": "Baseline family",
            "Exact before / after": "精确之前 / 之后",
            "Select a changed key.": "请选择一个变化的 key。",
            "Machine evidence and audit": "机器 evidence 与审计",
            "bootstrap manifest": "bootstrap manifest",
            "complete file inventory": "完整文件 inventory",
            "checksums": "checksums",
        },
    )


def _script() -> str:
    return r'''
const state={manifest:null,planIndex:0,bucketIndex:0,buckets:[],view:"overview",detailSection:"plan-forwarding",detailPage:0,diffFamily:"forwarding",diffPage:0,loadToken:0};
const esc=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number=value=>Number(value??0).toLocaleString();
const bytes=value=>{const n=Number(value||0);if(n<1024)return `${n} B`;if(n<1024**2)return `${(n/1024).toFixed(1)} KiB`;return `${(n/1024**2).toFixed(2)} MiB`;};
const seconds=value=>value===null||value===undefined?"not recorded":`${Number(value).toFixed(4)} s`;
const current=()=>state.manifest.plans[state.planIndex];
const metric=(label,value)=>`<div class="metric"><span class="muted">${esc(label)}</span><strong>${esc(value)}</strong></div>`;
function setView(view){state.view=view;document.querySelectorAll(".tab").forEach(button=>button.classList.toggle("active",button.dataset.view===view));document.querySelectorAll(".view").forEach(node=>node.hidden=node.id!==`view-${view}`);if(view==="exact")renderDetail();if(view==="diff")renderDiff();}
function selectPlan(index){state.planIndex=Number(index);state.detailPage=0;state.diffPage=0;const bucket=state.buckets.find(value=>value.plans.some(plan=>plan.plan_index===state.planIndex));if(bucket){state.bucketIndex=bucket.bucket_index;renderBucket(bucket.bucket_index);}document.getElementById("plan-select").value=String(index);document.querySelectorAll(".plan-button").forEach(button=>button.classList.toggle("active",Number(button.dataset.plan)===state.planIndex));renderSelected();if(state.view==="exact")renderDetail();if(state.view==="diff")renderDiff();}
function stackedRows(target,rows,segments,valueLabel){const maximum=Math.max(1,...rows.map(row=>segments.reduce((sum,segment)=>sum+Number(segment.value(row)||0),0)));document.getElementById(target).innerHTML=rows.map(row=>{const total=segments.reduce((sum,segment)=>sum+Number(segment.value(row)||0),0),rowLabel=row.display_label??`P${row.plan_index}`,rowTitle=row.canonical_label??rowLabel,bucket=row.bucket_index===undefined?"":` data-bucket="${row.bucket_index}"`;return `<button type="button" class="chart-row${row.bucket_index===state.bucketIndex?" active":""}"${bucket}><strong title="${esc(rowTitle)}">${esc(rowLabel)}</strong><div class="bar-track">${segments.map(segment=>`<span class="bar-segment ${typeof segment.className==="function"?segment.className(row):segment.className}" style="width:${Number(segment.value(row)||0)/maximum*100}%" title="${esc(segment.label)} ${number(segment.value(row))}"></span>`).join("")}</div><span class="numeric">${esc(valueLabel(row,total))}</span></button>`;}).join("");}
const median=values=>{const ordered=[...values].map(Number).sort((a,b)=>a-b),middle=Math.floor(ordered.length/2);return ordered.length%2?ordered[middle]:(ordered[middle-1]+ordered[middle])/2;};
const numericRange=values=>{const ordered=[...values].map(Number).sort((a,b)=>a-b);return {minimum:ordered[0]??0,median:median(ordered),maximum:ordered.at(-1)??0};};
function planBuckets(plans){const count=Math.min(12,Math.max(1,plans.length)),width=Math.ceil(plans.length/count),result=[];for(let start=0;start<plans.length;start+=width){const values=plans.slice(start,start+width),first=values[0].plan_index,last=values.at(-1).plan_index;result.push({bucket_index:result.length,display_label:first===last?`P${first}`:`P${first}–P${last}`,canonical_label:`Plans ${first} through ${last}`,plans:values});}return result;}
function selectBucket(index){state.bucketIndex=Number(index);renderCharts();renderBucket(state.bucketIndex);}
function renderBucket(index){const bucket=state.buckets[index]??state.buckets[0];if(!bucket)return;document.getElementById("bucket-detail").innerHTML=`<strong>${esc(bucket.display_label)} · exact Plans/realizations</strong><p class="muted">Grouped charts are discovery summaries only. Choose an exact Pn/Cn/Rn below.</p>${bucket.plans.map(plan=>`<button type="button" data-bucket-plan="${plan.plan_index}">P${plan.plan_index} / C${plan.plan_index} / R${plan.realization_index}</button>`).join("")}`;document.querySelectorAll("[data-bucket-plan]").forEach(button=>button.addEventListener("click",()=>selectPlan(button.dataset.bucketPlan)));}
function renderCharts(){const plans=state.manifest.plans,buckets=state.buckets.length?state.buckets:planBuckets(plans);state.buckets=buckets;
  const input=buckets.map(bucket=>({...bucket,summary:{input_connection:bucket.plans.reduce((sum,p)=>sum+p.summary.input_connection,0),input_parameter:bucket.plans.reduce((sum,p)=>sum+p.summary.input_parameter,0),input_forwarding:bucket.plans.reduce((sum,p)=>sum+p.summary.input_forwarding,0),input_precedence:bucket.plans.reduce((sum,p)=>sum+p.summary.input_precedence,0)}}));
  const baseline=buckets.map(bucket=>{const records=numericRange(bucket.plans.map(p=>p.summary.baseline_total)),serialized=numericRange(bucket.plans.map(p=>p.serialized_bytes.baseline_total));return {...bucket,summary:{baseline_total:records.median},baseline_range:records,serialized_range:serialized};});
  const churn=buckets.map(bucket=>{const fields={};for(const field of ["unchanged","changed","added","removed"])fields[field]=median(bucket.plans.map(p=>p.adjacent_diff_summary[field]));const ratio=numericRange(bucket.plans.map(p=>p.adjacent_diff_summary.churn_ratio));return {...bucket,adjacent_diff_summary:{...fields,churn_ratio:ratio.median},churn_range:ratio};});
  stackedRows("chart-input",input,[{label:"Connection sum",className:"bar-c",value:p=>p.summary.input_connection},{label:"Parameter sum",className:"bar-p",value:p=>p.summary.input_parameter},{label:"Forwarding sum",className:"bar-f",value:p=>p.summary.input_forwarding},{label:"precedence sum",className:"bar-x",value:p=>p.summary.input_precedence}],(p,total)=>`${number(total)} Σ`);
  stackedRows("chart-baseline",baseline,[{label:"median baseline records",className:"bar-f",value:p=>p.summary.baseline_total}],p=>`${number(p.baseline_range.median)} med · ${number(p.baseline_range.minimum)}–${number(p.baseline_range.maximum)}`);
  stackedRows("chart-churn",churn,[{label:"median unchanged",className:"bar-unchanged",value:p=>p.adjacent_diff_summary.unchanged},{label:"median changed",className:"bar-changed",value:p=>p.adjacent_diff_summary.changed},{label:"median added",className:"bar-added",value:p=>p.adjacent_diff_summary.added},{label:"median removed",className:"bar-removed",value:p=>p.adjacent_diff_summary.removed}],p=>`${(p.churn_range.median*100).toFixed(1)}% · ${(p.churn_range.minimum*100).toFixed(1)}–${(p.churn_range.maximum*100).toFixed(1)}`);
  const familyLabels={resources:"Resource",parameters:"Parameter",forwarding:"Forwarding",zone_edges:"Zone edge",zone_prefixes:"Zone prefix"};
  const families=Object.entries(state.manifest.run_summary.baseline_family_bytes).map(([family,value])=>({canonical_label:family,display_label:familyLabels[family]??family.replaceAll("_"," "),family,value}));stackedRows("chart-family",families,[{label:"whole-run serialized bytes",className:p=>`bar-${p.family}`,value:p=>p.value}],p=>bytes(p.value));
  document.querySelectorAll("[data-bucket]").forEach(row=>row.addEventListener("click",()=>selectBucket(row.dataset.bucket)));renderBucket(state.bucketIndex);
}
function renderSelected(){const p=current(),s=p.summary,d=p.adjacent_diff_summary,e=p.compile_evidence;document.getElementById("coverage-label").textContent=`P${p.plan_index} → C${p.plan_index}/R${p.realization_index} · ${p.coverage} · ${p.start_timestamp??"synthetic S0"} → ${p.end_timestamp}`;document.getElementById("selected-transform").innerHTML=`<section class="card"><h3>Logical Plan delta · P${p.plan_index}</h3><div class="metric-grid">${metric("Connection",number(s.input_connection))}${metric("Parameter",number(s.input_parameter))}${metric("Forwarding",number(s.input_forwarding))}${metric("Precedence",number(s.input_precedence))}</div></section><div class="transform-arrow">→</div><section class="card"><h3>Compiled C${p.plan_index} / immutable R${p.realization_index}</h3><p class="muted">Interval operations and constraints</p><div class="metric-grid">${metric("Resource intents",number(s.intent_resources))}${metric("Qdisc intents",number(s.intent_parameters))}${metric("Forwarding intents",number(s.intent_forwarding))}${metric("Zone-edge intents",number(s.intent_zone_edges))}${metric("Constraints",number(s.intent_constraints))}</div><p class="muted">Complete interval-end baseline</p><div class="metric-grid">${metric("Records",number(s.baseline_total))}${metric("Serialized",bytes(p.serialized_bytes.baseline_total))}${metric("Compile time",seconds(e?.compile_seconds))}</div></section><div class="transform-arrow">→</div><section class="card"><h3>${d.bootstrap_from_empty?"Bootstrap from empty":"Since previous realization"}</h3><div class="metric-grid">${metric("Unchanged",number(d.unchanged))}${metric("Changed",number(d.changed))}${metric("Added",number(d.added))}${metric("Removed",number(d.removed))}${metric("Churn",`${(d.churn_ratio*100).toFixed(2)}%`)}</div></section>`;}
function sectionLabel(value){return value.replace("plan-","Plan · ").replace("intents-","Compiled · ").replace("baseline-","Baseline · ").replaceAll("-"," ");}
function initializeSelectors(){const plans=state.manifest.plans,select=document.getElementById("plan-select");select.innerHTML=plans.map(p=>`<option value="${p.plan_index}">P${p.plan_index} → C${p.plan_index}/R${p.realization_index} ${esc(p.coverage)}</option>`).join("");document.getElementById("plan-scrubber").innerHTML=plans.map(p=>`<button class="plan-button" data-plan="${p.plan_index}"><strong>P${p.plan_index}</strong><br><span>C${p.plan_index}/R${p.realization_index}</span></button>`).join("");select.addEventListener("change",()=>selectPlan(select.value));document.querySelectorAll(".plan-button").forEach(button=>button.addEventListener("click",()=>selectPlan(button.dataset.plan)));const sections=[...new Set(plans.flatMap(p=>Object.keys(p.sections)).filter(value=>!value.startsWith("diff-")))];document.getElementById("detail-section").innerHTML=sections.map(value=>`<option value="${esc(value)}">${esc(sectionLabel(value))}</option>`).join("");document.getElementById("detail-section").value=state.detailSection;}
async function decodeExactChunk(value){if(!value||value.encoding!=="gzip-base64-json")return value;if(typeof DecompressionStream!=="function")throw new Error("This browser lacks gzip chunk support");const binary=atob(value.data),bytes=Uint8Array.from(binary,ch=>ch.charCodeAt(0)),stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip")),text=await new Response(stream).text();if(new TextEncoder().encode(text).length!==value.uncompressed_bytes)throw new Error("Compiler chunk size audit failed");return JSON.parse(text);}
function loadChunk(descriptor,token){return new Promise((resolve,reject)=>{if(!descriptor){resolve({rows:[]});return;}const script=document.createElement("script");script.src=descriptor.path;script.onload=async()=>{const registry=window.__SKYSEED_BPF_COMPILER_CHUNKS__||{},encoded=registry[descriptor.chunk_id];delete registry[descriptor.chunk_id];script.remove();if(token!==state.loadToken){resolve(null);return;}if(!encoded){reject(new Error(`missing ${descriptor.chunk_id}`));return;}try{resolve(await decodeExactChunk(encoded));}catch(error){reject(error);}};script.onerror=()=>{script.remove();reject(new Error(`unable to load ${descriptor.path}`));};document.body.appendChild(script);});}
function rowIdentity(row){return row.transition_id||row.precedence_id||row.intent_id||row.constraint_id||row.connection_ref||row.route_ref||row.zone_id||row.key||"record";}
function referenceButtons(row){const refs=[...(row.compiled_intent_refs||[]),row.origin_transition_id,row.origin_precedence_id,row.predecessor?.intent_id,row.successor?.intent_id].filter(Boolean);return refs.length?`<p><strong>Exact references</strong></p>${refs.map(ref=>`<button data-reference="${esc(ref)}">${esc(ref)}</button>`).join(" ")}`:'<p class="muted">No narrower direct provenance reference is claimed for this record.</p>';}
function sectionForReference(ref){if(ref.includes("-plan-connection-"))return "plan-connections";if(ref.includes("-plan-parameter-"))return "plan-parameters";if(ref.includes("-plan-forwarding-"))return "plan-forwarding";if(ref.includes("-plan-precedence-"))return "plan-precedences";if(ref.includes("-resource-"))return "intents-resources";if(ref.includes("-parameter-"))return "intents-parameters";if(ref.includes("-forwarding-"))return "intents-forwarding";if(ref.includes("-zone-edge-"))return "intents-zone-edges";if(ref.includes("-constraint-"))return "constraints";return null;}
function indexFromReference(ref){const match=String(ref).match(/-(\d+)$/);return match?Number(match[1]):0;}
function openReference(ref){const section=sectionForReference(ref);if(!section)return;state.detailSection=section;const descriptors=current().sections[section]||[],index=indexFromReference(ref),page=Math.max(0,descriptors.findIndex(value=>value.start_index<=index&&index<value.end_index));state.detailPage=page;document.getElementById("detail-section").value=section;renderDetail(ref);}
async function renderDetail(selectReference=null){const p=current(),descriptors=p.sections[state.detailSection]||[];state.detailPage=Math.max(0,Math.min(state.detailPage,Math.max(0,descriptors.length-1)));const token=++state.loadToken;document.getElementById("detail-page").textContent=descriptors.length?`Page ${state.detailPage+1} / ${descriptors.length}`:"No records";document.getElementById("detail-prev").disabled=state.detailPage===0;document.getElementById("detail-next").disabled=state.detailPage>=descriptors.length-1;try{const payload=await loadChunk(descriptors[state.detailPage],token);if(!payload)return;const query=document.getElementById("detail-search").value.toLowerCase(),rows=payload.rows.filter(row=>!query||JSON.stringify(row).toLowerCase().includes(query));document.getElementById("detail-count").textContent=`${number(rows.length)} shown · ${number(descriptors.reduce((sum,value)=>sum+value.rows,0))} in family`;document.getElementById("detail-list").innerHTML=rows.length?rows.map((row,index)=>`<div class="evidence-row" data-row="${index}"><strong>${esc(rowIdentity(row))}</strong><br><span class="muted">${esc(row.operation||row.reason||row.kind||row.nominal_time||state.detailSection)}</span></div>`).join(""):'<p class="muted">No matching records on this page.</p>';document.querySelectorAll("[data-row]").forEach(node=>node.addEventListener("click",()=>selectExact(rows[Number(node.dataset.row)])));if(selectReference){const row=rows.find(value=>rowIdentity(value)===selectReference);if(row)selectExact(row);}}catch(error){document.getElementById("detail-list").innerHTML=`<p>${esc(error.message)}</p>`;}}
function selectExact(row){document.getElementById("exact-json").textContent=JSON.stringify(row,null,2);document.getElementById("reference-actions").innerHTML=referenceButtons(row);document.querySelectorAll("[data-reference]").forEach(button=>button.addEventListener("click",()=>openReference(button.dataset.reference)));}
function baselineDescriptor(planIndex,family,index){if(planIndex===null||index===null)return null;const plan=state.manifest.plans[planIndex],descriptors=plan.sections[`baseline-${family.replaceAll("_","-")}`]||[];return descriptors.find(value=>value.start_index<=index&&index<value.end_index)||null;}
async function selectDiff(row){const token=++state.loadToken,previousPlan=current().adjacent_diff_summary.bootstrap_from_empty?null:state.planIndex-1,beforeDescriptor=baselineDescriptor(previousPlan,state.diffFamily,row.previous_index),afterDescriptor=baselineDescriptor(state.planIndex,state.diffFamily,row.current_index);try{const [beforeChunk,afterChunk]=await Promise.all([loadChunk(beforeDescriptor,token),loadChunk(afterDescriptor,token)]);if(token!==state.loadToken)return;const before=row.previous_index===null?null:beforeChunk.rows[row.previous_index-beforeDescriptor.start_index],after=row.current_index===null?null:afterChunk.rows[row.current_index-afterDescriptor.start_index];document.getElementById("diff-exact").textContent=JSON.stringify({kind:row.kind,key:row.key,before,after},null,2);}catch(error){document.getElementById("diff-exact").textContent=error.message;}}
async function renderDiff(){const p=current(),family=state.diffFamily,summary=p.adjacent_diff_summary.by_family[family],section=`diff-${family.replaceAll("_","-")}`,descriptors=p.sections[section]||[];state.diffPage=Math.max(0,Math.min(state.diffPage,Math.max(0,descriptors.length-1)));document.getElementById("diff-summary").textContent=`unchanged ${number(summary.unchanged)} · changed ${number(summary.changed)} · added ${number(summary.added)} · removed ${number(summary.removed)}`;document.getElementById("diff-page").textContent=descriptors.length?`Page ${state.diffPage+1} / ${descriptors.length}`:"No changed keys";document.getElementById("diff-prev").disabled=state.diffPage===0;document.getElementById("diff-next").disabled=state.diffPage>=descriptors.length-1;const token=++state.loadToken;try{const payload=await loadChunk(descriptors[state.diffPage],token);if(!payload)return;document.getElementById("diff-list").innerHTML=payload.rows.length?payload.rows.map((row,index)=>`<div class="evidence-row" data-diff-row="${index}"><span class="diff-kind ${esc(row.kind)}">${esc(row.kind)}</span> <strong>${esc(row.key)}</strong></div>`).join(""):'<p class="muted">No changed, added, or removed record in this family.</p>';document.querySelectorAll("[data-diff-row]").forEach(node=>node.addEventListener("click",()=>selectDiff(payload.rows[Number(node.dataset.diffRow)])));}catch(error){document.getElementById("diff-list").innerHTML=`<p>${esc(error.message)}</p>`;}}
function initialize(){state.manifest=window.__SKYSEED_BPF_COMPILER_MANIFEST__;if(!state.manifest||!Array.isArray(state.manifest.plans))throw new Error("Compiler manifest is invalid");state.buckets=planBuckets(state.manifest.plans);initializeSelectors();renderCharts();selectPlan(0);document.querySelectorAll(".tab").forEach(button=>button.addEventListener("click",()=>setView(button.dataset.view)));document.getElementById("detail-section").addEventListener("change",event=>{state.detailSection=event.target.value;state.detailPage=0;renderDetail();});document.getElementById("detail-search").addEventListener("input",()=>renderDetail());document.getElementById("detail-prev").addEventListener("click",()=>{state.detailPage--;renderDetail();});document.getElementById("detail-next").addEventListener("click",()=>{state.detailPage++;renderDetail();});document.getElementById("diff-family").addEventListener("change",event=>{state.diffFamily=event.target.value;state.diffPage=0;renderDiff();});document.getElementById("diff-prev").addEventListener("click",()=>{state.diffPage--;renderDiff();});document.getElementById("diff-next").addEventListener("click",()=>{state.diffPage++;renderDiff();});}
const manifestScript=document.createElement("script");manifestScript.src="manifest.js";manifestScript.onload=initialize;manifestScript.onerror=()=>{document.querySelector("main").innerHTML='<section class="card"><h2>Unable to load manifest.js</h2></section>';};document.body.appendChild(manifestScript);
'''


__all__ = ["render_bpf_compiler_page"]
