/* Presentation only: supplied schematic geometry or actual Physical snapshots. */
const byId = id => document.getElementById(id);
const earthRadius = 6378137;
const coastline = /*COASTLINE_DATA*/null;
const palette = ['#83a8ff', '#b89cf5', '#6edbd2', '#f1a0b6', '#f0cf82', '#89d6a1'];
const spherePoint = ([lat, lon]) => {
  const a = lat * Math.PI / 180, b = lon * Math.PI / 180;
  return [earthRadius * Math.cos(a) * Math.cos(b), earthRadius * Math.cos(a) * Math.sin(b), earthRadius * Math.sin(a)];
};
const coastPoints = coastline.lines.map(line => line.map(spherePoint));
const gridPoints = [];
for (let lat = -60; lat <= 60; lat += 30)
  gridPoints.push(Array.from({length:121}, (_, i) => spherePoint([lat, i * 3 - 180])));
for (let lon = -180; lon < 180; lon += 30)
  gridPoints.push(Array.from({length:61}, (_, i) => spherePoint([i * 3 - 90, lon])));

class InspectionGlobe {
  constructor(canvas, selectNode, selectLine = () => {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.yaw = -116 * Math.PI / 180;
    this.tilt = 25 * Math.PI / 180;
    this.points = [];
    this.links = [];
    this.outlines = [];
    this.geographic = true;
    this.hits = [];
    let drag = null;
    canvas.onpointerdown = event => {
      drag = {x:event.clientX, y:event.clientY, moved:false};
      canvas.setPointerCapture(event.pointerId);
    };
    canvas.onpointermove = event => {
      if (!drag) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      drag.moved ||= Math.abs(dx) + Math.abs(dy) > 2;
      this.yaw += dx * .008;
      this.tilt = Math.max(-1.5, Math.min(1.5, this.tilt + dy * .008));
      drag.x = event.clientX; drag.y = event.clientY;
      this.draw();
    };
    canvas.onpointerup = event => {
      if (drag && !drag.moved) {
        const box = canvas.getBoundingClientRect();
        const x = event.clientX - box.left, y = event.clientY - box.top;
        const hit = this.hits.find(p => Math.hypot(p.x - x, p.y - y) < 10);
        if (hit) selectNode(hit.id);
        else {
          let closest = null, distance = 7;
          for (const segment of this.segments) {
            const [a, b] = segment.points, dx = b[0]-a[0], dy = b[1]-a[1];
            const t = Math.max(0, Math.min(1, ((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy || 1)));
            const d = Math.hypot(x-a[0]-t*dx, y-a[1]-t*dy);
            if (d < distance) { closest = segment.feature; distance = d; }
          }
          if (closest) selectLine(closest);
        }
      }
      drag = null;
    };
    canvas.onpointercancel = () => drag = null;
    window.addEventListener('resize', () => this.draw());
  }
  reset() { this.yaw = -116 * Math.PI / 180; this.tilt = 25 * Math.PI / 180; this.draw(); }
  set(points, links = [], {outlines = [], geographic = true} = {}) {
    this.points = points; this.links = links;
    this.outlines = outlines; this.geographic = geographic;
    // Fit the supplied positions, including high orbits; rotation does not rescan them.
    this.extent = points.reduce((radius, point) => Math.max(radius, Math.hypot(...point.position)), earthRadius * 1.16);
    for (const path of outlines) for (const point of path.points)
      this.extent = Math.max(this.extent, Math.hypot(...point));
    this.draw();
  }
  project(p) {
    const x = Math.cos(this.yaw) * p[0] - Math.sin(this.yaw) * p[1];
    const y = Math.sin(this.yaw) * p[0] + Math.cos(this.yaw) * p[1];
    return [this.width / 2 + y * this.scale,
      this.height / 2 - (Math.cos(this.tilt) * p[2] - Math.sin(this.tilt) * x) * this.scale,
      Math.cos(this.tilt) * x + Math.sin(this.tilt) * p[2]];
  }
  visible(p) { return p[2] >= 0 || Math.hypot(p[0] - this.width/2, p[1] - this.height/2) > earthRadius * this.scale; }
  line(a, b, color, width = 1) {
    const c = this.ctx;
    c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]);
    c.strokeStyle = color; c.lineWidth = width; c.stroke();
  }
  path(points, color, width, feature) {
    let last = null;
    for (const position of points) {
      const p = this.project(position);
      if (last && this.visible(last) && this.visible(p)) {
        this.line(last, p, color, width);
        if (feature) this.segments.push({points:[last, p], feature});
      }
      last = p;
    }
  }
  draw() {
    const c = this.ctx, canvas = this.canvas, ratio = window.devicePixelRatio || 1;
    this.width = canvas.clientWidth; this.height = canvas.clientHeight;
    this.scale = Math.min(this.width, this.height) * .43 / (this.extent || earthRadius * 1.16);
    const radius = earthRadius * this.scale;
    canvas.width = Math.round(this.width * ratio); canvas.height = Math.round(this.height * ratio);
    c.setTransform(ratio, 0, 0, ratio, 0, 0);
    const gradient = c.createRadialGradient(this.width/2-radius*.3, this.height/2-radius*.3, 5, this.width/2, this.height/2, radius);
    gradient.addColorStop(0, '#24475b'); gradient.addColorStop(1, '#102a3c');
    c.fillStyle = gradient; c.beginPath(); c.arc(this.width/2, this.height/2, radius, 0, 2*Math.PI); c.fill();
    for (const [paths, color] of [[gridPoints, '#294555'], [this.geographic ? coastPoints : [], '#7794a0']]) {
      for (const path of paths) {
        let last = null;
        for (const position of path) {
          const p = this.project(position);
          if (last && p[2] > 0 && last[2] > 0) this.line(last, p, color, .7);
          last = p;
        }
      }
    }
    this.segments = [];
    for (const outline of this.outlines) this.path(outline.points, outline.color, outline.width, outline.feature);
    for (const link of this.links) {
      // Display clipping samples only; these do not represent simulated states.
      const samples = Array.from({length:13}, (_, i) => link.a.map((v, j) => v + (link.b[j]-v)*i/12));
      this.path(samples, link.color, link.width, link.feature);
    }
    this.hits = [];
    for (const point of this.points) {
      const p = this.project(point.position);
      if (!this.visible(p)) continue;
      c.fillStyle = point.color; c.beginPath(); c.arc(p[0], p[1], point.radius, 0, 2*Math.PI); c.fill();
      this.hits.push({id:point.id, x:p[0], y:p[1]});
      if (point.label) { c.fillStyle = '#e5edf4'; c.font = '12px system-ui'; c.fillText(point.label, p[0]+9, p[1]-9); }
    }
  }
}

