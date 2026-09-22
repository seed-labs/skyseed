"""Flow-first replay over bounded, retained evidence; no runtime or Models."""
from tools.observation._presentation.html import json_for_script
from tools.observation._presentation.spatial_display import _evidence_globe_script
from ._spatial import _browser_index

_SPATIAL_BODY = '''<section class="card"><h2>Continuous communication replay · 3D</h2>
<label>Communication <select id="traffic-flow"></select></label><p id="traffic-flow-summary"></p>
<div class="toolbar"><button id="traffic-play">Play</button><label>Speed <select id="traffic-speed"><option value="0.25">0.25×</option><option value="1" selected>1×</option><option value="5">5×</option><option value="20">20×</option></select></label><label>Seek time <input id="traffic-seek" aria-label="Replay time in seconds" type="range" min="0" max="0" step="0.01" value="0"></label><strong id="traffic-play-time"></strong><button id="traffic-reset-globe">Reset globe</button><label><input id="traffic-planned" type="checkbox"> Planned request path (dashed purple)</label></div>
<p>One episode/probe identity stays selected across its recorded segments. Cyan request and orange reply markers replay only ingress-supported arrivals, in recorded order. Each retained exchange is illustrated for up to one simulated second after its final observation; marker timing and straight-line motion are schematic, not measured transit or interpolated satellite motion. Fast playback may aggregate exchanges between display ticks. Idle intervals, ambiguous associations and missing replies never become successful paths.</p>
<canvas id="traffic-globe" width="1100" height="520" style="width:100%;height:520px;background:#06111e;touch-action:none;cursor:grab"></canvas>
<p id="traffic-geometry-basis" role="status"></p><p id="traffic-selection-status" role="status"></p>
<details id="traffic-transaction-details"><summary>Individual transaction and hop evidence</summary>
<div class="toolbar"><label>Sequence <select id="traffic-direction"><option value="request">Observed request</option><option value="reply">Observed reply</option></select></label><button id="traffic-step-prev">Previous observation</button><input id="traffic-step" aria-label="Packet observation step" type="range" min="0" max="0" value="0"><button id="traffic-step-next">Next observation</button><button id="traffic-focus-step">Focus current node</button><button id="traffic-resume-flow">Return to flow replay</button></div>
<div id="traffic-step-detail"></div><details><summary>Complete ordered observations / gaps</summary><ol id="traffic-hop-list"></ol></details>
<div class="scroll"><table><thead><tr><th>ICMP</th><th>Direction</th><th>Observed time</th><th>Formal boundary</th><th>Observed</th><th>Planned</th><th>Planned path / reason</th><th>Classification</th><th>Evidence reason</th></tr></thead><tbody id="traffic-selected-transaction"></tbody></table></div></details></section>'''


