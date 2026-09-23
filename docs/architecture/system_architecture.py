"""Editable source for system-architecture.svg; generate with this file's Python entry.

The explicit card layout is intentional: ownership, data and control have separate
visual encodings. Coordinates keep the complete view legible without graph auto-layout.
Only Python's standard library is required. This is one diagram, not a diagram framework.
"""
from html import escape
from pathlib import Path

W, H = 1480, 2615
parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title description">
<title id="title">SkySEED — complete system architecture</title>
<desc id="description">Authored inputs, Emulator, pure simulation, chronological production, selected BPF/Linux execution and separately owned evidence. Nested groups show ownership; blue arrows transfer values, dashed purple arrows call or control, and diamond lines hold objects.</desc>
<defs>
 <marker id="data" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#176a9d"/></marker>
 <marker id="call" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10" fill="none" stroke="#7952a3"/></marker>
 <marker id="owns" markerWidth="12" markerHeight="12" refX="0" refY="6" orient="auto"><path d="M0,6 L6,1 L12,6 L6,11 z" fill="#285a49"/></marker>
</defs>
<style>text{{font-family:Arial,Helvetica,sans-serif;fill:#183245}}.title{{font-size:23px;font-weight:bold}}.body{{font-size:18px}}.caption{{font-size:17px;fill:#456274}}.label{{font-size:16px;paint-order:stroke;stroke:#f5f8fb;stroke-width:7px;stroke-linejoin:round}}</style>
<rect width="100%" height="100%" fill="#f5f8fb"/>
''']
def text(x,y,value,css='body'):
    parts.append(f'<text x="{x}" y="{y}" class="{css}">{escape(value)}</text>')
def card(x,y,w,h,title,lines,fill='#fff'):
    parts.append('<g class="card">')
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="#8099aa"/>')
    text(x+18,y+32,title,'title')
    parts.append(f'<path d="M{x+18},{y+47} H{x+w-18}" stroke="#c4d0d9"/>')
    for i,line in enumerate(lines):text(x+18,y+75+26*i,line)
    parts.append('</g>')
def group(x,y,w,h,title,subtitle):
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="#edf4f0" stroke="#285a49" stroke-width="2"/>')
    text(x+20,y+34,title,'title');text(x+20,y+60,subtitle,'caption')
def edge(points,kind='data',label=None,label_at=None):
    stroke={'data':'#176a9d','call':'#7952a3','owns':'#285a49'}[kind]
    marker=f'marker-start="url(#owns)"' if kind=='owns' else f'marker-end="url(#{kind})"'
    dash=' stroke-dasharray="8 5"' if kind=='call' else ''
    parts.append(f'<polyline points="{points}" fill="none" stroke="{stroke}" stroke-width="2.2" {marker}{dash}/>')
    if label:text(*label_at,label,'label')

text(40,48,'SkySEED  /  FROM AUTHORED SCENE TO OBSERVED NETWORK','caption')
text(40,88,'One system, explicit owners and three kinds of evidence','title')
text(40,120,'Read top to bottom. Cards select real interfaces; a card is not necessarily a process.','body')
edge('42,161 125,161','owns');text(145,167,'Holds / owns; nesting = composition','caption')
edge('630,161 706,161','call');text(720,167,'Calls / controls','caption')
edge('1080,161 1156,161');text(1170,167,'Transfers data','caption')

card(40,205,440,245,'Authored Example + configuration',[
 'Satellite input: Shells, orbit epoch / phase,',
 'ISL and GS device capabilities',
 'GS input: sites, coordinates, access limits',
 'ZoneLayoutDefinition; Model settings',
 'Timeline start / end / cadence',
 'Explicit SEED layers, ASs, IXs and peering'])
card(520,205,440,245,'SEED Emulator + SkySEED entities',[
 'Emulator → Base → SkySEED AS',
 'AS holds EmulatorShell and GS objects',
 'Shell → EmulatorOrbit → EmulatorSatellite',
 'OrbitParameters; satellite phase / capacities',
 'EmulatorGroundStation: location / capacity',
 'SEED nodes remain on this side of adapter'])
card(1000,205,440,245,'External adapter → receiver values',[
 'create_simulator_static_input(systems)',
 'copies facts into SimulatorStaticInput',
 'SimulationDefinition(static_input, timeline)',
 'SimulationTimeline: start_time, end_time,',
 'observation_interval; finite and immutable',
 'No SEED objects enter Simulator'])
edge('480,280 520,280','call')
edge('960,355 1000,355')

# Physical and planning owners remain inside Simulator, not Producer or Tools.
group(40,505,1400,580,'Simulator','_definition; owns _physical_world, _physical_link_model and _planner. Explicit create_physical_world(), create_planner(), shutdown().')
card(65,590,425,235,'PhysicalWorld',[
 '_autonomous_systems; _nodes_by_id',
 '_position_model: PositionModel',
 '_zone_layout: ZoneLayout | None',
 'ZoneLayout holds optional membership Model',
 'frame_at(timestamp); get_satellite(NodeId)',
 'Immutable identity / physical facts'])
card(528,590,425,235,'PhysicalLinkModel + query values',[
 'compute_observation(world, frame, request)',
 'PhysicalLinkRequest: potential pairs / scopes',
 'PhysicalLinkQueryResult: sparse ISL, GS,',
 'Zone candidates; optional measurements',
 'Prediction / limited search are capabilities',
 'Feasibility is not operational selection'])
card(992,590,423,235,'Planner',[
 '_current_state; acquisition reservations',
 '_parameter_memory; bounded _unpublished',
 'ISL / GS / ZoneService / Parameter / Route',
 'prime(); observe(frame, observation)',
 'flush(); finish(); get_state()',
 'Framework derives event ordering'])
card(65,870,425,185,'PhysicalPositionSnapshot',[
 'timestamp; complete NodeId → ECEF positions',
 'PositionModel computes; World validates',
 'Query result refers to this same timestamp',
 'No operational state or timeline cursor'])
card(528,870,425,185,'PlannedOperationalState',[
 'timestamp; operational_connections',
 'zone_service_assignments; route_outcomes',
 'Current state + Transition → next state',
 'Preparation memory stays with Planner'])
card(992,870,423,185,'Plan → ordered PlanTransitions',[
 'previous_plan_id; input/output fingerprints',
 'Transition: timestamp + semantic changes',
 'semantic_event_precedences: event edges',
 'Dependencies order events, not timestamps'])
edge('1220,450 1220,505')
edge('490,700 528,700')
edge('953,700 992,700')
edge('265,825 265,870')
edge('1015,825 1015,848 740,848 740,870','owns')
edge('1210,825 1210,870')
edge('400,870 400,847 560,847 560,825')

card(40,1155,440,255,'ExperimentController',[
 '_emulator; _simulator; _producer; _deployer',
 'generate() / serve(on_completed=...)',
 'continue_calculation(); status(); cancellation',
 'Shared etcd: state, requests, user values',
 'Optional presentation task pins completed',
 'facts; calculation and runtime stay separate'])
card(520,1155,440,255,'Producer · chronological work',[
 '_timeline; _previous_result; bounded queues',
 'produce(); request_extension(); close()',
 'Calls World → physical query → Planner',
 'Then compile_plan for the Compiler cutoff',
 'Retained Models; future suffix revision',
 'Seals deliveries; publishes completed range'])
card(1000,1155,440,255,'Compiler · compile_plan',[
 'CompilationResult: exact Plan lowering',
 'Inputs: Plan / PhysicalWorld / predecessor',
 'source_plan_id; output_planned_state',
 'compiled_activation_boundaries; constraints',
 'Resource / parameter / forwarding intents',
 'Predecessor indexes retain incremental work',
 'Material writer selects backend translation'])
edge('480,1260 520,1260','owns')
edge('740,1155 740,1085','call','advance one formal timestamp',(755,1125))
edge('1210,1055 1210,1155','data','Plan',(1225,1125))
edge('960,1310 1000,1310','call')
card(520,1470,920,145,'Selected backend preparation → sealed deliveries',[
 'BPF: ELF + table deltas; Linux: identity + ordered link, Zone and IPv4 route actions',
 'results/compiler/: v8 artifacts, version catalog, execution v6',
 'results/experiment/completed.json: atomic request boundary and bounded stream prefixes'])
edge('740,1410 740,1470');edge('1220,1410 1220,1470')

card(40,1680,395,255,'Deployer · SEED deployment',[
 'output; project_name',
 'prepare(emulator); build(); start()',
 'status(); cleanup()',
 'SEED build contexts and Docker Compose',
 'Container and deployment network owner',
 'Not a time-dependent calculation stage'])
edge('240,1410 240,1680','call','deploy.sh / cleanup.sh',(65,1537))
edge('480,1370 495,1370 495,1640 940,1640 940,1680','call','run.sh / runtime control',(580,1630))
card(40,1980,395,175,'deployment/ — deployed identity',[
 'Compose / build inputs; project identity',
 'Startup / teardown measurements',
 'Container incarnations handed to Runner',
 'Cleanup follows Runner resource drainage'])
edge('240,1935 240,1980')

group(470,1680,970,475,'Runner — shared BPF/Linux lifecycle','_artifact; _active_frontier; _runtime_realization; _helper. run(), pause(), resume(), cancel_runtime(), cleanup().')
text(490,1770,'The three cards below are responsibilities of Runner, not three public classes.','caption')
card(490,1800,292,205,'Initialization',[
 'prepare()',
 'Load initial artifact / identities',
 'Acquire privileged helper',
 'Open namespace FDs / sockets',
 'Bind / prepare; activate B0'])
card(804,1800,292,205,'Execution',[
 'run(); activate_next()',
 'Create resources → READY',
 'Apply selected backend work',
 'Withdraw old; expose ACTIVE',
 'Pause / material hold keep state'])
card(1118,1800,302,205,'Maintenance',[
 'append_batch(path)',
 'Freeze → PREPARING → READY',
 'Prepare sealed backend deltas',
 'Execution alone activates',
 'BPF: reclaim after readers exit'])
card(490,2040,930,90,'Runner-owned BPF or native adapter + shared Linux resources',[
 'Shared helper, netns, identities, veths, qdiscs; BPF adds maps/libbpf. Native: ordered completion, no publication.'])
edge('1050,1615 1050,1680','data','sealed immutable paths',(1065,1645))
edge('435,2090 470,2090')
edge('782,1900 804,1900')
edge('1118,1940 1096,1940','data')

card(40,2240,440,285,'Calculated evidence — results/',[
 'PhysicalWorld frames / candidates',
 'Planner history; Compiler history / materials',
 'Fixed Traffic workload is an execution input',
 'Experiment status / completed publication',
 'Tools read one completed byte prefix',
 'A published request does not terminate',
 'the retained computation process'])
card(520,2240,440,285,'Optional Tools — visualizations/',[
 'PhysicalWorld / Zone / Planner / Compiler',
 'Traffic preview / observations / Performance',
 'Relative browser assets, no Model replay',
 'Runtime reads wait for completed invocation',
 'Presentation can lag; failure preserves facts',
 'inputs/: captured sources / effective config',
 '.control/: etcd discovery / database / events'])
card(1000,2240,440,285,'Traffic + observed runs/',[
 'Tool owns persistent fixed-pair senders',
 'Runner owns network state and ACTIVE time',
 'runs/<id>/runner, traffic, performance',
 'Actual outcomes / resource measurements',
 'Replies, failures and gaps stay distinct',
 'Stop Traffic → drain Runner → Deployer',
 'Reset: new Runner invocation, same Compose'])
edge('480,2355 520,2355')
edge('1000,2355 960,2355')
edge('1230,2155 1230,2240')
edge('1430,2315 1460,2315 1460,2090 1420,2090','call')
edge('520,1520 452,1520 452,2200 255,2200 255,2240','data')
text(40,2580,'Construction is inert. Explicit operations start tasks and acquire resources; cancellation waits for their actual exit.','caption')
parts.append('</svg>\n')
Path(__file__).with_name('system-architecture.svg').write_text('\n'.join(parts))
