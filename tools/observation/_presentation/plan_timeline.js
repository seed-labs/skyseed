/* A file:// viewer can load a sibling classic script without a web server. */
function planTimeline(windows, inline) {
  const slider = byId('timeline'), play = byId('play'), select = byId('planWindow');
  const query = new URLSearchParams(location.search);
  let data = inline, current = inline ? 0 : -1, local = 0, request = 0, timer, playing = false;
  const sections = windows || [{start:inline.start,end:inline.end,times:inline.frames.map(f=>f.timestamp)}];
  const times = [], owners = [], offsets = [];
  for (const [i, w] of sections.entries()) {
    const option = document.createElement('option');
    option.value = String(i); option.textContent = 'Plan '+(i+1)+' · '+w.start+' – '+w.end;
    select.append(option); offsets.push(times.length);
    for (const time of w.times) { times.push(time); owners.push(i); }
  }
  if (!windows) select.parentElement.hidden=true;
  if (query.has('window') && sections[Number(query.get('window'))]) select.value=String(Number(query.get('window')));
  function limits() {
    const i=Number(select.value);
    slider.min = select.value === 'all' ? 0 : offsets[i];
    slider.max = select.value === 'all' ? times.length-1 : offsets[i]+sections[i].times.length-1;
    play.disabled = slider.disabled = slider.min === slider.max;
  }
  limits();
  const stamp=query.get('timestamp');
  const first=stamp ? times.findIndex(t=>Date.parse(t)>=Date.parse(stamp)) : 0;
  slider.value = Math.min(Number(slider.max),Math.max(Number(slider.min),first<0 ? times.length-1 : first));

  const pending = new Map();
  function load(i) {
    if (!pending.has(i)) pending.set(i,new Promise((resolve,reject)=>{
      const script=document.createElement('script'); script.src=sections[i].src;
      script.onload = async () => {
        try {
          const bytes = Uint8Array.from(atob(script.plan), c => c.charCodeAt(0));
          const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
          resolve(await new Response(stream).json());
        } catch (error) { reject(error); }
        finally { script.remove(); }
      };
      script.onerror=()=>{script.remove(); reject(new Error('Cannot load '+sections[i].src));};
      document.head.append(script);
    }).finally(()=>pending.delete(i)));
    return pending.get(i);
  }
  let onData, draw;
  async function move() {
    const token=++request, index=Number(slider.value), i=owners[index];
    if (i!==current) {
      byId('timestamp').textContent='加载 / Loading Plan '+(i+1);
      const part=await load(i);
      if (token!==request) return;
      data=part; current=i;
      if (onData) onData(data);
    }
    local=index-offsets[i];
    if (draw) draw();
  }
  function stop() {playing=false; clearTimeout(timer); play.textContent='播放 / Play';}
  function failed(e) {stop(); byId('timestamp').textContent=e.message;}
  async function step() {
    if (!playing) return;
    slider.value=Number(slider.value)>=Number(slider.max) ? slider.min : Number(slider.value)+1;
    try { await move(); } catch(e) {failed(e);return;}
    if (playing) timer=setTimeout(step,650);
  }
  return {
    open: async()=>{ await move(); return data; },
    index:()=>local,
    seek:timestamp=>{
      const idx=data.frames.findIndex(f=>Date.parse(f.timestamp)>=Date.parse(timestamp));
      local=idx<0?data.frames.length-1:idx; slider.value=offsets[current]+local; draw();
    },
    bind:(prepare,update)=>{
      onData=prepare; draw=update;
      slider.oninput=()=>move().catch(failed);
      select.onchange=()=>{limits(); if(select.value!=='all') slider.value=slider.min; move().catch(failed);};
      play.onclick=()=>{if(playing) stop(); else {playing=true;play.textContent='暂停 / Pause';timer=setTimeout(step,650);}};
    },
  };
}
