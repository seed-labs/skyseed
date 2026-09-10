"""Shared display-only interaction policy for spatial inspection canvases."""

from __future__ import annotations


def spatial_interaction_policy() -> dict[str, object]:
    """Return non-semantic camera controls shared by repository Tools."""

    return {
        "globe": {
            "initial_yaw_radians": -0.55,
            "initial_pitch_radians": 0.28,
            "minimum_zoom": 0.55,
            "maximum_zoom": 5.0,
            "wheel_sensitivity": 0.0015,
        },
        "map": {
            "minimum_zoom": 1.0,
            "maximum_zoom": 12.0,
            "wheel_sensitivity": 0.0015,
        },
        "selection_radius_css_pixels": 9,
        "drag_threshold_css_pixels": 4,
        "semantics": (
            "camera, pan, zoom, focus, and display projection are Tool-local "
            "and never change authoritative ECEF or planning evidence"
        ),
    }


__all__ = ["spatial_interaction_policy"]


def _evidence_globe_script():
    """Private canvas camera shared by evidence views; never computes geometry."""
    from tools._earth_reference import _natural_earth_coastline_lines
    from tools._display_geometry import display_geometry_policy
    from tools._html import json_for_script
    return ('const EVIDENCE_COAST=' + json_for_script(_natural_earth_coastline_lines())
            + ',EVIDENCE_RADIUS=' + str(display_geometry_policy()['earth_reference_radius_m']) + ';\n' + r'''
function createEvidenceGlobe(canvas,nodes,visibility){
 const x=canvas.getContext('2d');let yaw=-.55,pitch=.28,zoom=1,drag=null,frame=null,edges=[],markers=[],highlight=null,by=new Map();
 function project(v,w,h){const R=EVIDENCE_RADIUS,a=v[1]/R,b=v[3]/R,c=v[2]/R,cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch),u=a*cy+c*sy,z=-a*sy+c*cy,s=Math.min(w,h)*.36*zoom;return[w/2+u*s,h/2-(b*cp-z*sp)*s,b*sp+z*cp]}
 function surface(lat,lon){lat*=Math.PI/180;lon*=Math.PI/180;const R=EVIDENCE_RADIUS;return[0,R*Math.cos(lat)*Math.cos(lon),R*Math.cos(lat)*Math.sin(lon),R*Math.sin(lat)]}
 function draw(){const box=canvas.getBoundingClientRect(),w=box.width,h=Math.max(280,box.height),d=Math.max(1,devicePixelRatio||1);canvas.width=w*d;canvas.height=h*d;x.setTransform(d,0,0,d,0,0);x.clearRect(0,0,w,h);x.fillStyle='#0c3047';x.beginPath();x.arc(w/2,h/2,Math.min(w,h)*.36*zoom,0,Math.PI*2);x.fill();x.strokeStyle='#9bb5c955';x.lineWidth=.7;
  for(const shape of EVIDENCE_COAST){x.beginPath();let started=false;for(const [lat,lon]of shape){const p=project(surface(lat,lon),w,h);if(p[2]<0){started=false;continue}if(started)x.lineTo(p[0],p[1]);else{x.moveTo(p[0],p[1]);started=true}}x.stroke()}
  if(!frame)return;
  for(const e of edges){if(!visibility.edgeVisible(e))continue;const a=by.get(e.a),b=by.get(e.b);if(!a||!b)continue;const pa=project(a,w,h),pb=project(b,w,h);if(pa[2]<0||pb[2]<0)continue;x.strokeStyle=e.color;x.fillStyle=e.color;x.lineWidth=e.width||2.5;x.setLineDash(e.dash||[]);x.beginPath();x.moveTo(pa[0],pa[1]);x.lineTo(pb[0],pb[1]);x.stroke();x.setLineDash([]);const dx=pb[0]-pa[0],dy=pb[1]-pa[1],angle=Math.atan2(dy,dx),cx=pa[0]+dx*.65,cy=pa[1]+dy*.65;x.beginPath();x.moveTo(cx,cy);x.lineTo(cx-7*Math.cos(angle-.45),cy-7*Math.sin(angle-.45));x.lineTo(cx-7*Math.cos(angle+.45),cy-7*Math.sin(angle+.45));x.closePath();x.fill()}
  for(const p of frame.positions){if(!visibility.nodeVisible(p[0]))continue;const q=project(p,w,h);if(q[2]<0)continue;const active=p[0]===highlight,n=nodes[p[0]];x.fillStyle=active?'#fff':n.kind==='ground-station'?'#ffd166':visibility.color(n.shell_id);x.beginPath();x.arc(q[0],q[1],active?5:n.kind==='ground-station'?2.6:1.8,0,Math.PI*2);x.fill()}
  for(const marker of markers){if(!visibility.edgeVisible(marker))continue;const a=by.get(marker.a),b=by.get(marker.b);if(!a||!b)continue;const pa=project(a,w,h),pb=project(b,w,h);if(pa[2]<0||pb[2]<0)continue;const t=Math.max(0,Math.min(1,marker.progress));x.fillStyle=marker.color;x.beginPath();x.arc(pa[0]+(pb[0]-pa[0])*t,pa[1]+(pb[1]-pa[1])*t,5,0,Math.PI*2);x.fill()}
  canvas.dataset.frame=frame.timestamp;canvas.dataset.visibleEdges=edges.filter(e=>visibility.edgeVisible(e)).length;canvas.dataset.markers=JSON.stringify(markers);
 }
 canvas.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId)};canvas.onpointermove=e=>{if(!drag)return;yaw+=(e.clientX-drag.x)*.008;pitch=Math.max(-1.55,Math.min(1.55,pitch+(e.clientY-drag.y)*.008));drag={x:e.clientX,y:e.clientY};draw()};canvas.onpointerup=canvas.onpointercancel=()=>{drag=null};canvas.onwheel=e=>{e.preventDefault();zoom=Math.max(.55,Math.min(5,zoom*Math.exp(-e.deltaY*.0015)));draw()};window.addEventListener('resize',draw);
 return {setFrame(value){frame=value;by=new Map(value.positions.map(p=>[p[0],p]));draw()},setOverlay(value,node,moving=[]){edges=value;highlight=node;markers=moving;draw()},focus(node){const p=by.get(node);if(!p)return;const lat=Math.atan2(p[3],Math.hypot(p[1],p[2])),lon=Math.atan2(p[2],p[1]);yaw=Math.PI/2-lon;pitch=-lat;draw()},reset(){yaw=-.55;pitch=.28;zoom=1;draw()},draw};
}
''')