def _traffic_spatial_script(data):
    return _evidence_globe_script() + 'const TRAFFIC_BROWSER=' + json_for_script(_browser_index(data)) + ';\n' + r'''
const TRAFFIC_NODES=TRAFFIC_BROWSER.nodes,TRAFFIC_TIMES=TRAFFIC_BROWSER.times,TRAFFIC_ROWS=TRAFFIC_BROWSER.transactions,TRAFFIC_FLOWS=TRAFFIC_BROWSER.flows;
const cursor=document.getElementById('traffic-time'),allTimes=document.getElementById('traffic-all-times'),seek=document.getElementById('traffic-seek'),flowSelect=document.getElementById('traffic-flow');
const origin=Date.parse(TRAFFIC_TIMES[0]),seconds=t=>t==null?null:(Date.parse(t)-origin)/1000,frameSeconds=TRAFFIC_TIMES.map(seconds),eventSeconds=TRAFFIC_ROWS.map(r=>seconds(r.evidence_time));
const replayEnd=Math.max(0,...frameSeconds,...eventSeconds.filter(t=>t!=null).map(t=>t+1));seek.max=replayEnd;
let page=0,selectedIndex=null,selectedTransaction=null,selectedShard=null,shardData=null,frameIndex=null,requestedFrame=null,query='',frameSerial=0,transactionSerial=0;
let selectedFlow=TRAFFIC_FLOWS[0]||null,playTime=0,playTimer=null,lastTick=null,manualInspection=false,detailKey='',loadFailure='',filterRevision=0,listKey='';
const visibility=createShellVisibility(TRAFFIC_NODES,TRAFFIC_ZONE_COUNT,()=>{filterRevision++;detailKey='';page=0;renderTraffic();renderFlows();drawProgression()});
const globe=createEvidenceGlobe(document.getElementById('traffic-globe'),TRAFFIC_NODES,visibility),step=document.getElementById('traffic-step'),direction=document.getElementById('traffic-direction');
function invalidateTrafficAsset(kind){const old=document.getElementById('traffic-'+kind+'-chunk');if(old)old.remove();return kind==='frame'?++frameSerial:++transactionSerial}
function loadTrafficAsset(kind,index,token,callback){
 const isFrame=kind==='frame',slot=isFrame?'SKYSEED_TRAFFIC_FRAME':'SKYSEED_TRAFFIC_TRANSACTIONS';window[slot]=null;
 const script=document.createElement('script');script.id='traffic-'+kind+'-chunk';script.src=`data/${kind}-${String(index).padStart(3,'0')}.js`;
 script.onload=()=>{const value=window[slot];window[slot]=null;script.remove();if(token!==(isFrame?frameSerial:transactionSerial))return;
  if(!value||(isFrame?value.index!==index:!Object.keys(value).every(k=>Math.floor(Number(k)/TRAFFIC_BROWSER.chunk_size)===index))){loadFailure='Evidence chunk identity mismatch; seek or select again to retry.';drawProgression();return}callback(value)};
 script.onerror=()=>{script.remove();if(token===(isFrame?frameSerial:transactionSerial)){loadFailure='Evidence chunk load failed; seek or select again to retry.';drawProgression()}};document.body.append(script);
}
function selectFrame(index){
 // Invalidate BEFORE the cache branch: 0 → pending 1 → cached 0 fences late 1.
 const token=invalidateTrafficAsset('frame');requestedFrame=index;cursor.value=String(index);document.getElementById('traffic-time-label').textContent=TRAFFIC_TIMES[index]||'unknown';
 if(frameIndex===index){drawProgression();return}
 loadTrafficAsset('frame',index,token,value=>{frameIndex=index;globe.setFrame(value);drawProgression()});drawProgression();
}
function selectedSteps(){return selectedTransaction?.[direction.value]||[]}
function nodeLabel(index){return index==null?'[unresolved node]':(visibility.nodeVisible(index)?'':'[hidden endpoint] ')+TRAFFIC_NODES[index].node_id}
function trafficFrameAt(time){let i=0;while(i+1<frameSeconds.length&&frameSeconds[i+1]<=time)i++;return i}
function trafficFlowRow(flow,time){let found=null;for(const i of flow?.transactions||[]){const at=eventSeconds[i];if(at!=null&&at<=time&&(found==null||at>eventSeconds[found]))found=i}return found}
function trafficAnimation(transaction,elapsed){
 const edges=[],markers=[];if(!transaction||elapsed<0||elapsed>=1||transaction.association_uncertainty.length)return{edges,markers};
 for(const [name,color,phase]of [['request','#5ee5e5',elapsed*2],['reply','#ffbb65',elapsed*2-1]]){
  const steps=transaction[name]||[];if(phase<0||!steps.length)continue;const at=Math.min(steps.length-1,Math.floor(phase*steps.length));
  for(const v of steps.slice(0,at+1))if(v.arrival_supported)edges.push({a:v.ingress_peer,b:v.node,color,width:2.7});
  const v=steps[at];if(phase<1&&v.arrival_supported)markers.push({a:v.ingress_peer,b:v.node,color,progress:phase*steps.length-at,direction:name});
 }return{edges,markers};
}
function drawProgression(){
 const steps=selectedSteps(),at=Math.min(Number(step.value),Math.max(0,steps.length-1));step.max=Math.max(0,steps.length-1);step.value=at;
 const elapsed=selectedIndex==null?null:playTime-eventSeconds[selectedIndex],active=manualInspection||(elapsed!=null&&elapsed>=0&&elapsed<1);
 const animation=manualInspection?{edges:steps.slice(0,at+1).filter(v=>v.arrival_supported).map(v=>({a:v.ingress_peer,b:v.node,color:direction.value==='request'?'#5ee5e5':'#ffbb65',width:2.7})),markers:[]}:trafficAnimation(selectedTransaction,elapsed??-1);
 const planned=active&&document.getElementById('traffic-planned').checked?selectedTransaction?.planned||[]:[],overlay=[];
 for(let i=1;i<planned.length;i++)if(planned[i-1]!=null&&planned[i]!=null)overlay.push({a:planned[i-1],b:planned[i],color:'#c59bff',dash:[5,5],width:1.7});
 const current=steps[at];globe.setOverlay(frameIndex===requestedFrame?[...overlay,...animation.edges]:[],manualInspection?current?.node:null,frameIndex===requestedFrame?animation.markers:[]);
 visibility.focus([...animation.edges.flatMap(v=>[v.a,v.b]),...planned]);
 document.getElementById('traffic-geometry-basis').textContent=frameIndex!==requestedFrame?'Loading selected retained frame; overlays withheld until it arrives.':`Geometry: retained PhysicalPositionSnapshot ${TRAFFIC_TIMES[frameIndex]}. Latest sample at/before replay time; no continuous position measurement. Schematic markers use these sampled endpoints.`;
 const row=TRAFFIC_ROWS[selectedIndex];document.getElementById('traffic-selection-status').textContent=loadFailure||(selectedTransaction?`${row.label} · ${row.classification} · evidence through ${row.evidence_time||'unknown time'}`+(selectedTransaction.association_uncertainty.length?' · AMBIGUOUS: '+selectedTransaction.association_uncertainty.join('; '):'')+(!selectedTransaction.reply.length?' · NO REPLY OBSERVATIONS':'')+([...selectedTransaction.request,...selectedTransaction.reply].some(v=>v.gap_before||v.gap_after)?' · GAP in retained observations; inspect hops':'')+(!active?' · No exchange observation at this time; last evidence shown only in details.':''):selectedIndex!=null?'Loading selected exchange…':'No exchange observation at this time; no path is inferred.');
 const key=[selectedIndex,!!selectedTransaction,direction.value,at,filterRevision].join(':');if(key!==detailKey){detailKey=key;renderTransactionDetails(steps,at)}
}
function renderTransactionDetails(steps,at){
 function describe(v,i){return `${i+1}. packet #${v.packet} · ${v.observed_time||'time unknown'} · `+(v.arrival_supported?nodeLabel(v.ingress_peer)+' → '+nodeLabel(v.node)+' (arrival observed on ingress)':nodeLabel(v.node)+' (node observation only)')+` · ${v.decision}: ${v.reason}`+(v.gap_before?' · GAP: '+v.gap_before:'')+(v.gap_after?' · GAP: '+v.gap_after:'')}
 document.getElementById('traffic-step-detail').textContent=steps[at]?describe(steps[at],at):`No ${direction.value} packet observations. Missing evidence is a gap, not a zero-hop path.`;
 const list=document.getElementById('traffic-hop-list');list.replaceChildren();steps.forEach((v,i)=>{const item=document.createElement('li'),button=document.createElement('button');button.textContent=describe(v,i);button.setAttribute('aria-pressed',String(i===at));button.onclick=()=>selectStep(i);item.append(button);list.append(item)});
 document.getElementById('traffic-step-prev').disabled=!steps.length||at===0;document.getElementById('traffic-step-next').disabled=!steps.length||at===steps.length-1;
 const selected=document.getElementById('traffic-selected-transaction');selected.innerHTML=selectedTransaction?.html||'';
 const byId=new Map(TRAFFIC_NODES.map((n,i)=>[n.node_id,i]));for(const span of selected.querySelectorAll('[data-node-id]')){const i=byId.get(span.dataset.nodeId);if(i!=null&&!visibility.nodeVisible(i))span.textContent='[hidden endpoint] '+span.textContent}
}
function selectStep(i){pauseTraffic();manualInspection=true;step.value=i;const v=selectedSteps()[i];if(v?.frame!=null)selectFrame(v.frame);else drawProgression();renderTraffic()}
function selectTransaction(index,inspect=true){
 const token=invalidateTrafficAsset('transactions');selectedIndex=index;selectedTransaction=null;step.value=0;detailKey='';loadFailure='';
 if(inspect){pauseTraffic();manualInspection=true;const flow=TRAFFIC_FLOWS.find(f=>f.key===TRAFFIC_ROWS[index].flow);if(flow){selectedFlow=flow;renderFlows()}}
 if(index==null){drawProgression();return}const shard=TRAFFIC_ROWS[index].shard;
 const select=value=>{if(token!==transactionSerial)return;selectedTransaction=value[index];if(!selectedTransaction){loadFailure='Missing selected transaction identity';drawProgression();return}if(inspect)selectStep(0);else drawProgression();renderTraffic()};
 drawProgression();if(selectedShard===shard&&shardData){select(shardData);return}
 selectedShard=null;shardData=null;loadTrafficAsset('transactions',shard,token,value=>{selectedShard=shard;shardData=value;select(value)});
}
function renderFlows(){
 flowSelect.replaceChildren();for(const flow of TRAFFIC_FLOWS){const option=document.createElement('option');option.value=flow.key;option.textContent=flow.label;flowSelect.append(option)}flowSelect.value=selectedFlow?.key||'';
 const flow=selectedFlow;document.getElementById('traffic-flow-summary').textContent=flow?`${flow.transactions.length} retained exchanges · recorded segments ${flow.segments.join(', ')||'none'} · ${flow.transactions.filter(i=>eventSeconds[i]==null).length} untimed exchanges (details only). `+(flow.sent!=null?`Executor: ${flow.sent} sent / ${flow.received} received / ${flow.loss} loss. Executor requests without packet records remain gaps; see qualification below.`:'No authoritative executor totals for this selection.'):'No retained communication identities.';
}
function seekTraffic(time,force=true){
 playTime=Math.max(0,Math.min(replayEnd,time));manualInspection=false;loadFailure='';seek.value=String(playTime);document.getElementById('traffic-play-time').textContent=playTime.toFixed(2)+' s';
 const frame=trafficFrameAt(playTime);if(force||requestedFrame!==frame)selectFrame(frame);
 const next=trafficFlowRow(selectedFlow,playTime);if(next!==selectedIndex||(force&&next!=null&&!selectedTransaction))selectTransaction(next,false);else drawProgression();renderTraffic();
}
function pauseTraffic(){if(playTimer!=null)clearInterval(playTimer);playTimer=null;lastTick=null;document.getElementById('traffic-play').textContent='Play'}
function tickTraffic(now){if(lastTick==null){lastTick=now;return}const elapsed=Math.max(0,(now-lastTick)/1000);lastTick=now;seekTraffic(playTime+elapsed*Number(document.getElementById('traffic-speed').value),false);if(playTime>=replayEnd)pauseTraffic()}
function playTraffic(){if(playTimer!=null){pauseTraffic();return}seekTraffic(playTime>=replayEnd?0:playTime);lastTick=performance.now();document.getElementById('traffic-play').textContent='Pause';playTimer=setInterval(()=>tickTraffic(performance.now()),50)}
function renderTraffic(){
 const key=JSON.stringify([page,selectedFlow?.key,allTimes.checked,cursor.value,query,selectedIndex,filterRevision]);if(key===listKey)return;listKey=key;
 const at=TRAFFIC_TIMES[Number(cursor.value)],rows=TRAFFIC_ROWS.filter(r=>(!selectedFlow||r.flow===selectedFlow.key)&&(allTimes.checked||r.boundary===at)&&r.label.toLowerCase().includes(query.toLowerCase())&&(r.network_scope?visibility.shellVisible(r.network_scope):(r.nodes.length?r.nodes.some(i=>visibility.nodeVisible(i)):true)));
 const pages=Math.max(1,Math.ceil(rows.length/25));page=Math.min(page,pages-1);const target=document.getElementById('traffic-transactions');target.replaceChildren();
 for(const r of rows.slice(page*25,(page+1)*25)){const tr=document.createElement('tr'),button=document.createElement('button');button.textContent=r.label;button.setAttribute('aria-pressed',String(r.index===selectedIndex));button.onclick=()=>{document.getElementById('traffic-transaction-details').open=true;selectTransaction(r.index)};const td=document.createElement('td');td.append(button);tr.append(td);for(const text of [r.request_time||'unknown',r.boundary||'unknown',r.reply?'reply observed':'no reply',r.classification]){const cell=document.createElement('td');cell.textContent=text;tr.append(cell)}target.append(tr)}
 document.getElementById('traffic-visible-count').textContent=`${rows.length} matching / ${TRAFFIC_ROWS.length} transactions · page ${page+1}/${pages} · 25 per page. Selected flow persists across time, segments and filters.`;
 document.getElementById('traffic-prev').disabled=page===0;document.getElementById('traffic-next').disabled=page>=pages-1;
 for(const row of document.querySelectorAll('[data-coverage-shell]'))row.hidden=!visibility.shellVisible(row.dataset.coverageShell);
}
flowSelect.onchange=()=>{selectedFlow=TRAFFIC_FLOWS.find(f=>f.key===flowSelect.value)||null;page=0;renderFlows();seekTraffic(playTime)};
seek.oninput=()=>seekTraffic(Number(seek.value));document.getElementById('traffic-play').onclick=playTraffic;
document.getElementById('traffic-speed').onchange=()=>{lastTick=performance.now()};
cursor.oninput=()=>{allTimes.checked=false;page=0;if(manualInspection){selectFrame(Number(cursor.value));renderTraffic()}else seekTraffic(frameSeconds[Number(cursor.value)])};allTimes.onchange=()=>{page=0;renderTraffic()};
document.getElementById('traffic-prev').onclick=()=>{page--;renderTraffic()};document.getElementById('traffic-next').onclick=()=>{page++;renderTraffic()};document.getElementById('traffic-query').oninput=e=>{query=e.target.value;page=0;renderTraffic()};
direction.onchange=()=>{step.value=0;selectStep(0)};step.oninput=()=>selectStep(Number(step.value));
document.getElementById('traffic-step-prev').onclick=()=>selectStep(Math.max(0,Number(step.value)-1));document.getElementById('traffic-step-next').onclick=()=>selectStep(Math.min(selectedSteps().length-1,Number(step.value)+1));
document.getElementById('traffic-focus-step').onclick=()=>{const v=selectedSteps()[Number(step.value)];if(v?.frame!=null&&v.frame!==frameIndex){selectFrame(v.frame);return}globe.focus(v?.node)};
document.getElementById('traffic-reset-globe').onclick=()=>globe.reset();document.getElementById('traffic-planned').onchange=drawProgression;
document.getElementById('traffic-resume-flow').onclick=()=>seekTraffic(playTime);window.addEventListener('pagehide',pauseTraffic);
renderFlows();seekTraffic(0);
'''.replace('TRAFFIC_ZONE_COUNT', str(data['planned_evidence']['formal_transitions'][0]['state_summary']['zone_assignment_count']))
