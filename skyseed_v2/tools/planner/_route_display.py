"""Private current-demand navigation over retained identities and deltas."""

_ROUTE_SCRIPT = r'''
function plannerRouteState(timeline, index) {
 let current=null;
 for(const [at,value] of timeline){if(at>index)break;current=value}
 return current;
}
function plannerRouteRelevant(demand, focus, assignments, routes, index) {
 if(!focus)return true;
 if(focus.kind==='node')return demand.source===focus.index || demand.target.node===focus.index;
 if(demand.target.kind==='zone')return demand.target.zone_id===focus.id;
 const scope=demand.network_scope||demand.target.network_scope||'', serving=assignments.find(v=>v[0]===focus.id&&(v[2]||'')===scope)?.[1];
 return serving!=null && demand.source===serving && routes.some(v=>v.source===demand.target.node && v.target.zone_id===focus.id && (v.network_scope||v.target.network_scope||'')===scope && plannerRouteState(v.activity,index)!=null);
}
let routePage=0, routeQuery='', entityQuery='', entityPage=0, routeHistory=false;
function showRawRoutes(root){
 root.innerHTML='<h3>Recorded demand identities at this time</h3><p class="muted">Locate a Zone, GroundStation or satellite, then select a current demand / destination. Only recorded demands exist: this example associates three GS candidates per Zone by static great-circle distance. It does not route every GS to every Zone.</p><details id="route-locator"><summary>Locate entity</summary><input id="route-entity-query" placeholder="Zone, GS or satellite identity"><div id="route-entities"></div></details><p id="route-scope"></p><input id="route-demand-query" placeholder="Search relevant current demands"><label><input id="route-history" type="checkbox"> Include absent / withdrawn demands</label><div id="route-demands"></div><div id="route-outcome"></div>';
 const entities=[...nodes.map((n,i)=>({kind:'node',index:i,label:n.node_id})),...zones.map(z=>({kind:'zone',id:z.zone_id,label:'Zone '+z.zone_id}))];
 const locate=document.getElementById('route-locator');locate.open=!focus;
 const eq=document.getElementById('route-entity-query');eq.value=entityQuery;
 function entityResults(){
  const result=entities.filter(v=>v.label.toLowerCase().includes(entityQuery.toLowerCase())), pages=Math.max(1,Math.ceil(result.length/20));entityPage=Math.min(entityPage,pages-1);
  const out=document.getElementById('route-entities');out.replaceChildren();
  const count=document.createElement('p');count.textContent=`${result.length} matching / ${entities.length} entities · page ${entityPage+1}/${pages}`;out.append(count);
  for(const v of result.slice(entityPage*20,(entityPage+1)*20)){const b=document.createElement('button');b.type='button';b.className='item';b.textContent=v.label;b.onclick=()=>{focus=v;routePage=0;relationFocus=null;routeFocus=null;routeLoadSerial++;render()};out.append(b)}
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
let pairFocus=null,pairPage=0,pairQuery='',pairIncludeUnserved=true,pairRawOpen=false,pairInitialized=false,pairReverse=null,pairSignature='',pairSerial=0,pairRecords=new Map(),pairCache=new Map(),pairError='';
function currentPairRefs(){return pairFocus?plannerPairRefs(pairFocus,frame.zone_assignments,DATA.route_index,pairReverse):null}
function pairOutcome(ref){return ref&&pairRecords.has(ref.key)?plannerRouteState(pairRecords.get(ref.key).timeline,index):null}
function pairEdges(){const refs=currentPairRefs();if(!refs)return[];const out=[];for(const [ref,color,name,dash]of [[refs.forward,'#5ee5e5','GS → Zone',[]],[refs.reverse,'#ffbb65','Zone → GS',[7,3]]]){const value=pairOutcome(ref);if(value?.[0]==='reachable')for(let i=1;i<value[2].length;i++)out.push({a:value[2][i-1],b:value[2][i],color,kind:name,raw:value,route:true,dash})}return out}
function clearPair(){pairFocus=null;pairSignature='';pairRecords.clear();pairCache.clear();pairSerial++;pairError=''}
function selectPair(pair){window.SKYSEED_PLANNER_ROUTE_SHARDS={};pairFocus=pair;pairSignature='';pairPage=0;routeFocus=null;routeLoadSerial++;relationFocus=null;focus={kind:'zone',id:pair.zone_id};render()}
function ensurePairRoutes(){
 if(!pairReverse)pairReverse=plannerReverseIndex(DATA.route_index,nodes);
 if(!pairInitialized){pairInitialized=true;if(DATA.pair_index?.length)pairFocus=DATA.pair_index[0]}
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
 special.innerHTML='<h3>Stable Zone–GS pairs</h3><p>Each network scope has its own serving assignment for the same geographic Zone. Pairs come from recorded typed GS → Zone demands (the example associates three geographic GSs per Zone). Both directions resolve independently; Zone → GS uses the current serving satellite. Scoped pairs keep their recorded scope under Shell selection; missing service remains explicit. Unscoped pairs follow their current serving satellite. Cyan: GS → Zone; dashed orange: Zone → GS.</p><input id="pair-query" placeholder="Find Zone or GroundStation"><label><input id="pair-unserved" type="checkbox"> Include unserved pairs</label><div id="pair-list"></div><div id="pair-outcome"></div><details id="pair-raw"><summary>Raw demands, historical changes and detailed evidence</summary><div id="pair-raw-content"></div></details>';
 const query=document.getElementById('pair-query'),unserved=document.getElementById('pair-unserved');query.value=pairQuery;unserved.checked=pairIncludeUnserved;
 const assignments=new Map(frame.zone_assignments.map(v=>[JSON.stringify([v[2]||'',v[0]]),v[1]])),refsFor=p=>plannerPairRefs(p,assignments,DATA.route_index,pairReverse);
 function servingText(refs){return refs.serving==null?'UNSERVED':node(refs.serving).node_id+' · Shell '+(node(refs.serving).shell_id??'unrecorded')}
 function status(ref){return ref?(plannerRouteState(ref.activity,index)||'absent / withdrawn'):'no recorded demand'}
 function results(){const all=DATA.pair_index||[],rows=all.filter(p=>((p.network_scope||'')+' '+p.zone_id+' '+node(p.ground_station).node_id).toLowerCase().includes(pairQuery.toLowerCase())&&plannerPairVisible(p,refsFor(p),i=>visibility.nodeVisible(i),pairIncludeUnserved,s=>visibility.shellVisible(s)));const pages=Math.max(1,Math.ceil(rows.length/20));pairPage=Math.min(pairPage,pages-1);const out=document.getElementById('pair-list');out.replaceChildren();const count=document.createElement('p');count.textContent=`${rows.length} matching / ${all.length} stable pairs · page ${pairPage+1}/${pages}`;out.append(count);
  for(const pair of rows.slice(pairPage*20,(pairPage+1)*20)){const refs=refsFor(pair),b=document.createElement('button');b.className='item';b.dataset.pairKey=pair.key;b.setAttribute('aria-pressed',String(pairFocus?.key===pair.key));b.textContent=`${pair.network_scope||'unscoped'} · Zone ${pair.zone_id} ↔ ${node(pair.ground_station).node_id} · ${servingText(refs)} · GS → Zone: ${status(refs.forward)} · Zone → GS: ${refs.serving==null?'UNSERVED':status(refs.reverse)}`;b.onclick=()=>selectPair(pair);out.append(b)}
  for(const [label,delta]of [['Previous pairs',-1],['Next pairs',1]]){const b=document.createElement('button');b.textContent=label;b.disabled=pairPage+delta<0||pairPage+delta>=pages;b.onclick=()=>{pairPage+=delta;results()};out.append(b)}
 }
 query.oninput=()=>{pairQuery=query.value;pairPage=0;results()};unserved.onchange=()=>{pairIncludeUnserved=unserved.checked;pairPage=0;results()};results();
 const out=document.getElementById('pair-outcome'),refs=currentPairRefs();out.replaceChildren();
 function line(label,text){const p=document.createElement('p'),strong=document.createElement('strong');strong.textContent=label+': ';p.append(strong,document.createTextNode(text));out.append(p)}
 if(refs){line('Selected pair',`${pairFocus.network_scope||'unscoped'} · Zone ${pairFocus.zone_id} ↔ ${node(pairFocus.ground_station).node_id}`);line('Current service',servingText(refs)+(plannerPairVisible(pairFocus,refs,i=>visibility.nodeVisible(i),pairIncludeUnserved,s=>visibility.shellVisible(s))?'':' · hidden by current filter; selection retained'));
  for(const [label,ref]of [['GS → Zone',refs.forward],['Zone → GS',refs.reverse]]){const value=pairOutcome(ref);line(label,!ref?(refs.serving==null?'UNSERVED; no current serving-satellite demand':'No recorded directional demand'):!pairRecords.has(ref.key)?pairError||'Loading recorded outcome…':!value?'Absent / withdrawn at this time':value[0]==='reachable'?'REACHABLE · '+value[2].map(i=>(visibility.nodeVisible(i)?'':'[hidden] ')+node(i).node_id).join(' → '):'UNREACHABLE · '+value[1]);
   if(value?.[0]==='unreachable'&&value[1]==='NO_OPERATIONAL_PATH'){const why=plannerAccessExplanation(refs,pairFocus,frame,nodes);if(why)line('Retained evidence',why)}
  }
 }else line('Selection','Choose a fixed Zone–GS pair.');
 const raw=document.getElementById('pair-raw'),rawContent=document.getElementById('pair-raw-content');let rawLoaded=false;
 function rawEvidence(){if(rawLoaded)return;rawLoaded=true;showRawRoutes(rawContent);
  if(refs)for(const [label,ref]of [['GS → Zone',refs.forward],['Zone → GS',refs.reverse]]){const record=ref&&pairRecords.get(ref.key);if(!record)continue;const detail=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent=label+' · typed identity and full recorded history';pre.textContent=JSON.stringify({key:ref.key,demand:record.demand,timeline:record.timeline},null,2);detail.append(summary,pre);rawContent.append(detail)}
 }
 raw.open=pairRawOpen;raw.ontoggle=()=>{pairRawOpen=raw.open;if(raw.open)rawEvidence()};if(raw.open)rawEvidence();
}
'''
