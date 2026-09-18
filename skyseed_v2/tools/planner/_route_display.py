"""Private current-demand navigation over retained identities and deltas."""

_TIMELINE_SCRIPT = r'''
function plannerRouteState(timeline, index) {
 let current=null;
 for(const [at,value] of timeline){if(at>index)break;current=value}
 return current;
}
'''

_ROUTE_SCRIPT = _TIMELINE_SCRIPT + r'''
function plannerRouteRelevant(demand, focus, assignments, routes, index) {
 if(!focus)return true;
 if(focus.kind==='node')return demand.source===focus.index || demand.target.node===focus.index;
 if(demand.target.kind==='zone')return demand.target.zone_id===focus.id;
 const scope=demand.network_scope||demand.target.network_scope||'', serving=assignments.find(v=>v[0]===focus.id&&(v[2]||'')===scope)?.[1];
 return serving!=null && demand.source===serving && routes.some(v=>v.source===demand.target.node && v.target.zone_id===focus.id && (v.network_scope||v.target.network_scope||'')===scope && plannerRouteState(v.activity,index)!=null);
}
let routePage=0, routeQuery='', entityQuery='', entityPage=0, routeHistory=false;
function showRawRoutes(root){
 root.innerHTML='<h3>Recorded demand identities at this time</h3><p class="muted">Locate a Zone, GroundStation or satellite, then select a current demand / destination. Only recorded demands are listed; business associations do not imply all-to-all connectivity or operational reachability.</p><details id="route-locator"><summary>Locate entity</summary><input id="route-entity-query" placeholder="Zone, GS or satellite identity"><div id="route-entities"></div></details><p id="route-scope"></p><input id="route-demand-query" placeholder="Search relevant current demands"><label><input id="route-history" type="checkbox"> Include absent / withdrawn demands</label><div id="route-demands"></div><div id="route-outcome"></div>';
 const entities=[...nodes.map((n,i)=>({kind:'node',index:i,label:(n.display_name?n.display_name+' · ':'')+n.node_id})),...zones.map(z=>({kind:'zone',id:z.zone_id,label:'Zone '+z.zone_id}))];
 const locate=document.getElementById('route-locator');locate.open=!focus;
 const eq=document.getElementById('route-entity-query');eq.value=entityQuery;
 function entityResults(){
  const result=entities.filter(v=>v.label.toLowerCase().includes(entityQuery.toLowerCase())), pages=Math.max(1,Math.ceil(result.length/20));entityPage=Math.min(entityPage,pages-1);
  const out=document.getElementById('route-entities');out.replaceChildren();
  const count=document.createElement('p');count.textContent=`${result.length} matching / ${entities.length} entities · page ${entityPage+1}/${pages}`;out.append(count);
  for(const v of result.slice(entityPage*20,(entityPage+1)*20)){const b=document.createElement('button');b.type='button';b.className='item';b.textContent=v.label;b.onclick=()=>selectPlannerEntity(v);out.append(b)}
  for(const [label,delta]of [['Previous entities',-1],['Next entities',1]]){const b=document.createElement('button');b.textContent=label;b.disabled=entityPage+delta<0||entityPage+delta>=pages;b.onclick=()=>{entityPage+=delta;entityResults()};out.append(b)}
 }
 eq.oninput=()=>{entityQuery=eq.value;entityPage=0;entityResults()};entityResults();
 document.getElementById('route-scope').textContent=focus?`Relevant endpoint demands for ${focus.kind==='node'?node(focus.index).node_id:'Zone '+focus.id+' and its recorded serving-satellite / GS associations'}`:'All recorded demands; choose an entity to narrow the list.';
 const query=document.getElementById('route-demand-query'),history=document.getElementById('route-history');query.value=routeQuery;history.checked=routeHistory;
 function demandResults(){
  const serving=focus?.kind==='zone'?frame.zone_assignments.filter(v=>v[0]===focus.id).map(v=>v[1]):[];
  const associated=new Set(focus?.kind==='zone'?DATA.route_index.filter(v=>v.target.zone_id===focus.id&&plannerRouteState(v.activity,index)!=null).map(v=>v.source):[]);
  const relevant=DATA.route_index.filter(v=>focus?.kind==='zone'?(v.target.kind==='zone'?v.target.zone_id===focus.id:serving.includes(v.source)&&associated.has(v.target.node)):plannerRouteRelevant(v,focus,frame.zone_assignments,DATA.route_index,index));
  const active=relevant.filter(v=>plannerRouteState(v.activity,index)!=null);
  const result=(routeHistory?relevant:active).filter(v=>v.label.toLowerCase().includes(routeQuery.toLowerCase()));
  const pages=Math.max(1,Math.ceil(result.length/25));routePage=Math.min(routePage,pages-1);
  const out=document.getElementById('route-demands');out.replaceChildren();const count=document.createElement('p');
  count.textContent=`${active.length} current / ${relevant.length} historical relevant demands; ${result.length} match · page ${routePage+1}/${pages} · 25 per page`;out.append(count);
  for(const v of result.slice(routePage*25,(routePage+1)*25)){const b=document.createElement('button');b.type='button';b.className='item';b.dataset.routeDemand=v.key;b.setAttribute('aria-pressed',String(routeFocus?.key===v.key));b.textContent=v.label+' · '+(plannerRouteState(v.activity,index)||'absent / withdrawn');b.onclick=()=>loadRoute(v);out.append(b)}
  if(!result.length){const p=document.createElement('p');p.textContent='No matching active demand. This does not mean a Route was computed and found unreachable.';out.append(p)}
  for(const [label,delta]of [['Previous demands',-1],['Next demands',1]]){const b=document.createElement('button');b.textContent=label;b.disabled=routePage+delta<0||routePage+delta>=pages;b.onclick=()=>{routePage+=delta;demandResults()};out.append(b)}
 }
 query.oninput=()=>{routeQuery=query.value;routePage=0;demandResults()};history.onchange=()=>{routeHistory=history.checked;routePage=0;demandResults()};demandResults();showRouteOutcome();
}
'''


