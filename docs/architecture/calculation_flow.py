"""Single editable source for calculation-flow.svg (Python standard library only).

Run from skyseed: python docs/architecture/calculation_flow.py
The explicit layout separates algorithm hooks, framework work and ownership.
Arrows show value dependencies, not seven sequential algorithm calls.
"""
from html import escape
from pathlib import Path

W, H = 1740, 2795
parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title description">
<title id="title">SkySEED calculation flow and replaceable Model hooks</title>
<desc id="description">Scene adaptation supplies pure Simulator input. Producer advances time and calls Simulator's physical side and Planner, then the backend Compiler. Position and PhysicalLink produce observations; InterSatellite, GroundStation and ZoneService make different decisions from those observations. Planner handles acquisition; Parameter supplies effective connections; Route consumes connections, service and demands. Planner adopts state and produces Plans. Producer packages and seals materials, and Controller publishes completed requests. Seven blue Model cards are replaceable through existing constructor injection.</desc>
<defs>
 <marker id="data" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#176a9d"/></marker>
 <marker id="control" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9" fill="none" stroke="#7952a3"/></marker>
</defs>
<style>
 text {{font-family:Arial,Helvetica,sans-serif;fill:#183245}}
 .heading {{font-size:29px;font-weight:bold}}
 .title {{font-size:23px;font-weight:bold}}
 .body {{font-size:19px}}
 .caption {{font-size:18px;fill:#456274}}
 .label {{font-size:17px;paint-order:stroke;stroke:#f5f8fb;stroke-width:6px;stroke-linejoin:round}}
</style>
<rect width="100%" height="100%" fill="#f5f8fb"/>
''']


def text(x, y, value, css='body'):
    parts.append(f'<text x="{x}" y="{y}" class="{css}">{escape(value)}</text>')


def card(x, y, w, h, title, lines, *, model=False, fill=None):
    color = fill or ('#e5f2fc' if model else '#fff')
    stroke = '#176a9d' if model else '#8099aa'
    parts.append(f'<g><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{color}" stroke="{stroke}" stroke-width="1.5"/>')
    text(x+20, y+34, title, 'title')
    parts.append(f'<path d="M{x+20},{y+49} H{x+w-20}" stroke="{stroke}" opacity=".35"/>')
    for i, line in enumerate(lines):
        text(x+20, y+79+28*i, line)
    parts.append('</g>')


def group(x, y, w, h, title, subtitle):
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="#edf4f0" stroke="#285a49" stroke-width="2"/>')
    text(x+20, y+35, title, 'title')
    text(x+20, y+64, subtitle, 'caption')


def edge(points, *, control=False, label=None, at=None):
    kind = 'control' if control else 'data'
    stroke = '#7952a3' if control else '#176a9d'
    dash = ' stroke-dasharray="8 5"' if control else ''
    parts.append(f'<polyline points="{points}" fill="none" stroke="{stroke}" stroke-width="2.2" marker-end="url(#{kind})"{dash}/>')
    if label:
        text(*at, label, 'label')


text(40, 48, 'SkySEED  /  CALCULATION', 'caption')
text(40, 88, 'Replace the algorithms; keep progression and ownership explicit', 'heading')
text(40, 125, 'Blue cards = replaceable Models. Green nesting = ownership. Arrows = value dependencies; dashed arrows = orchestration.', 'caption')

card(40, 160, 500, 200, 'Scene construction', [
    'Shells, orbital elements and device capabilities',
    'GroundStation sites and access capacities',
    'SEED Emulator + SkySEED entities',
    'No deployment is needed to calculate'])
card(610, 160, 500, 200, 'External adapter', [
    'create_simulator_static_input(systems)',
    'Copies receiver-defined static facts',
    'Result: SimulatorStaticInput',
    'SEED objects stay outside Simulator'])
card(1180, 160, 500, 200, 'Assemble the Simulator', [
    'Definition = static input + finite Timeline',
    'Supply Zone layout and Model instances',
    'Each hook: built-in default OR custom object',
    'create_default_simulator(...)'])
edge('540,260 610,260')
edge('1110,260 1180,260')

card(40, 415, 1640, 120, 'Producer orchestrates the requested calculation', [
    'One formal cursor: ask for frame(t), query observations, advance Planner, then call backend compilation at the Compiler cutoff.',
    'Prehistory may warm acquisition. A search expansion stays at the same t. Retained continuation advances only the new suffix.'], fill='#f1ecf8')

group(40, 590, 1640, 1695, 'Simulator',
      'Owns PhysicalWorld, the PhysicalLink Model and Planner. World identities and physical facts remain available to their consumers.')
edge('290,535 290,590', control=True, label='advance t', at=(305, 574))
edge('1500,360 1500,385 1710,385 1710,615 1680,615', label='pure inputs', at=(1540, 380))
group(65, 685, 510, 265, 'PhysicalWorld',
      'Nodes, orbits, sites and optional ZoneLayout')
card(85, 765, 470, 165, 'Position · positions at time t', [
    'In: World + requested timestamp',
    'Out: complete NodeId → ECEF mapping',
    'World returns an immutable position snapshot'], model=True)
card(620, 700, 500, 230, 'PhysicalLink · potential connections', [
    'In: World + position snapshot + request',
    'Out: ISL, GS and Zone candidate observations',
    'Carries available physical measurements',
    'Feasible candidates are not selected links',
    'Same timestamp as the supplied snapshot'], model=True)
card(1180, 685, 460, 245, 'Planner supplies the query scope', [
    'PhysicalLinkRequest: pairs and search scope',
    'Uses InterSatellite potential-pair enumeration',
    'Includes retained / preparing pairs',
    'Optional GS search advice can widen it',
    'Producer repeats a query only as needed'])
edge('575,845 620,845')
edge('1180,810 1120,810')
card(70, 985, 1570, 100, 'PhysicalLinkQueryResult at t', [
    'Separate candidate sets: satellite ↔ satellite  |  ground station ↔ satellite  |  Zone → possible serving satellites'])
edge('870,930 870,985')

group(60, 1120, 1600, 1100, 'Planner · algorithm decisions inside framework progression',
      'The three selection roles consume different candidate sets and prior decision context; one does not call the next.')
card(80, 1210, 480, 210, 'InterSatellite · satellite connections', [
    'In: World + ISL candidates + selection state',
    'Out: selected satellite candidate pairs',
    'Also enumerates potential pairs for queries',
    'Selection does not bypass acquisition'], model=True)
card(620, 1210, 500, 210, 'GroundStation · ground access', [
    'In: World + GS candidates + selection state',
    'Out: selected ground-access candidates',
    'Framework checks identities and capacities',
    'Preparation can precede operational access'], model=True)
card(1180, 1210, 460, 210, 'ZoneService · serving satellites', [
    'In: World + Zone candidates + prior service',
    'Out: service assignments and changes',
    'An assignment can be explicitly unserved',
    'Service choice is not geographic membership'], model=True)
# Fan out physical observations; this is not an ISL -> GS -> Zone call chain.
edge('1640,1030 1670,1030 1670,1195 320,1195 320,1210')
edge('870,1195 870,1210')
edge('1410,1195 1410,1210')

card(80, 1490, 480, 210, 'Framework · acquisition handling', [
    'Retain original selection start times',
    'Reserve capacity for preparing connections',
    'Advance ISL / GS lifecycle at formal t',
    'Out: candidates operational at this time'])
edge('320,1420 320,1490')
edge('870,1420 870,1450 460,1450 460,1490')
card(620, 1490, 500, 230, 'Parameter · effective connections', [
    'In: operational candidate observations,',
    '      timestamp + previously adopted memory',
    'Out: connections with effective parameters',
    '      and next opaque parameter memory',
    'Bandwidth, delay and loss follow the Model'], model=True)
edge('560,1600 620,1600')

card(1180, 1745, 460, 230, 'Route · paths or local arrangements', [
    'In: World + effective connections, service,',
    '      demands + previous outcomes at t',
    'Out: paths / unreachability OR local choices',
    '      with policy data; partial / empty allowed',
    'Demands: supplied or optional Model factory'], model=True)
edge('1120,1600 1150,1600 1150,1860 1180,1860')
edge('1410,1420 1410,1745', label='service assignments', at=(1425, 1600))
card(80, 1780, 500, 195, 'Between observations · same owners', [
    'Adopted state feeds the next decisions',
    'Acquisition reservations and Model memory',
    'remain private to their live owners',
    'They are not copied into operational state'])

card(80, 2040, 1560, 155, 'Framework · state adoption, Transitions and Plans', [
    'Adopt effective connections, Zone service and route outcomes as current PlannedOperationalState; adopt parameter memory separately.',
    'Derive changes and required semantic event dependencies in PlanTransition; group ordered Transitions into lineage-bearing Plans.',
    'Planner publishes complete Plan prefixes. A request flush preserves the next timestamp and the same live Models.'])
edge('870,1720 870,2040')
edge('1410,1975 1410,2040')
# Zone assignments are state in their own right, even when there are no route demands.
edge('1580,1420 1580,1460 1670,1460 1670,2120 1640,2120')

card(60, 2360, 755, 200, 'Backend Compiler · lowering, not another Model', [
    'Producer calls compile_plan with the assembly backend',
    'Consumes the Plan and its event dependencies',
    'Out: CompilationResult with backend execution intents',
    'Does not rerun route selection or own request publication'])
edge('440,2195 440,2360', label='Plans', at=(455, 2322))
card(925, 2360, 755, 200, 'Producer + Controller · packaging and publication', [
    'Producer appends compilation results, drains accepted work',
    'Selected backend prepares executable actions and foundation',
    'Controller publishes completed request / bounded prefixes',
    'Published results remain readable while computation waits'])
edge('815,2460 925,2460', label='results', at=(835, 2440))
card(925, 2620, 755, 120, 'Delivery to separate execution owners', [
    'Sealed Runner materials + completed calculation evidence',
    'Deployment and Runner execution require explicit operations'])
edge('1300,2560 1300,2620')
text(60, 2636, 'Optional capabilities, not universal requirements', 'title')
text(60, 2672, 'ZoneMembership is a lower-level capability inside ZoneLayout.', 'body')
text(60, 2701, 'Prediction, wider search and reuse require explicit support.', 'body')
text(60, 2730, 'Mix Models only when their consumers’ input contracts match.', 'body')
text(40, 2774, 'Model authors choose algorithms and private representation. Existing owners keep time, validation, adoption, publication and lifecycle.', 'caption')
parts.append('</svg>\n')
Path(__file__).with_name('calculation-flow.svg').write_text('\n'.join(parts), encoding='utf-8')