function sceneIndex(data) {
  const groups = new Map(data.groups.map(g => [g.group_id, g]));
  const nodes = new Map(data.nodes.map(n => [n.node_id, n]));
  const members = new Map(data.groups.map(g => [g.group_id, []]));
  const children = new Map([[null, []], ...data.groups.map(g => [g.group_id, []])]);
  for (const group of data.groups) children.get(group.parent_id).push(group);
  for (const node of data.nodes)
    for (let id = node.group_id; id !== null; id = groups.get(id).parent_id) members.get(id).push(node.node_id);
  const ordered = [];
  function visit(parent, depth) { for (const group of children.get(parent)) { ordered.push({group, depth}); visit(group.group_id, depth+1); } }
  visit(null, 0);
  return {groups, nodes, members, ordered};
}

function showFacts(target, facts) {
  target.replaceChildren();
  for (const [name, value] of facts) {
    const row = document.createElement('div'), key = document.createElement('span'), text = document.createElement('span');
    key.textContent = name; text.textContent = value; row.append(key, text); target.append(row);
  }
}

function bindTimeline(frames, update) {
  const slider = byId('timeline'), play = byId('play');
  slider.max = Math.max(0, frames.length - 1);
  slider.disabled = play.disabled = frames.length < 2;
  slider.oninput = update;
  let timer = null;
  play.onclick = () => {
    if (timer) { clearInterval(timer); timer = null; play.textContent = '播放 / Play'; }
    else {
      play.textContent = '暂停 / Pause';
      timer = setInterval(() => { slider.value = (Number(slider.value)+1) % frames.length; update(); }, 650);
    }
  };
}
