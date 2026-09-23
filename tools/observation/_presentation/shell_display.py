"""Shared private display controls over typed static Tool metadata."""

_SHELL_CONTROLS = '''<fieldset id="shell-controls"><legend>Shell visibility · one shared world</legend>
<div id="shell-options" style="display:flex;gap:12px;flex-wrap:wrap"></div>
<button type="button" id="shell-all">Select all Shells</button>
<label><input id="shared-gs" type="checkbox" checked> Shared GroundStations</label>
<label><input id="shared-zones" type="checkbox" checked> Shared Zones</label>
<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
<label>Orbit Shell <select id="orbit-shell"><option value="">All Shells / all orbits</option></select></label>
<label>Orbit <select id="orbit-select" disabled><option value="">All orbits</option></select></label>
<button type="button" id="orbit-reset">Reset orbit filter</button></div>
<p id="shell-counts" class="muted"></p><p id="shell-focus-note" role="status"></p>
</fieldset>'''

_SHELL_SCRIPT = r'''
function createShellVisibility(nodes, zoneCount, changed) {
 const colors={S1:'#58c4f6',S2:'#ef92d0',S3:'#a9df78',S4:'#ffbb65',S5:'#b7a2ff'};
 const palette=['#58c4f6','#ef92d0','#a9df78','#ffbb65','#b7a2ff'];
 const shells=[...new Set(nodes.filter(n=>n.kind==='satellite'&&n.shell_id).map(n=>n.shell_id))].sort();
 const selected=new Set(shells), options=document.getElementById('shell-options'), checks=new Map();
 const orbitShell=document.getElementById('orbit-shell'), orbitSelect=document.getElementById('orbit-select');
 let orbitOwner='', orbit='';
 const owners=new Map();
 for(const n of nodes)if(n.kind==='satellite'&&n.asn!=null&&n.shell_id&&n.orbit_id){const key=JSON.stringify([n.asn,n.shell_id]);if(!owners.has(key))owners.set(key,{asn:n.asn,shell:n.shell_id,orbits:new Set()});owners.get(key).orbits.add(n.orbit_id)}
 for(const [key,v]of owners){const option=document.createElement('option');option.value=key;option.textContent=`AS${v.asn} · ${v.shell}`;orbitShell.append(option)}
 function orbitVisible(n){return !orbitOwner||n.kind!=='satellite'||(JSON.stringify([n.asn,n.shell_id])===orbitOwner&&(!orbit||n.orbit_id===orbit))}
 const gs=document.getElementById('shared-gs'), zones=document.getElementById('shared-zones');
 function color(shell){if(colors[shell])return colors[shell];let h=0;for(const c of shell||'')h=(h*31+c.charCodeAt(0))>>>0;return palette[h%palette.length]}
 function nodeVisible(i){const n=nodes[i];return !!n&&(n.kind==='ground-station'?gs.checked:n.shell_id?selected.has(n.shell_id)&&orbitVisible(n):!orbitOwner)}
 function edgeVisible(e){return(e.a==null?zones.checked:nodeVisible(e.a))&&nodeVisible(e.b)}
 function update(){for(const [s,c]of checks)c.checked=selected.has(s);counts();changed()}
 function counts(){const visible=nodes.filter((_,i)=>nodeVisible(i)).length;const p=document.getElementById('shell-counts');p.textContent=`Visible / global nodes: ${visible} / ${nodes.length}; Zones: ${zones.checked?zoneCount:0} / ${zoneCount}. Geometry uses actual ECEF and altitude.`;p.dataset.visibleNodes=visible;p.dataset.globalNodes=nodes.length}
 function focus(indices=[],zone=false){const hidden=indices.filter(i=>!nodeVisible(i)).length+(zone&&!zones.checked?1:0);document.getElementById('shell-focus-note').textContent=hidden?`Focused entity/path has ${hidden} hidden endpoint(s). Its evidence remains available; hidden segments are not drawn.`:''}
 for(const shell of shells){const label=document.createElement('label'),input=document.createElement('input'),solo=document.createElement('button');input.type='checkbox';input.checked=true;input.dataset.shell=shell;input.onchange=()=>{if(input.checked)selected.add(shell);else selected.delete(shell);update()};label.style.color=color(shell);label.append(input,document.createTextNode(' '+shell+' '));solo.type='button';solo.textContent='Solo '+shell;solo.dataset.shellSolo=shell;solo.onclick=()=>{selected.clear();selected.add(shell);update()};options.append(label,solo);checks.set(shell,input)}
 if(!shells.length)options.textContent='Typed Shell metadata unavailable; shared filters remain usable.';
 document.getElementById('shell-all').onclick=()=>{shells.forEach(s=>selected.add(s));update()};gs.onchange=zones.onchange=update;
 orbitShell.onchange=()=>{orbitOwner=orbitShell.value;orbit='';orbitSelect.replaceChildren();const all=document.createElement('option');all.value='';all.textContent='All orbits';orbitSelect.append(all);for(const id of [...(owners.get(orbitOwner)?.orbits||[])].sort()){const o=document.createElement('option');o.value=id;o.textContent=id;orbitSelect.append(o)}orbitSelect.disabled=!orbitOwner;update()};
 orbitSelect.onchange=()=>{orbit=orbitSelect.value;update()};
 document.getElementById('orbit-reset').onclick=()=>{orbitShell.value='';orbitShell.onchange()};counts();
 return{nodeVisible,edgeVisible,zoneVisible:()=>zones.checked,groundVisible:()=>gs.checked,color,counts,focus,shellVisible:s=>selected.has(s)&&(!orbitOwner||owners.get(orbitOwner)?.shell===s),orbitVisible,shells};
}
'''
