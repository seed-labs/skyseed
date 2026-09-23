"""Private Area display helpers for the ordinary Planner maps and route drawer."""

_OSPF_SCRIPT = r'''
function plannerOspfIndex(value){
 if(!value)return null;
 const links=new Map();for(const [a,b,area,ab,ba,kind]of value.links){links.set(a+':'+b,{area,cost:ab,kind});links.set(b+':'+a,{area,cost:ba,kind})}
 const areas=value.links.filter(v=>v[5]==='inter-satellite').map(([a,b,area])=>({a,b,color:plannerOspfColor(area),kind:'OSPF operational ISL',context:true}));
 return {links,areas,members:new Map(value.memberships.map(([n,areas,active])=>[n,{areas,active}])),abrs:new Set(value.abrs)};
}
function plannerOspfRole(kind,areas){return kind==='ground-station'?'Business terminal (no OSPF routing or transit)':areas.length>1?'ABR':areas[0]===0?'Backbone-only router':areas.length?'Ordinary-area router':'No enabled area'}
function plannerOspfColor(area){return area==null?'#8899aa':area===0?'#f1f5ff':['#bb8cff','#65d6ad','#ffad66','#ec85b5','#6bafff','#d6d56a'][(area-1)%6]}
function plannerOspfEdges(edges,view){
 const shown=new Set(),styled=edges.map(e=>{const link=view.links.get(e.a+':'+e.b);if(e.route||e.dash||link?.kind!=='inter-satellite')return e;shown.add(Math.min(e.a,e.b)+':'+Math.max(e.a,e.b));return {...e,color:plannerOspfColor(link.area)}});
 return [...view.areas.filter(e=>!shown.has(Math.min(e.a,e.b)+':'+Math.max(e.a,e.b))),...styled];
}
function plannerOspfPath(path,view){
 if(!view)return null;
 const segments=[];let total=0;
 for(let i=1;i<path.length;i++){const a=path[i-1],b=path[i],link=view.links.get(a+':'+b);if(!link)return null;segments.push({a,b,...link});total+=link.cost}
 return {total,segments};
}
function appendOspfRoute(root,path,metadata,view,nodes){
 const value=plannerOspfPath(path,view);if(!metadata||!value)return;
 const p=document.createElement('p');p.textContent=`${metadata.metric}: ${value.total} ${metadata.units} · ${metadata.basis}`;root.append(p);
 const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='Recorded interface costs and Areas';
 pre.textContent=value.segments.map(v=>`${nodes[v.a].display_name||nodes[v.a].node_id} → ${nodes[v.b].display_name||nodes[v.b].node_id}: ${v.cost} ${metadata.units}; ${v.kind==='ground-station-access'?'terminal attachment; ':''}Area ${Array.isArray(v.area)?'{'+v.area.join(', ')+'}':v.area??'excluded'}`).join('\n');
 details.append(summary,pre);root.append(details);
}
'''
