from examples.Sky03_s3_ospf.ospf_model import OspfRoutePlanningModel
"""Optional ordinary Planner annotations use configuration and recorded links only."""
from dataclasses import asdict
from copy import deepcopy
import json
import re
import subprocess

from tests.unit.test_ospf_route_model import scene, link, configured
from tests.tools.test_presentation_replay import _node
from tools.planner._ospf import _prepare_ospf, _ospf_frame, _ospf_metadata
from tools.planner._ospf_display import _OSPF_SCRIPT


def test_configured_membership_survives_outages_and_gs_stays_terminal():
    _, (a,b,c), (g,) = scene(3,1)
    edges = [link(a,b),link(b,c),link(g,b)]
    nodes = [{'node_id':n.value,'kind':n.entity_kind} for n in (a,b,c,g)]
    desc = {'route':{'implementation':'examples.Sky03_s3_ospf.ospf_model.OspfRoutePlanningModel',
        'configuration':{'routing_semantics':'ospf','interfaces':[asdict(configured(e,area)) for e,area in zip(edges,[0,1,2])],
                         'default_area':None,'cost_metric':'delay_us'}}}
    # Match captured provenance's enum encoding.
    for row in desc['route']['configuration']['interfaces']:
        row['connection_id']['kind'] = row['connection_id']['kind'].value
    prepared = _prepare_ospf(desc,nodes)
    metadata = _ospf_metadata(prepared)
    assert metadata['configured_memberships'] == [[0,[0]],[1,[0,1,2]],[2,[1]]]
    # canonical GS endpoint is first; both costs must retain that direction.
    frame = _ospf_frame(prepared,[['inter-satellite',0,1,100,5,0,100,9,0],
                                 ['ground-station-access',3,1,100,7,0,100,2,0]])
    assert frame['abrs'] == [1]  # backbone-only a is not an ABR
    assert frame['memberships'] == [[0,[0],[0]],[1,[0,1,2],[0,2]],[2,[1],[]]]
    assert frame['links'][1][2:5] == [2,7,2]
    lost = _ospf_frame(prepared,[])
    assert lost['abrs'] == [1]
    assert all(not row[2] for row in lost['memberships'])
    assert 3 not in dict(metadata['configured_memberships'])
    assert _prepare_ospf({},nodes) is None
    unknown = deepcopy(desc);unknown['route']['implementation'] = 'custom.OspfModel'
    assert _prepare_ospf(unknown,nodes) == prepared
    unknown['route']['configuration']['routing_semantics'] = 'custom'
    assert _prepare_ospf(unknown,nodes) is None
    incomplete = deepcopy(desc);del incomplete['route']['configuration']['interfaces']
    assert _prepare_ospf(incomplete,nodes) is None


def test_nonvisual_area_roles_and_selected_path_cost_follow_recorded_directions():
    script = 'const assert=require("node:assert/strict");\n'+_OSPF_SCRIPT+r'''
const view=plannerOspfIndex({links:[[0,1,0,3,7,'inter-satellite'],[1,2,[0,1],5,11,'ground-station-access']],memberships:[[0,[0],[0]],[1,[0,1],[0,1]]],abrs:[1]});
assert.equal(plannerOspfRole('satellite',[0]),'Backbone-only router');
assert.equal(plannerOspfRole('satellite',[0,1]),'ABR');
assert.match(plannerOspfRole('ground-station',[]),/terminal/);
assert.equal(plannerOspfPath([0,1,2],view).total,8);
assert.equal(plannerOspfPath([2,1,0],view).total,18);
assert.deepEqual(plannerOspfPath([0,1,2],view).segments.map(s=>s.area),[0,[0,1]]);
assert.equal(plannerOspfPath([0,2],view),null); // no alternative path search
assert.equal(plannerOspfIndex(undefined),null);
assert.notEqual(plannerOspfColor(0),plannerOspfColor(1));
const candidate={a:0,b:1,dash:[3,5],color:'candidate'},route={a:1,b:0,route:true,color:'route'};
const displayed=plannerOspfEdges([candidate,route],view);
assert.equal(displayed.length,3);assert.equal(displayed[0].color,plannerOspfColor(0));
assert.equal(displayed[1],candidate);assert.equal(displayed[2],route);
assert.equal(plannerOspfEdges([{a:0,b:1,color:'operational'}],view).length,1);
class Element{constructor(){this.children=[]}append(...v){this.children.push(...v)}}
const document={createElement(){return new Element()}},root=new Element();
appendOspfRoute(root,[2,1,0],{metric:'selected path',units:'cost units',basis:'delay_us'},view,[{node_id:'a'},{node_id:'b'},{node_id:'g',display_name:'Station name'}]);
assert.match(root.children[0].textContent,/18 cost units/);
assert.match(root.children[1].children[1].textContent,/Station name → b: 11 cost units; terminal attachment; Area \{0, 1\}/);
'''
    result = subprocess.run([_node(),'-e',script],capture_output=True,text=True)
    assert result.returncode == 0,result.stderr


