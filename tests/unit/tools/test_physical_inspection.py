"""The inspection output preserves real query facts, including unknown values."""
from datetime import datetime, timezone
import json

from skyseed import Scene
from skyseed.scene import ScenePositionSource
from skyseed.pc.physical import PhysicalLayer, NodeState, LinkCandidate
from tools.observation.physical import inspection


def test_fixed_ground_state_and_empty_opportunities_survive_export(tmp_path):
    scene = Scene()
    scene.create_ground_station_group('sites')
    scene.create_ground_station('g', latitude_deg=0, longitude_deg=0, group='sites')
    time = datetime(2026,1,1,tzinfo=timezone.utc)
    data = inspection.inspection_data(PhysicalLayer(scene), [time])
    inspection.write_inspection(data, tmp_path)
    saved = json.loads((tmp_path/'physical.json').read_text())
    assert saved['frames'][0]['timestamp'] == time.isoformat()
    assert saved['frames'][0]['states']['g']['position_m'] == [6378137,0,0]
    assert saved['frames'][0]['states']['g']['velocity_m_s'] == [0,0,0]
    assert saved['frames'][0]['candidates'] == {'g':[]}
    assert saved['groups'][0]['group_id'] == saved['nodes'][0]['group_id'] == 'sites'
    assert saved['nodes'][0]['position_source']['kind'] == 'geodetic/v1'


def test_custom_fields_and_unknown_values_survive_tool(tmp_path):
    class PositionOnly:
        def compute_positions(self, scene, satellite_ids, timestamp):
            return {identity:NodeState((6928137,0,0),data={'source':'recorded'}) for identity in satellite_ids}
    class OpportunityOnly:
        def compute_candidates(self, scene, snapshot, node_id):
            return [LinkCandidate(node_id,peer,data={'measured':True})
                    for peer in snapshot.states if peer!=node_id]
    scene = Scene()
    scene.create_satellite('s',position=ScenePositionSource('test/position'))
    scene.create_ground_station('g',latitude_deg=0,longitude_deg=0)
    physical = PhysicalLayer(scene)
    physical.set_position_model(PositionOnly)
    physical.set_candidate_model(OpportunityOnly)
    data = inspection.inspection_data(physical,[datetime(2026,1,1,tzinfo=timezone.utc)])
    inspection.write_inspection(data,tmp_path)
    frame = json.loads((tmp_path/'physical.json').read_text())['frames'][0]
    assert frame['states']['s']['velocity_m_s'] is None
    assert frame['states']['s']['data'] == {'source':'recorded'}
    assert frame['candidates']['s'][0]['propagation_delay_s'] is None
    assert frame['candidates']['s'][0]['data'] == {'measured':True}