_PAIR_SCRIPT = r'''
function plannerPairRefs(pair, assignments, routes, reverse) {
 const scope=pair.network_scope||'', serving=(assignments instanceof Map?assignments.get(JSON.stringify([scope,pair.zone_id])):assignments.find(v=>v[0]===pair.zone_id&&(v[2]||'')===scope)?.[1])??null;
 return {serving, forward:routes[pair.forward], reverse:serving==null?null:reverse.get(JSON.stringify([scope,serving,pair.ground_station]))||null};
}
function plannerReverseIndex(routes,nodes) {
 const out=new Map();for(const r of routes)if(nodes[r.source]?.kind==='satellite'&&r.target.kind==='ground-station')out.set(JSON.stringify([r.network_scope||'',r.source,r.target.node]),r);return out;
}
function plannerPairVisible(pair,refs,nodeVisible,includeUnserved,shellVisible=()=>true) {
 if(pair.network_scope)return shellVisible(pair.network_scope)&&(refs.serving!=null||includeUnserved);
 return refs.serving==null?includeUnserved:nodeVisible(refs.serving);
}
function plannerAccessExplanation(refs,pair,frame,nodes) {
 const serving=nodes[refs.serving];if(!serving||serving.shell_id==null||serving.asn==null||!Array.isArray(frame.operational_connections))return '';
 const owner=n=>n?.shell_id!=null&&n.asn!=null?JSON.stringify([n.asn,n.shell_id]):null, expected=owner(serving);
 let access=false;
 for(const [,a,b] of frame.operational_connections){const x=nodes[a],y=nodes[b];if(!x||!y)return '';
  if(x.kind==='satellite'&&y.kind==='satellite'&&(!owner(x)||owner(x)!==owner(y)))return '';
  if(a===pair.ground_station||b===pair.ground_station){const sat=nodes[a===pair.ground_station?b:a];if(sat?.kind!=='satellite'||!owner(sat))return '';if(owner(sat)===expected)access=true}
 }
 return access?'':'GS has no operational access to the serving Shell (retained operational graph; GS transit is forbidden).';
}
let pairFocus=null,pairPage=0,pairQuery='',pairIncludeUnserved=true,pairRawOpen=false,pairGlobal=false,pairReverse=null,pairSignature='',pairSerial=0,pairRecords=new Map(),pairCache=new Map(),pairError='';
let pairByZone=null,pairByGround=null,pairSearch=null,pairAssignmentRows=null,pairAssignments=null;
let pairHighlights={nodes:new Set(),zones:new Set(),serving:null};
function preparePairView(){
 if(!pairByZone){
  pairByZone=new Map();pairByGround=new Map();pairSearch=new Map();pairReverse=plannerReverseIndex(DATA.route_index,nodes);
  for(const p of DATA.pair_index||[]){
   for(const [map,key]of [[pairByZone,p.zone_id],[pairByGround,p.ground_station]]){if(!map.has(key))map.set(key,[]);map.get(key).push(p)}
   const gs=nodes[p.ground_station];pairSearch.set(p.key,((p.network_scope||'')+' '+p.zone_id+' '+(gs.display_name||'')+' '+gs.node_id).toLowerCase());
  }
 }
 if(pairAssignmentRows!==frame.zone_assignments){pairAssignmentRows=frame.zone_assignments;pairAssignments=new Map(frame.zone_assignments.map(v=>[JSON.stringify([v[2]||'',v[0]]),v[1]]))}
}
function pairRefs(pair){preparePairView();return plannerPairRefs(pair,pairAssignments,DATA.route_index,pairReverse)}
function currentPairRefs(){return pairFocus?pairRefs(pairFocus):null}
function pairContextPairs(){
 preparePairView();
 if(focus?.kind==='zone')return pairByZone.get(focus.id)||[];
 if(focus?.kind==='node')return nodes[focus.index].kind==='ground-station'?pairByGround.get(focus.index)||[]:[];
 return pairGlobal?DATA.pair_index||[]:[];
}
function pairVisible(pair){return plannerPairVisible(pair,pairRefs(pair),i=>visibility.nodeVisible(i),pairIncludeUnserved,s=>visibility.shellVisible(s))}
function selectPlannerEntity(next,browseAll=false){
 focus=next;relationFocus=null;routeFocus=null;routeLoadSerial++;clearPair();
 pairPage=0;pairQuery='';routePage=0;routeQuery='';pairGlobal=browseAll;
 pairRawOpen=next?.kind==='node'&&nodes[next.index].kind==='satellite';render();
}
function updatePairHighlights(){
 pairHighlights={nodes:new Set(),zones:new Set(),serving:null};
 if(stage!=='route')return;
 const pairs=focus?pairContextPairs():pairFocus?[pairFocus]:[];
 for(const p of pairs)if(pairVisible(p)){pairHighlights.nodes.add(p.ground_station);pairHighlights.zones.add(p.zone_id)}
 if(pairFocus&&pairVisible(pairFocus))pairHighlights.serving=currentPairRefs().serving;
}
function drawPairNodeMark(context,point,index){
 const serving=pairHighlights.serving===index;
 if(!serving&&!pairHighlights.nodes.has(index))return;
 context.strokeStyle=serving?'#ffe59a':'#d8a7ff';context.lineWidth=2;context.beginPath();context.arc(point[0],point[1],serving?9:8,0,7);context.stroke();
}
function pairOutcome(ref){return ref&&pairRecords.has(ref.key)?plannerRouteState(pairRecords.get(ref.key).timeline,index):null}
function pairEdges(){const refs=currentPairRefs();if(!refs)return[];const out=[];for(const [ref,color,name,dash]of [[refs.forward,'#5ee5e5','GS → Zone',[]],[refs.reverse,'#ffbb65','Zone → GS',[7,3]]]){const value=pairOutcome(ref);if(value?.[0]==='reachable')for(let i=1;i<value[2].length;i++)out.push({a:value[2][i-1],b:value[2][i],color,kind:name,raw:value,route:true,dash})}return out}
function clearPair(){pairFocus=null;pairSignature='';pairRecords.clear();pairCache.clear();pairSerial++;pairError=''}
function selectPair(pair){window.SKYSEED_PLANNER_ROUTE_SHARDS={};pairFocus=pair;pairSignature='';pairRecords.clear();pairSerial++;routeFocus=null;routeLoadSerial++;relationFocus=null;render()}
function ensurePairRoutes(){
 preparePairView();
 const refs=currentPairRefs();if(!refs)return;
 const demands=[refs.forward,refs.reverse].filter(Boolean),signature=demands.map(v=>v.key).join(':');if(signature===pairSignature)return;
 pairSignature=signature;pairRecords.clear();pairError='';const token=++pairSerial;
 function next(at){if(token!==pairSerial)return;if(at===demands.length){render();return}const demand=demands[at];
  function accept(value){if(token!==pairSerial)return;if(!value?.[demand.key]){pairError='Missing recorded directional demand in shard.';render();return}pairRecords.set(demand.key,value[demand.key]);next(at+1)}
  if(pairCache.has(demand.shard)){const cached=pairCache.get(demand.shard);pairCache.delete(demand.shard);pairCache.set(demand.shard,cached);accept(cached);return}
  const old=document.getElementById('planner-pair-shard');if(old)old.remove();const script=document.createElement('script');script.id='planner-pair-shard';script.src=`data/routes-${String(demand.shard).padStart(2,'0')}.js`;
  script.onload=()=>{const value=window.SKYSEED_PLANNER_ROUTE_SHARDS?.[demand.shard];if(window.SKYSEED_PLANNER_ROUTE_SHARDS)delete window.SKYSEED_PLANNER_ROUTE_SHARDS[demand.shard];script.remove();if(token!==pairSerial)return;if(pairCache.size>=2)pairCache.delete(pairCache.keys().next().value);pairCache.set(demand.shard,value);accept(value)};
  script.onerror=()=>{script.remove();if(token!==pairSerial)return;pairError='Directional history load failed. Select the pair again to retry.';render()};document.body.append(script);
 }next(0);
}
function showCurrentRoutes(){
 special.innerHTML='<h3>Business routes by entity</h3><p id="pair-context"></p><p>Click a Zone or GroundStation on either map to browse its recorded associations, then choose a pair and move the timeline. Associations come from recorded demands; time changes service, access and directional routes. Lavender endpoint outlines mark associations, not physical links; a gold ring marks the selected pair’s current serving satellite. Cyan: GS → Zone; dashed orange: Zone → GS.</p><button id="pair-global" type="button">Browse all recorded pairs</button><input id="pair-query" placeholder="Filter associated Zone or GroundStation"><label><input id="pair-unserved" type="checkbox"> Include unserved pairs</label><div id="pair-list"></div><div id="pair-outcome"></div><details id="pair-raw"><summary>Raw demands, satellite inspection and history</summary><div id="pair-raw-content"></div></details>';
 const context=document.getElementById('pair-context');
 context.textContent=focus?.kind==='zone'?'Selected Zone '+focus.id:focus?.kind==='node'?((nodes[focus.index].kind==='ground-station'?'Selected GroundStation: ':'Selected satellite: ')+(nodes[focus.index].display_name||nodes[focus.index].node_id)):pairGlobal?'All recorded associations across network scopes':'Select a Zone or GroundStation to begin.';
 document.getElementById('pair-global').onclick=()=>selectPlannerEntity(null,true);
 const query=document.getElementById('pair-query'),unserved=document.getElementById('pair-unserved');query.value=pairQuery;unserved.checked=pairIncludeUnserved;
 const refsFor=pairRefs;
 function servingText(refs){return refs.serving==null?'UNSERVED':node(refs.serving).node_id+' · Shell '+(node(refs.serving).shell_id??'unrecorded')}
 function status(ref){return ref?(plannerRouteState(ref.activity,index)||'absent / withdrawn'):'no recorded demand'}
 function results(){const all=pairContextPairs(),query=pairQuery.toLowerCase(),rows=all.filter(p=>pairSearch.get(p.key).includes(query)&&pairVisible(p));const pages=Math.max(1,Math.ceil(rows.length/20));pairPage=Math.min(pairPage,pages-1);const out=document.getElementById('pair-list');out.replaceChildren();const count=document.createElement('p');
  const entities=focus?.kind==='zone'?`${new Set(all.map(p=>p.ground_station)).size} associated GroundStations (unique)`:focus?.kind==='node'&&nodes[focus.index].kind==='ground-station'?`${new Set(all.map(p=>p.zone_id)).size} associated Zones (unique)`:`${new Set(all.map(p=>p.zone_id)).size} Zones / ${new Set(all.map(p=>p.ground_station)).size} GroundStations`;
  count.textContent=!focus&&!pairGlobal?`${(DATA.pair_index||[]).length} recorded pairs · choose a Zone or GroundStation`:`${entities} · ${all.length} recorded pairs across scopes · ${rows.length} shown by filters · page ${pairPage+1}/${pages}`;out.append(count);
  if(!all.length){const p=document.createElement('p');p.textContent=focus?.kind==='node'&&nodes[focus.index].kind==='satellite'?'Satellite endpoint demands are available below; business associations belong to Zones and GroundStations.':focus?'No recorded business associations for this entity.':`${(DATA.pair_index||[]).length} recorded pairs are available. Click an entity above, or browse all pairs.`;out.append(p)}
  for(const pair of rows.slice(pairPage*20,(pairPage+1)*20)){const refs=refsFor(pair),b=document.createElement('button');b.className='item';b.dataset.pairKey=pair.key;b.setAttribute('aria-pressed',String(pairFocus?.key===pair.key));b.textContent=`${pair.network_scope||'unscoped'} · Zone ${pair.zone_id} ↔ ${node(pair.ground_station).display_name||node(pair.ground_station).node_id} · ${servingText(refs)} · GS → Zone: ${status(refs.forward)} · Zone → GS: ${refs.serving==null?'UNSERVED':status(refs.reverse)}`;b.onclick=()=>selectPair(pair);out.append(b)}
  if(rows.length>20)for(const [label,delta]of [['Previous pairs',-1],['Next pairs',1]]){const b=document.createElement('button');b.textContent=label;b.disabled=pairPage+delta<0||pairPage+delta>=pages;b.onclick=()=>{pairPage+=delta;results()};out.append(b)}
 }
 query.oninput=()=>{pairQuery=query.value;pairPage=0;results()};unserved.onchange=()=>{pairIncludeUnserved=unserved.checked;pairPage=0;render()};results();
 const out=document.getElementById('pair-outcome'),refs=currentPairRefs();out.replaceChildren();
 function line(label,text){const p=document.createElement('p'),strong=document.createElement('strong');strong.textContent=label+': ';p.append(strong,document.createTextNode(text));out.append(p)}
 if(refs){line('Selected pair',`${pairFocus.network_scope||'unscoped'} · Zone ${pairFocus.zone_id} ↔ ${node(pairFocus.ground_station).display_name||node(pairFocus.ground_station).node_id}`);line('Current service',servingText(refs)+(plannerPairVisible(pairFocus,refs,i=>visibility.nodeVisible(i),pairIncludeUnserved,s=>visibility.shellVisible(s))?'':' · hidden by current filter; selection retained'));
  for(const [label,ref]of [['GS → Zone',refs.forward],['Zone → GS',refs.reverse]]){const value=pairOutcome(ref);line(label,!ref?(refs.serving==null?'UNSERVED; no current serving-satellite demand':'No recorded directional demand'):!pairRecords.has(ref.key)?pairError||'Loading recorded outcome…':!value?'Absent / withdrawn at this time':value[0]==='reachable'?'REACHABLE · '+value[2].map(i=>(visibility.nodeVisible(i)?'':'[hidden] ')+(node(i).display_name||node(i).node_id)).join(' → '):'UNREACHABLE · '+value[1]);
   if(DATA.ospf&&value?.[0]==='reachable')appendOspfRoute(out,value[2],DATA.ospf,ospfView,nodes);
   if(value?.[0]==='unreachable'&&value[1]==='NO_ELIGIBLE_PATH'){const why=plannerAccessExplanation(refs,pairFocus,frame,nodes);if(why)line('Retained evidence',why)}
  }
  line('Browsing context','Pair selection keeps the selected entity. The selected pair remains inspectable when list filters hide its row.');
 }else line('Selection','Choose an associated pair to display its independently recorded directions.');
 const raw=document.getElementById('pair-raw'),rawContent=document.getElementById('pair-raw-content');let rawLoaded=false;
 function rawEvidence(){if(rawLoaded)return;rawLoaded=true;showRawRoutes(rawContent);
  if(refs)for(const [label,ref]of [['GS → Zone',refs.forward],['Zone → GS',refs.reverse]]){const record=ref&&pairRecords.get(ref.key);if(!record)continue;const detail=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent=label+' · typed identity and full recorded history';pre.textContent=JSON.stringify({key:ref.key,demand:record.demand,timeline:record.timeline},null,2);detail.append(summary,pre);rawContent.append(detail)}
 }
 raw.open=pairRawOpen;raw.ontoggle=()=>{pairRawOpen=raw.open;if(raw.open)rawEvidence()};if(raw.open)rawEvidence();
}
'''