def test_ordinary_streamed_tool_adds_ospf_without_running_models(tmp_path, monkeypatch):
    from examples.Sky00_tiny_network import tiny_network as tiny
    from skyseed_v2 import simulator
    from skyseed_v2.experiment.output.provenance import _describe_simulator
    from tests._support import drain_simulator
    from tools.planner import projection
    from tools.planner.inspection import write_planner_history_tool

    original = tiny.create_simulator(tiny.build_emulator())
    definition = original.get_definition()
    original.get_planner().shutdown()
    model = OspfRoutePlanningModel(default_area=0, gateway_candidate_count=1)
    simulation = simulator.create_default_simulator(
        definition, configuration=tiny.simulation_configuration(), route_model=model)
    descriptors = _describe_simulator(simulation)
    history = tmp_path/'results'
    drain_simulator(simulation, evidence_path=history)
    station, = definition.static_input.list_ground_stations()
    inputs = tmp_path/'inputs'; inputs.mkdir()
    (inputs/'provenance.json').write_text(json.dumps({
        'scenario_parameters':{'node_labels':{station.node_id.value:'Known station'}}}))
    calls = []
    prepare = projection._prepare_ospf
    def counted(*args):
        calls.append(1)
        return prepare(*args)
    def forbidden(*args, **kwargs):
        raise AssertionError('detached Tool invoked a Model')
    monkeypatch.setattr(projection, '_prepare_ospf', counted)
    monkeypatch.setattr(type(model), 'plan_route_outcomes', forbidden)
    output = tmp_path/'planner'
    result = write_planner_history_tool(history/'planner/planning.jsonl.gz', output,
        physical_frames_path=history/'physical-world/physical-frames.jsonl', model_descriptors=descriptors)
    assert result['status'] == 'PASS' and calls == [1]
    data = json.loads((output/'planner-inspection.json').read_text())
    assert data['ospf']['basis'] == 'unit'
    nodes = data['spatial_index']['nodes']
    assert next(n for n in nodes if n['kind'] == 'ground-station')['display_name'] == 'Known station'
    for path in sorted((output/'data').glob('frame-*.js')):
        frame = json.loads(path.read_text().split('=',1)[1].rstrip(';\n'))
        assert frame['ospf']['links']
        assert not frame['ospf']['abrs']  # Area 0 alone is not an ABR.
        assert all(nodes[n]['kind'] == 'satellite' for n, _, _ in frame['ospf']['memberships'])
    page = (output/'index.html').read_text()
    assert 'id="ospf-areas"' in page
    for script in re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>', page, re.S):
        result = subprocess.run([_node(), '-e', 'new (require("node:vm").Script)(require("node:fs").readFileSync(0,"utf8"))'],
                                input=script, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    # The same ordinary history API omits layout claims without known metadata.
    plain = tmp_path/'plain'
    write_planner_history_tool(history/'planner/planning.jsonl.gz', plain,
        physical_frames_path=history/'physical-world/physical-frames.jsonl')
    assert 'ospf' not in json.loads((plain/'planner-inspection.json').read_text())
    assert 'id="ospf-areas"' not in (plain/'index.html').read_text()
