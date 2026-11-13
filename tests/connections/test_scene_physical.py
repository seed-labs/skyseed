"""Real Scene persistence, registered model state and physical handoffs."""
from datetime import datetime, timezone, timedelta
import pytest
from skyseed import Scene
from skyseed.scene import ScenePositionSource
from skyseed.pc.physical import PhysicalLayer, NodeState, LinkCandidate, SGP4PositionModel, tle_position_source

TIME = datetime(2026,1,1,tzinfo=timezone.utc)


class Replay:
    def __init__(self, *, speed=100):
        self.speed = speed
        self.calls = []

    def compute_positions(self, scene, satellite_ids, timestamp):
        self.calls.append((tuple(satellite_ids),timestamp))
        result = {}
        for identity in satellite_ids:
            samples = scene.get_node(identity).position_source.data
            elapsed = (timestamp-datetime.fromisoformat(samples['epoch'])).total_seconds()
            # A simple linear ECEF trajectory represents an external replay.
            result[identity] = NodeState((6928137, self.speed*elapsed, 0), (0,self.speed,0),
                                          {'source':samples['file'], 'query':len(self.calls)})
        assert self.layer.scene is scene
        return result


class Doppler:
    def __init__(self, *, carrier_hz):
        self.carrier_hz = carrier_hz

    def compute_candidates(self, scene, snapshot, node_id):
        import numpy as np
        values = []
        for peer in snapshot.states:
            if peer == node_id:
                continue
            first, second = snapshot[node_id], snapshot[peer]
            if first.velocity_m_s is None or second.velocity_m_s is None:
                raise ValueError('Doppler requires ECEF velocity')
            delta = np.array(second.position_m)-first.position_m
            speed = np.array(second.velocity_m_s)-first.velocity_m_s
            shift = -self.carrier_hz/299792458*np.dot(delta,speed)/np.linalg.norm(delta)
            values.append(LinkCandidate(node_id,peer,data={'doppler_hz':float(shift),
                                                         'origin':dict(first.data)}))
        return values


def replay_scene():
    scene = Scene()
    scene.create_satellite('sat', position=ScenePositionSource('research/replay',
        {'epoch':TIME.isoformat(),'file':'external-track.csv'}))
    scene.create_ground_station('ground',latitude_deg=0,longitude_deg=0)
    return scene


def test_scene_save_load_model_reuse_and_fields_reach_candidates(tmp_path):
    source = tmp_path/'replay.scene'
    replay_scene().dump(source)
    scene = Scene.load(source)
    physical = PhysicalLayer(scene)
    physical.set_position_model(Replay, model_options={'speed':200})
    physical.set_candidate_model(Doppler, model_options={'carrier_hz':1e9})
    model = physical.position_model
    snapshot = physical.snapshot_at(TIME+timedelta(seconds=10))
    assert snapshot['sat'].position_m == (6928137,2000,0)
    candidate = physical.candidates(snapshot, ['sat'])['sat'][0]
    assert candidate.data['origin']['source'] == 'external-track.csv'
    assert candidate.data['doppler_hz'] < 0
    assert candidate.propagation_delay_s is None  # Missing is not a fabricated zero.
    physical.candidates(snapshot, ['ground'])
    assert len(model.calls) == 1  # Reusing a snapshot does not rerun propagation.
    physical.snapshot_at(TIME+timedelta(seconds=20), ['sat'])
    assert physical.position_model is model
    assert model.calls[-1][0] == ('sat',)
    assert len(model.calls) == 2
    other = PhysicalLayer(scene)
    other.set_position_model(Replay)
    assert other.position_model is not model
    assert other.position_model.calls == []
    with pytest.raises(ValueError, match='different Scene'):
        PhysicalLayer(replay_scene()).candidates(snapshot)


def test_empty_and_ground_only_queries_do_not_invoke_satellite_model():
    physical = PhysicalLayer(replay_scene())
    physical.set_position_model(Replay)
    assert physical.snapshot_at(TIME, []).states == {}
    assert tuple(physical.snapshot_at(TIME, ['ground']).states) == ('ground',)
    assert physical.position_model.calls == []


@pytest.mark.parametrize('slot', ['position', 'candidate'])
def test_required_model_rejects_none_and_keeps_working(tiny_scene, slot):
    physical = PhysicalLayer(tiny_scene)
    original = getattr(physical, f'{slot}_model')
    setter = getattr(physical, f'set_{slot}_model')

    with pytest.raises(TypeError, match='model class'):
        setter(None)

    assert getattr(physical, f'{slot}_model') is original
    snapshot = physical.snapshot_at(TIME)
    assert set(snapshot.states) == set(tiny_scene.get_node_ids())
    assert set(physical.candidates(snapshot)) == set(snapshot.states)


def test_missing_velocity_is_not_silently_zero():
    class PositionOnly(Replay):
        def compute_positions(self, scene, satellite_ids, timestamp):
            return {identity:NodeState((6928137,1000,0)) for identity in satellite_ids}
    physical = PhysicalLayer(replay_scene())
    physical.set_position_model(PositionOnly)
    physical.set_candidate_model(Doppler, model_options={'carrier_hz':1e9})
    with pytest.raises(ValueError, match='requires ECEF velocity'):
        physical.candidates_at(TIME)


@pytest.mark.parametrize('extra', [False, True])
def test_model_identity_mismatch_cannot_silently_drop_or_add_nodes(extra):
    class WrongIDs:
        def compute_positions(self, scene, satellite_ids, timestamp):
            return {'unexpected':NodeState((1,2,3))} if extra else {}
    physical = PhysicalLayer(replay_scene())
    physical.set_position_model(WrongIDs)
    with pytest.raises(ValueError, match='wrong satellite IDs'):
        physical.snapshot_at(TIME)


@pytest.mark.parametrize('bad_report', ['owner', 'peer', 'duplicate'])
def test_candidate_identity_handoff_rejects_unusable_reports(bad_report):
    class WrongCandidate:
        def compute_candidates(self, scene, snapshot, node_id):
            if bad_report == 'owner':
                return [LinkCandidate('ground','sat')]
            if bad_report == 'peer':
                return [LinkCandidate('sat','unknown')]
            return [LinkCandidate('sat','ground'), LinkCandidate('sat','ground')]
    physical = PhysicalLayer(replay_scene())
    physical.set_position_model(Replay)
    physical.set_candidate_model(WrongCandidate)
    with pytest.raises(ValueError, match='wrong owner|outside snapshot|duplicate'):
        physical.candidates_at(TIME, ['sat'])


def test_tle_survives_scene_persistence_and_uses_explicit_model(tmp_path):
    line1 = '1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753'
    line2 = '2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667'
    scene = Scene()
    scene.create_satellite('my-stable-id', position=tle_position_source(line1,line2,source='fixture.tle'))
    path = tmp_path/'gp.scene'
    scene.dump(path)
    loaded = Scene.load(path)
    assert loaded.get_node('my-stable-id').position_source.data == scene.get_node('my-stable-id').position_source.data
    physical = PhysicalLayer(loaded)
    physical.set_position_model(SGP4PositionModel)
    snapshot = physical.snapshot_at(datetime(2000,6,27,18,50,19,733568,tzinfo=timezone.utc))
    assert tuple(snapshot.states) == ('my-stable-id',)
    assert snapshot['my-stable-id'].velocity_m_s is not None
