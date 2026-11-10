from datetime import datetime, timedelta, timezone
import pytest
from skyseed import Scene
from skyseed.scene import ScenePositionSource
from skyseed.pc.physical import PhysicalLayer, NodeState, LinkCandidate
from skyseed.pc.link import LinkLayer, PeriodicLinkPlanModel
from skyseed.pc.network import NetworkLayer

TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def epoch():
    return TIME


@pytest.fixture
def network_factory():
    def make(edges=None):
        scene = Scene()
        for node in ("a", "b", "c", "d"):
            scene.create_satellite(node, position=ScenePositionSource("test/static", {}))
        scene.create_ground_station("g", latitude_deg=0, longitude_deg=0)
        scene.create_zone("z", latitude_deg=0, longitude_deg=0)
        class Positions:
            def compute_positions(self, scene, satellite_ids, timestamp):
                return {node: NodeState((6_928_137, (ord(node)-97)*10000, 0)) for node in satellite_ids}
        class Candidates:
            def __init__(self):
                self.calls = 0
            def compute_candidates(self, scene, snapshot, node_id):
                self.calls += 1
                seconds = (snapshot.timestamp - TIME).total_seconds()
                pairs = edges(seconds) if edges is not None else [("g", "a"), ("a", "b"), ("b", "c"), ("b", "d")]
                return tuple(LinkCandidate(source, target, propagation_delay_s=0.001)
                             for source, target in pairs if source == node_id)
        physical = PhysicalLayer(scene)
        physical.set_position_model(Positions)
        physical.set_candidate_model(Candidates)
        link = LinkLayer(physical)
        link.set_selection_hook(None)
        link.set_plan_model(PeriodicLinkPlanModel, model_options={
            "isl_setup_duration": timedelta(0), "ground_setup_duration": timedelta(0)})
        return NetworkLayer(link)
    return make
